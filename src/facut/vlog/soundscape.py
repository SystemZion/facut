"""Review-first VLOG soundscape analysis and deterministic plan application.

The analysis is intentionally limited to project metadata and timeline
relationships.  It never reports loudness, noise, clipping, or intelligibility
as measured unless another subsystem has supplied those measurements.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from facut.core.models import ProjectDocument, TrackType
from facut.core.project_manager import ProjectManager
from facut.core.timeline_engine import TimelineEngine
from facut.exceptions import InvalidArgumentError, ReviewRequiredError


AudioRole = Literal["dialogue", "narration", "original", "ambience", "music", "sfx"]
OperationStatus = Literal["draft", "approved", "rejected"]
_ROLES = {"dialogue", "narration", "original", "ambience", "music", "sfx"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SoundscapeOperation(_StrictModel):
    id: str = Field(default_factory=lambda: f"soundop_{uuid4().hex[:16].upper()}")
    kind: Literal[
        "clip_gain", "clip_fade", "clip_process", "duck_music", "master_loudness"
    ]
    parameters: dict[str, Any]
    status: OperationStatus = "draft"
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_basis: Literal["timeline_metadata", "measured_audio"] = "timeline_metadata"


class SoundscapeAnalysis(_StrictModel):
    version: Literal["1.0"] = "1.0"
    project_id: str
    project_revision: int = Field(ge=0)
    project_sha256: str
    signal_analysis: Literal["not_run", "provided"] = "not_run"
    roles: dict[str, list[dict[str, Any]]]
    overlaps: list[dict[str, Any]]
    warnings: list[str]


class SoundscapePlan(_StrictModel):
    version: Literal["1.0"] = "1.0"
    id: str
    project_id: str
    project_revision: int = Field(ge=0)
    project_sha256: str
    analysis_sha256: str
    style: str
    status: Literal["draft", "ready"] = "draft"
    operations: list[SoundscapeOperation]
    warnings: list[str]
    execution_status: Literal["not_applied"] = "not_applied"


def _root(project_dir: str | Path) -> Path:
    return Path(project_dir) / "cache" / "vlog" / "soundscape"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _document_sha256(document: ProjectDocument) -> str:
    return hashlib.sha256(_canonical(document.model_dump(mode="json"))).hexdigest()


def _atomic_write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def _role(track_role: Any, clip_role: Any, track_type: TrackType) -> AudioRole | None:
    for candidate in (clip_role, track_role):
        normalized = str(candidate or "").casefold()
        if normalized in _ROLES:
            return normalized  # type: ignore[return-value]
    if track_type in {TrackType.AUDIO, TrackType.VIDEO}:
        return "original"
    return None


def analyze_soundscape(
    manager: ProjectManager,
    *,
    measure: bool = False,
    ffmpeg: str | None = None,
) -> dict[str, Any]:
    """Inventory audio roles and optionally attach measured EBU R128 evidence."""

    document = manager.require_document()
    path = _root(manager.project_dir) / "analysis.json"
    if measure and path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (
            existing.get("project_sha256") == _document_sha256(document)
            and existing.get("signal_analysis") == "provided"
        ):
            cached_measurements = {
                str(clip["media_id"]): clip["measurement"]
                for clips in existing.get("roles", {}).values()
                for clip in clips
                if clip.get("measurement")
            }
            return {
                **existing,
                "measurements": cached_measurements,
                "path": str(path.resolve()),
                "cached": True,
            }
    roles: dict[str, list[dict[str, Any]]] = {role: [] for role in sorted(_ROLES)}
    warnings = [] if measure else [
        "Signal analysis was not run; loudness, clipping, wind noise, and intelligibility remain unknown."
    ]
    for track in document.tracks:
        for clip in track.clips:
            asset = document.find_media(clip.media_id)
            if asset is None or not asset.technical.audio_codec or clip.muted or clip.audio.muted:
                continue
            role = _role(track.metadata.get("role"), clip.metadata.get("role"), track.type)
            if role is None:
                warnings.append(f'Clip "{clip.id}" has audio but no supported soundscape role.')
                continue
            roles[role].append(
                {
                    "clip_id": clip.id,
                    "track_id": track.id,
                    "media_id": clip.media_id,
                    "start": clip.timeline_start,
                    "end": clip.end,
                    "gain_db": clip.audio.gain_db,
                    "fade_in": clip.audio.fade_in,
                    "fade_out": clip.audio.fade_out,
                }
            )
    overlaps: list[dict[str, Any]] = []
    speech = [*roles["dialogue"], *roles["narration"]]
    for spoken in speech:
        for music in roles["music"]:
            start = max(float(spoken["start"]), float(music["start"]))
            end = min(float(spoken["end"]), float(music["end"]))
            if end > start:
                overlaps.append(
                    {
                        "kind": "speech_music",
                        "speech_clip_id": spoken["clip_id"],
                        "speech_track_id": spoken["track_id"],
                        "music_clip_id": music["clip_id"],
                        "music_track_id": music["track_id"],
                        "start": start,
                        "end": end,
                        "measurement_required": True,
                    }
                )
    measurements: dict[str, dict[str, Any]] = {}
    if measure:
        if not ffmpeg:
            raise ValueError("Measured soundscape analysis requires an FFmpeg executable.")
        from facut.qc.detectors import check_loudness

        for role, clips in roles.items():
            for clip in clips:
                media_id = str(clip["media_id"])
                if media_id in measurements:
                    clip["measurement"] = measurements[media_id]
                    continue
                asset = document.find_media(media_id)
                if asset is None:
                    continue
                # Audio is always measured from the original asset. LRF proxy
                # audio is intentionally never trusted by the VLOG workflow.
                source = manager.resolve_path(asset.path)
                try:
                    result = check_loudness(source, ffmpeg, timeout=300.0)
                    measurement = {
                        "status": result.status.value,
                        "metrics": result.data,
                        "errors": result.errors,
                        "source_kind": "original",
                    }
                except Exception as error:
                    measurement = {
                        "status": "failed",
                        "metrics": {},
                        "errors": [str(error)],
                        "source_kind": "original",
                    }
                    warnings.append(f'Audio measurement failed for "{media_id}".')
                measurements[media_id] = measurement
                clip["measurement"] = measurement
    analysis = SoundscapeAnalysis(
        project_id=document.project.id,
        project_revision=document.revision,
        project_sha256=_document_sha256(document),
        signal_analysis="provided" if measure else "not_run",
        roles=roles,
        overlaps=overlaps,
        warnings=warnings,
    )
    _atomic_write(path, analysis.model_dump(mode="json"))
    return {
        **analysis.model_dump(mode="json"),
        "measurements": measurements,
        "path": str(path.resolve()),
        "cached": False,
    }


def plan_soundscape(
    manager: ProjectManager,
    *,
    style: str = "natural-vlog",
    target_lufs: float = -14.0,
    true_peak_db: float = -1.0,
) -> dict[str, Any]:
    """Create draft-only mix recommendations from timeline metadata."""

    existing_path = _root(manager.project_dir) / "analysis.json"
    existing = (
        json.loads(existing_path.read_text(encoding="utf-8"))
        if existing_path.is_file()
        else {}
    )
    analysis_data = (
        analyze_soundscape(manager, measure=True, ffmpeg=None)
        if existing.get("project_sha256") == _document_sha256(manager.require_document())
        and existing.get("signal_analysis") == "provided"
        else analyze_soundscape(manager)
    )
    analysis = SoundscapeAnalysis.model_validate(
        {
            key: value
            for key, value in analysis_data.items()
            if key not in {"path", "measurements", "cached"}
        }
    )
    operations: list[SoundscapeOperation] = []
    for overlap in analysis.overlaps:
        operations.append(
            SoundscapeOperation(
                kind="duck_music",
                parameters={
                    "source_track": overlap["speech_track_id"],
                    "target_tracks": [overlap["music_track_id"]],
                    "start": overlap["start"],
                    "end": overlap["end"],
                    "reduction_db": -8.0,
                    "attack_ms": 100,
                    "release_ms": 500,
                },
                reason="Speech overlaps music; propose moderate ducking, pending A/B listening.",
                confidence=0.65,
            )
        )
    operations.append(
        SoundscapeOperation(
            kind="master_loudness",
            parameters={
                "target": target_lufs,
                "true_peak": true_peak_db,
                "loudness_range": 11.0,
                "two_pass": True,
            },
            reason="Platform delivery target; approval still requires a measured final-output QC pass.",
            confidence=0.8,
        )
    )
    raw_analysis = analysis.model_dump(mode="json")
    plan = SoundscapePlan(
        id=f"soundscape-plan-{uuid4().hex[:12]}",
        project_id=analysis.project_id,
        project_revision=analysis.project_revision,
        project_sha256=analysis.project_sha256,
        analysis_sha256=hashlib.sha256(_canonical(raw_analysis)).hexdigest(),
        style=style,
        operations=operations,
        warnings=[
            *analysis.warnings,
            "All recommendations are drafts; no gain, cleanup, ducking, or loudness processing has been applied.",
        ],
    )
    path = _root(manager.project_dir) / "plans" / f"{plan.id}.json"
    _atomic_write(path, plan.model_dump(mode="json"))
    return {**plan.model_dump(mode="json"), "path": str(path.resolve())}


def apply_soundscape_plan(
    manager: ProjectManager,
    plan: str | Path | dict[str, Any],
    *,
    approved_only: bool = True,
) -> dict[str, Any]:
    """Apply explicit approvals non-destructively in one CutGraph revision."""

    prepared = prepare_soundscape_apply(
        manager, plan, approved_only=approved_only
    )

    def operation(candidate: ProjectDocument) -> list[dict[str, Any]]:
        return apply_prepared_soundscape(candidate, prepared)

    applied, state = manager.mutate(
        "vlog.soundscape.apply",
        f"Applied {len(prepared['operations'])} approved soundscape operation(s)",
        operation,
        command={
            "plan_id": prepared["plan_id"],
            "approved_only": approved_only,
            "_actor": "agent",
            "_intent": "Apply explicitly reviewed VLOG soundscape settings",
        },
    )
    return {
        "plan_id": prepared["plan_id"],
        "applied": applied,
        "execution_status": "settings_applied_render_required",
        "measured_output": False,
        "project_revision": state.revision,
        "warnings": [
            "Timeline settings were applied; audible results and delivery loudness are not claimed until render and QC."
        ],
    }


def prepare_soundscape_apply(
    manager: ProjectManager,
    plan: str | Path | dict[str, Any],
    *,
    approved_only: bool = True,
) -> dict[str, Any]:
    """Resolve approvals and stale-state guards before an atomic batch begins."""

    if isinstance(plan, dict):
        payload = {key: value for key, value in plan.items() if key != "path"}
    else:
        payload = json.loads(Path(plan).read_text(encoding="utf-8-sig"))
    item = SoundscapePlan.model_validate(payload)
    document = manager.require_document()
    analysis_path = _root(manager.project_dir) / "analysis.json"
    if not analysis_path.is_file():
        raise InvalidArgumentError("Soundscape analysis is missing.")
    analysis = SoundscapeAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
    analysis_sha = hashlib.sha256(_canonical(analysis.model_dump(mode="json"))).hexdigest()
    if (
        item.project_id != document.project.id
        or item.project_revision != document.revision
        or item.project_sha256 != _document_sha256(document)
        or item.analysis_sha256 != analysis_sha
    ):
        raise InvalidArgumentError(
            "Soundscape plan is stale for the current project or analysis.",
            suggestion="Analyze and plan the soundscape again before applying it.",
        )
    selected = [
        operation
        for operation in item.operations
        if operation.status == "approved"
        or (not approved_only and operation.status != "rejected")
    ]
    if not selected:
        raise ReviewRequiredError(
            "No soundscape operations are approved.",
            suggestion="A/B preview the draft recommendations and approve specific operations.",
        )

    return {
        "action": "vlog.soundscape.apply.prepared",
        "plan_id": item.id,
        "style": item.style,
        "approved_only": approved_only,
        "operations": [entry.model_dump(mode="json") for entry in selected],
    }


def apply_prepared_soundscape(
    candidate: ProjectDocument, command: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply validated soundscape settings to one in-memory transaction state."""

    if any(
        entry.get("plan_id") == command["plan_id"]
        for entry in candidate.settings.get("soundscape_applications", [])
    ):
        raise InvalidArgumentError(
            f'Soundscape plan "{command["plan_id"]}" was already applied.'
        )
    timeline = TimelineEngine(candidate)
    adjustments = [
        SoundscapeOperation.model_validate(item) for item in command["operations"]
    ]
    applied: list[dict[str, Any]] = []
    for adjustment in adjustments:
        result = _apply_operation(candidate, timeline, adjustment)
        applied.append(
            {"id": adjustment.id, "kind": adjustment.kind, "result": result}
        )
    candidate.settings.setdefault("soundscape_applications", []).append(
        {
            "plan_id": command["plan_id"],
            "style": command["style"],
            "approved_only": command["approved_only"],
            "operation_ids": [entry.id for entry in adjustments],
            "measurement_policy": "Final loudness and true peak require rendered-output QC.",
        }
    )
    return applied


