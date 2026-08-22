"""Validated, review-first narration plans and atomic timeline application."""

from __future__ import annotations

import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from facut.core.models import Clip, MediaAsset, ProjectDocument, Track, TrackType, new_id
from facut.core.project_manager import ProjectManager
from facut.exceptions import FacutError, InvalidArgumentError
from facut.media.importer import hash_file
from facut.media.probe import probe_media


class NarrationProviderNotConfigured(FacutError):
    """Raised when a caller requests a text provider which is not configured."""

    code = "PROVIDER_NOT_CONFIGURED"
    exit_code = 7


class NarrationPlanError(InvalidArgumentError):
    """A narration plan is structurally valid JSON but cannot be applied."""

    code = "INVALID_NARRATION_PLAN"


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NarrationLineStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class NarrationTimeRange(PlanModel):
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_order(self) -> "NarrationTimeRange":
        if self.end <= self.start:
            raise ValueError("timeline range end must be greater than start")
        return self


class NarrationCandidate(PlanModel):
    id: str
    text: str = Field(min_length=1)
    source: Literal["deterministic", "agent", "local-provider"] = "deterministic"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class NarrationPreview(PlanModel):
    id: str
    path: str
    style: str = "natural"
    take: int = Field(default=1, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class NarrationLine(PlanModel):
    id: str
    clip_id: str
    media_id: str
    timeline_range: NarrationTimeRange
    visual_summary: str = Field(min_length=1)
    suggestion: str = ""
    fact_confidence: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, Any]
    candidates: list[NarrationCandidate] = Field(min_length=1)
    selected_candidate_id: str | None = None
    draft_text: str | None = None
    voice: str | None = None
    style: str = "auto"
    previews: list[NarrationPreview] = Field(default_factory=list)
    selected_preview_id: str | None = None
    status: NarrationLineStatus = NarrationLineStatus.DRAFT

    @model_validator(mode="after")
    def validate_selections(self) -> "NarrationLine":
        candidate_ids = {item.id for item in self.candidates}
        if len(candidate_ids) != len(self.candidates):
            raise ValueError("narration candidate ids must be unique within a line")
        if self.selected_candidate_id is not None and self.selected_candidate_id not in candidate_ids:
            raise ValueError("selected_candidate_id does not identify a candidate")
        selected_candidate_id = self.selected_candidate_id or self.candidates[0].id
        selected_text = next(
            item.text for item in self.candidates if item.id == selected_candidate_id
        )
        if self.draft_text is None:
            self.draft_text = selected_text
        elif self.draft_text != selected_text:
            raise ValueError("draft_text must match the selected narration candidate")
        preview_ids = {item.id for item in self.previews}
        if len(preview_ids) != len(self.previews):
            raise ValueError("narration preview ids must be unique within a line")
        if self.selected_preview_id is not None and self.selected_preview_id not in preview_ids:
            raise ValueError("selected_preview_id does not identify a preview")
        return self

    @property
    def selected_text(self) -> str:
        candidate_id = self.selected_candidate_id or self.candidates[0].id
        return next(item.text for item in self.candidates if item.id == candidate_id)

    @property
    def selected_preview(self) -> NarrationPreview | None:
        if not self.previews:
            return None
        preview_id = self.selected_preview_id or self.previews[0].id
        return next(item for item in self.previews if item.id == preview_id)