def _apply_operation(
    document: ProjectDocument,
    timeline: TimelineEngine,
    operation: SoundscapeOperation,
) -> dict[str, Any]:
    parameters = dict(operation.parameters)
    if operation.kind == "clip_gain":
        clip = timeline.set_audio_volume(str(parameters["clip_id"]), float(parameters["db"]))
        return {"clip_id": clip.id, "gain_db": clip.audio.gain_db}
    if operation.kind == "clip_fade":
        clip = timeline.set_audio_fades(
            str(parameters["clip_id"]),
            fade_in=parameters.get("fade_in"),
            fade_out=parameters.get("fade_out"),
        )
        return {
            "clip_id": clip.id,
            "fade_in": clip.audio.fade_in,
            "fade_out": clip.audio.fade_out,
        }
    if operation.kind == "clip_process":
        clip_id = str(parameters.pop("clip_id"))
        clip = timeline.configure_audio(clip_id, **parameters)
        return {"clip_id": clip.id, "audio": clip.audio.model_dump(mode="json")}
    if operation.kind == "master_loudness":
        return timeline.set_master_loudness(**parameters)
    if operation.kind == "duck_music":
        source_track = str(parameters["source_track"])
        target_tracks = [str(value) for value in parameters["target_tracks"]]
        if document.find_track(source_track) is None or any(
            document.find_track(track_id) is None for track_id in target_tracks
        ):
            raise InvalidArgumentError("Soundscape ducking references an unknown track.")
        start = float(parameters["start"])
        end = float(parameters["end"])
        if end <= start:
            raise InvalidArgumentError("Soundscape ducking end must be greater than start.")
        region = {
            "source_track": source_track,
            "target_tracks": target_tracks,
            "start": start,
            "end": end,
            "reduction_db": float(parameters.get("reduction_db", -8.0)),
            "attack_ms": float(parameters.get("attack_ms", 100.0)),
            "release_ms": float(parameters.get("release_ms", 500.0)),
            "source": "soundscape-approved-plan",
        }
        document.settings.setdefault("audio_ducking", []).append(region)
        return region
    raise InvalidArgumentError(f'Unsupported soundscape operation "{operation.kind}".')