class NarrationPlan(PlanModel):
    version: Literal["1.0"] = "1.0"
    id: str = Field(default_factory=lambda: new_id("narration_plan"))
    project_id: str
    project_revision: int = Field(ge=0)
    status: Literal["review_required", "ready", "applied"] = "review_required"
    mode: Literal["evidence-grounded-draft", "provider-generated"] = "evidence-grounded-draft"
    provider: str = "deterministic"
    style: str = "natural-vlog"
    language: str = "zh-CN"
    lines: list[NarrationLine] = Field(default_factory=list)
    fact_policy: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_line_ids(self) -> "NarrationPlan":
        ids = [line.id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("narration line ids must be unique")
        return self


def narration_plan_from_suggestions(
    document: ProjectDocument,
    suggestions: dict[str, Any],
    *,
    provider: str = "deterministic",
) -> NarrationPlan:
    """Convert legacy evidence-grounded suggestions into a strict review plan."""

    lines: list[NarrationLine] = []
    for index, item in enumerate(suggestions.get("lines", []), start=1):
        draft = str(item.get("draft_text") or "").strip()
        if not draft:
            continue
        line_id = str(item.get("id") or f"line_{index:03d}")
        confidence = float(item.get("confidence", 0.0))
        lines.append(
            NarrationLine(
                id=line_id,
                clip_id=str(item["clip_id"]),
                media_id=str(item["media_id"]),
                timeline_range=NarrationTimeRange.model_validate(item["timeline_range"]),
                visual_summary=str(item["visual_summary"]),
                suggestion=str(item.get("suggestion") or ""),
                fact_confidence=confidence,
                evidence=dict(item.get("evidence") or {}),
                candidates=[
                    NarrationCandidate(
                        id=f"{line_id}_candidate_1",
                        text=draft,
                        source="deterministic",
                        confidence=confidence,
                    )
                ],
            )
        )
    return NarrationPlan(
        project_id=document.project.id,
        project_revision=document.revision,
        mode="evidence-grounded-draft",
        provider=provider,
        style=str(suggestions.get("style") or "natural-vlog"),
        language=str(suggestions.get("language") or "zh-CN"),
        lines=lines,
        fact_policy=dict(suggestions.get("fact_policy") or {}),
        warnings=list(suggestions.get("warnings") or []),
        limitations=list(suggestions.get("limitations") or []),
    )


def load_narration_plan(path: str | Path) -> NarrationPlan:
    source = Path(path).expanduser().resolve()
    try:
        return NarrationPlan.model_validate_json(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise NarrationPlanError(f'Could not read narration plan "{source}": {exc}') from exc
    except ValueError as exc:
        raise NarrationPlanError(f'Narration plan "{source}" is invalid: {exc}') from exc


def save_narration_plan(plan: NarrationPlan, path: str | Path) -> Path:
    """Atomically write a validated narration plan."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.model_dump(mode="json")
    NarrationPlan.model_validate(payload)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def narration_plan_json_schema() -> dict[str, Any]:
    """Return the machine-readable narration plan contract."""

    return NarrationPlan.model_json_schema()


def prepare_narration_apply(
    manager: ProjectManager,
    plan_path: str | Path,
    *,
    approved_only: bool = True,
    duck_music: bool = False,
    preserve_original: bool = True,
    track_id: str = "A_NARRATION",
    allow_stale: bool = False,
) -> dict[str, Any]:
    """Resolve and probe narration WAVs before entering the project transaction."""

    source = Path(plan_path).expanduser().resolve()
    plan = load_narration_plan(source)
    document = manager.require_document()
    if plan.project_id != document.project.id:
        raise NarrationPlanError(
            "Narration plan belongs to a different project.",
            suggestion="Regenerate the plan from the current project snapshot.",
        )
    if plan.project_revision != document.revision and not allow_stale:
        raise NarrationPlanError(
            f"Narration plan targets revision {plan.project_revision}, but the project is at revision {document.revision}.",
            suggestion="Regenerate the narration plan, or explicitly allow a stale plan after reviewing every range.",
        )
    if not preserve_original:
        raise NarrationPlanError(
            "Segment-level replacement of original sound is not implemented safely.",
            suggestion="Use preserve_original=true; adjust or mute source audio explicitly after reviewing the result.",
        )
    selected = [
        line
        for line in plan.lines
        if (line.status == NarrationLineStatus.APPROVED if approved_only else line.status not in {NarrationLineStatus.REJECTED, NarrationLineStatus.APPLIED})
    ]
    if not selected:
        raise NarrationPlanError(
            "No narration lines are eligible for application.",
            suggestion="Approve at least one synthesized line or omit --approved-only.",
        )
    prepared_lines: list[dict[str, Any]] = []
    prepared_media: dict[str, dict[str, Any]] = {}
    for line in selected:
        preview = line.selected_preview
        if preview is None:
            raise NarrationPlanError(
                f'Narration line "{line.id}" has no selected synthesized preview.',
                suggestion="Synthesize and select a preview before applying the plan.",
            )
        audio_path = Path(preview.path).expanduser()
        if not audio_path.is_absolute():
            audio_path = source.parent / audio_path
        audio_path = audio_path.resolve()
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise NarrationPlanError(f'Narration preview "{audio_path}" is missing or empty.')
        digest = hash_file(audio_path)
        if preview.sha256 is not None and preview.sha256 != digest:
            raise NarrationPlanError(
                f'Narration preview "{audio_path}" no longer matches its recorded SHA-256.'
            )
        technical = probe_media(audio_path, include_keyframes=False)
        if not technical.audio_codec or not technical.duration:
            raise NarrationPlanError(f'Narration preview "{audio_path}" has no usable audio stream.')
        media_id = f"media_{digest[:16].upper()}"
        asset = MediaAsset(
            id=media_id,
            kind="audio",
            path=manager.store_path(audio_path),
            original_name=audio_path.name,
            size=audio_path.stat().st_size,
            sha256=digest,
            technical=technical,
            metadata={"role": "narration", "narration_plan_id": plan.id},
        )
        prepared_media[media_id] = asset.model_dump(mode="json")
        prepared_lines.append(
            {
                "line_id": line.id,
                "media_id": media_id,
                "at": line.timeline_range.start,
                "source_out": technical.duration,
                "planned_end": line.timeline_range.end,
                "text": line.selected_text,
                "voice": line.voice,
                "style": preview.style,
                "preview_id": preview.id,
            }
        )
    return {
        "action": "narration.apply.prepared",
        "plan_id": plan.id,
        "plan_path": manager.store_path(source),
        "track_id": track_id,
        "approved_only": approved_only,
        "duck_music": duck_music,
        "preserve_original": preserve_original,
        "allow_stale": allow_stale,
        "media": list(prepared_media.values()),
        "lines": prepared_lines,
    }


def apply_prepared_narration(document: ProjectDocument, command: dict[str, Any]) -> dict[str, Any]:
    """Apply a preflighted narration command entirely in memory."""

    track_id = str(command.get("track_id") or "A_NARRATION")
    track = document.find_track(track_id)
    if track is None:
        track = Track(
            id=track_id,
            type=TrackType.AUDIO,
            name="Narration",
            order=len(document.tracks),
            metadata={"role": "narration"},
        )
        document.tracks.append(track)
    elif track.type != TrackType.AUDIO or track.metadata.get("role") not in {None, "narration"}:
        raise NarrationPlanError(f'Track "{track_id}" is not available as a narration audio track.')
    if track.locked:
        raise NarrationPlanError(f'Narration track "{track_id}" is locked.')
    track.metadata["role"] = "narration"
    existing_media = {asset.id: asset for asset in document.media}
    for payload in command.get("media", []):
        asset = MediaAsset.model_validate(payload)
        current = existing_media.get(asset.id)
        if current is not None and current.sha256 != asset.sha256:
            raise NarrationPlanError(f'Media id collision for narration asset "{asset.id}".')
        if current is None:
            document.media.append(asset)
            existing_media[asset.id] = asset

    applied: list[str] = []
    warnings: list[str] = []
    plan_id = str(command["plan_id"])
    for item in command.get("lines", []):
        line_id = str(item["line_id"])
        if any(
            clip.metadata.get("narration_plan_id") == plan_id
            and clip.metadata.get("narration_line_id") == line_id
            for clip in track.clips
        ):
            raise NarrationPlanError(f'Narration line "{line_id}" has already been applied.')
        duration = float(item["source_out"])
        at = float(item["at"])
        planned_end = float(item["planned_end"])
        clip = Clip(
            media_id=str(item["media_id"]),
            track_id=track.id,
            timeline_start=at,
            source_in=0.0,
            source_out=duration,
            audio_fade_in=min(0.05, duration / 4),
            audio_fade_out=min(0.08, duration / 4),
            metadata={
                "role": "narration",
                "narration_plan_id": plan_id,
                "narration_line_id": line_id,
                "text": item["text"],
                "voice": item.get("voice"),
                "style": item.get("style"),
                "preview_id": item.get("preview_id"),
                "preserve_original": bool(command.get("preserve_original", True)),
            },
        )
        track.clips.append(clip)
        applied.append(line_id)
        if clip.end > planned_end + 1 / document.project.fps:
            warnings.append(
                f'Narration line "{line_id}" exceeds its planned range by {clip.end - planned_end:.3f}s.'
            )
    track.clips.sort(key=lambda clip: (clip.timeline_start, clip.id))

    ducking = []
    if command.get("duck_music"):
        music_tracks = [
            item.id
            for item in document.tracks
            if item.type == TrackType.AUDIO and item.id != track.id and item.metadata.get("role") == "music"
        ]
        if music_tracks:
            ducking = [
                {
                    "source_track": track.id,
                    "target_tracks": music_tracks,
                    "start": float(item["at"]),
                    "end": float(item["at"]) + float(item["source_out"]),
                    # Keep VLOG music present under narration. A 12 dB drop
                    # made otherwise natural voices feel pasted on top of the
                    # film; 8 dB remains intelligible without erasing ambience.
                    "reduction_db": -8.0,
                    "attack_ms": 100,
                    "release_ms": 500,
                    "narration_line_id": item["line_id"],
                }
                for item in command.get("lines", [])
            ]
            document.settings.setdefault("audio_ducking", []).extend(ducking)
        else:
            warnings.append("Music ducking was requested, but no audio track has metadata role=music.")

    application = {
        "plan_id": plan_id,
        "plan_path": command.get("plan_path"),
        "track_id": track.id,
        "line_ids": applied,
        "preserve_original": bool(command.get("preserve_original", True)),
        "ducking_regions": len(ducking),
    }
    document.settings.setdefault("narration_applications", []).append(application)
    document.recompute_duration()
    return {**application, "warnings": warnings}
