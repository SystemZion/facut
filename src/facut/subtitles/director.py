"""Review-first ASR captions, glossaries, and adaptive typography plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from facut.core.models import ProjectDocument, SubtitleCue, TextStyle, Track, TrackType
from facut.core.project_manager import ProjectManager
from facut.fonts import FontCatalog


class SubtitleDirectorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptWord(SubtitleDirectorModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    confidence: float = Field(default=0.5, ge=0, le=1)


class TranscriptCuePlan(SubtitleDirectorModel):
    id: str
    media_id: str
    clip_id: str
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    timeline_start: float = Field(ge=0)
    timeline_end: float = Field(gt=0)
    raw_text: str
    display_text: str
    corrected_text: str | None = None
    speaker: str | None = None
    confidence: float = Field(ge=0, le=1)
    words: list[TranscriptWord] = Field(default_factory=list)
    role: str = "dialogue-caption"
    status: Literal["draft", "approved", "rejected"] = "draft"
    warnings: list[str] = Field(default_factory=list)

    @property
    def selected_text(self) -> str:
        return self.corrected_text or self.display_text


class TranscriptPlan(SubtitleDirectorModel):
    version: Literal["1.0"] = "1.0"
    project_revision: int = Field(ge=0)
    language: str
    model: str
    word_timestamps: bool
    speaker_diarization: Literal["disabled", "provider", "unavailable"] = "disabled"
    status: Literal["review_required", "ready", "applied"] = "review_required"
    cues: list[TranscriptCuePlan]
    glossary_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


ROLE_MAP: dict[str, dict[str, str]] = {
    "opening": {"content_type": "city-location", "title_role": "science-geometric", "template": "location-reveal"},
    "setup": {"content_type": "travel-setup", "title_role": "caption-sans", "template": "documentary-lower-third"},
    "exploration": {"content_type": "humanities", "title_role": "documentary-serif", "template": "humanities-panel"},
    "change": {"content_type": "comedy", "title_role": "comedy-heavy", "template": "comedy-pop"},
    "climax": {"content_type": "cinematic", "title_role": "cinematic-light", "template": "cinematic-title"},
    "reflection": {"content_type": "family", "title_role": "friendly-rounded", "template": "warm-reflection"},
}


def _atomic_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def subtitle_root(project_dir: str | Path) -> Path:
    root = Path(project_dir) / "cache" / "subtitles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def glossary_path(project_dir: str | Path) -> Path:
    return subtitle_root(project_dir) / "glossary.json"


def load_glossary(project_dir: str | Path) -> dict[str, Any]:
    path = glossary_path(project_dir)
    if not path.is_file():
        return {"version": "1.0", "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def add_glossary_entry(project_dir: str | Path, term: str, kind: str) -> dict[str, Any]:
    normalized = term.strip()
    if not normalized:
        raise ValueError("Glossary term cannot be empty.")
    payload = load_glossary(project_dir)
    payload["entries"] = [item for item in payload["entries"] if item["term"].casefold() != normalized.casefold()]
    payload["entries"].append({"term": normalized, "type": kind})
    payload["entries"].sort(key=lambda item: item["term"].casefold())
    _atomic_json(glossary_path(project_dir), payload)
    return payload


def _wrap_chinese(text: str, maximum: int = 16) -> str:
    compact = re.sub(r"\s+", "", text.strip())
    if len(compact) <= maximum:
        return compact
    break_points = [index + 1 for index, char in enumerate(compact) if char in "，。！？；、"]
    target = min(maximum, len(compact) // 2 + len(compact) % 2)
    split = min(break_points, key=lambda value: abs(value - target)) if break_points else target
    first, second = compact[:split], compact[split:]
    if len(first) > maximum or len(second) > maximum:
        first, second = compact[:maximum], compact[maximum:]
    return first.rstrip("，、") + "\n" + second.lstrip("，、")


def _wrap_latin(text: str, maximum: int = 42) -> str:
    words = text.strip().split()
    lines = [""]
    for word in words:
        candidate = f"{lines[-1]} {word}".strip()
        if len(candidate) <= maximum or len(lines) == 2:
            lines[-1] = candidate
        else:
            lines.append(word)
    return "\n".join(lines[:2])


def format_caption(text: str, language: str, duration: float) -> tuple[str, list[str]]:
    chinese = language.casefold().startswith(("zh", "ja", "ko"))
    formatted = _wrap_chinese(text) if chinese else _wrap_latin(text)
    visible = len(re.sub(r"\s+", "", text))
    speed = visible / max(duration, 0.001)
    warnings: list[str] = []
    if duration < 0.8:
        warnings.append("SUBTITLE_TOO_SHORT")
    maximum_speed = 10 if chinese else 20
    if speed > maximum_speed:
        warnings.append("SUBTITLE_READING_SPEED_HIGH")
    if formatted.count("\n") > 1:
        warnings.append("SUBTITLE_TOO_MANY_LINES")
    if any(len(line) > (16 if chinese else 42) for line in formatted.splitlines()):
        warnings.append("SUBTITLE_LINE_TOO_LONG")
    return formatted, warnings


def build_transcript_plan(
    document: ProjectDocument,
    project_dir: str | Path,
    transcripts: dict[str, dict[str, Any]],
    *,
    language: str,
    model: str,
    word_timestamps: bool,
    speaker_diarization: str = "disabled",
) -> TranscriptPlan:
    cues: list[TranscriptCuePlan] = []
    warnings: list[str] = []
    glossary = load_glossary(project_dir)
    glossary_terms = [item["term"] for item in glossary["entries"]]
    for track in document.tracks:
        if track.type not in {TrackType.VIDEO, TrackType.AUDIO}:
            continue
        for clip in track.clips:
            if not clip.enabled or clip.speed <= 0:
                if clip.enabled and clip.speed < 0:
                    warnings.append(f"Skipped reverse clip {clip.id}; ASR timeline mapping requires forward audio.")
                continue
            transcript = transcripts.get(clip.media_id)
            if transcript is None:
                continue
            for index, segment in enumerate(transcript.get("segments", []), start=1):
                source_start = max(clip.source_in, float(segment["start"]))
                source_end = min(clip.source_out, float(segment["end"]))
                if source_end <= source_start:
                    continue
                timeline_start = clip.timeline_start + (source_start - clip.source_in) / clip.speed
                timeline_end = clip.timeline_start + (source_end - clip.source_in) / clip.speed
                raw_text = str(segment.get("text", "")).strip()
                formatted, cue_warnings = format_caption(raw_text, language, timeline_end - timeline_start)
                confidence = float(segment.get("confidence", 0.5))
                if glossary_terms and not any(term in raw_text for term in glossary_terms):
                    cue_warnings.append("GLOSSARY_TERMS_NOT_CONFIRMED")
                cue_words = [
                    TranscriptWord(
                        start=float(item["start"]),
                        end=float(item["end"]),
                        text=str(item["text"]),
                        confidence=float(item.get("confidence", confidence)),
                    )
                    for item in segment.get("words", [])
                ]
                cues.append(
                    TranscriptCuePlan(
                        id=f"caption_{clip.id}_{index:04d}",
                        media_id=clip.media_id,
                        clip_id=clip.id,
                        source_start=source_start,
                        source_end=source_end,
                        timeline_start=timeline_start,
                        timeline_end=timeline_end,
                        raw_text=raw_text,
                        display_text=formatted,
                        speaker=segment.get("speaker"),
                        confidence=confidence,
                        words=cue_words,
                        status="approved" if confidence >= 0.85 and not cue_warnings else "draft",
                        warnings=cue_warnings,
                    )
                )
    glossary_digest = hashlib.sha256(
        json.dumps(glossary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    plan = TranscriptPlan(
        project_revision=document.revision,
        language=language,
        model=model,
        word_timestamps=word_timestamps,
        speaker_diarization=speaker_diarization,
        status="ready" if cues and all(item.status == "approved" for item in cues) else "review_required",
        cues=cues,
        glossary_sha256=glossary_digest,
        warnings=warnings,
    )
    return plan


def save_transcript_plan(plan: TranscriptPlan, path: str | Path) -> Path:
    return _atomic_json(Path(path).expanduser().resolve(), plan.model_dump(mode="json"))


def load_transcript_plan(path: str | Path) -> TranscriptPlan:
    return TranscriptPlan.model_validate_json(Path(path).expanduser().resolve().read_text(encoding="utf-8-sig"))


def apply_transcript_plan(
    manager: ProjectManager,
    plan: TranscriptPlan,
    *,
    approved_only: bool = True,
    track_id: str = "S_DIALOGUE",
    font_catalog: FontCatalog | None = None,
) -> dict[str, Any]:
    if plan.project_revision != manager.require_document().revision:
        raise ValueError(
            f"Transcript plan targets revision {plan.project_revision}, but project is at {manager.require_document().revision}."
        )
    catalog = font_catalog or FontCatalog()
    font = catalog.match("caption-sans", language=plan.language)
    selected = [item for item in plan.cues if (item.status == "approved" or not approved_only) and item.status != "rejected"]
    if not selected:
        raise ValueError("Transcript plan has no eligible cues to apply.")

    def operation(document: ProjectDocument):
        track = document.find_track(track_id)
        if track is None:
            document.tracks.append(
                Track(
                    id=track_id,
                    name="Dialogue Captions",
                    type=TrackType.SUBTITLE,
                    order=max((item.order for item in document.tracks), default=-1) + 1,
                    metadata={"role": "dialogue-caption"},
                )
            )
        elif track.type != TrackType.SUBTITLE:
            raise ValueError(f'Track "{track_id}" is not a subtitle track.')
        document.subtitle_cues = [item for item in document.subtitle_cues if item.track_id != track_id]
        created = []
        for item in selected:
            display_text = item.selected_text
            if item.corrected_text:
                display_text, _ = format_caption(
                    item.corrected_text, plan.language, item.timeline_end - item.timeline_start
                )
            cue = SubtitleCue(
                id=item.id,
                track_id=track_id,
                start=item.timeline_start,
                end=item.timeline_end,
                text=display_text,
                style=TextStyle(
                    font_family=font["selected_family"],
                    font_size=52,
                    color="#FFFFFF",
                    stroke_color="#000000",
                    stroke_width=2,
                    shadow=1,
                    alignment="center",
                    safe_area=True,
                ),
                metadata={
                    "role": item.role,
                    "speaker": item.speaker,
                    "confidence": item.confidence,
                    "raw_text": item.raw_text,
                    "font_role": "caption-sans",
                },
            )
            document.subtitle_cues.append(cue)
            created.append(cue.id)
        document.subtitle_cues.sort(key=lambda item: (item.start, item.end, item.id))
        document.recompute_duration()
        return created

    created, state = manager.mutate(
        "subtitle.apply",
        f"Applied {len(selected)} reviewed dialogue captions",
        operation,
        command={"approved_only": approved_only, "track_id": track_id},
    )
    return {
        "created_cues": created,
        "track_id": track_id,
        "font": font,
        "project_revision": state.revision,
    }


def build_typography_plan(project_dir: str | Path, *, language: str = "zh-CN") -> dict[str, Any]:
    story_path = Path(project_dir) / "cache" / "vlog" / "story.plan.json"
    if not story_path.is_file():
        raise FileNotFoundError("Story plan was not found. Run `facut vlog plan` first.")
    story = json.loads(story_path.read_text(encoding="utf-8"))
    observations_path = Path(project_dir) / "cache" / "vlog" / "observations.json"
    observations = (
        json.loads(observations_path.read_text(encoding="utf-8")).get("observations", [])
        if observations_path.is_file()
        else []
    )
    observation_text = " ".join(
        " ".join(
            [
                str(item.get("summary", "")),
                *map(str, item.get("tags", [])),
                *map(str, item.get("actions", [])),
            ]
        )
        for item in observations
    ).casefold()
    catalog = FontCatalog()
    sections = []
    for stage, base_definition in ROLE_MAP.items():
        definition = dict(base_definition)
        if stage == "exploration" and any(
            token in observation_text
            for token in ("astronomy", "science", "museum", "天文", "科技", "现代建筑")
        ):
            definition.update(
                {
                    "content_type": "science-museum",
                    "title_role": "science-geometric",
                    "template": "science-location-panel",
                }
            )
        elif stage == "exploration" and any(
            token in observation_text for token in ("food", "restaurant", "美食", "餐厅", "小吃")
        ):
            definition.update(
                {
                    "content_type": "food",
                    "title_role": "food-handwritten",
                    "template": "food-label",
                }
            )
        match = catalog.match(definition["title_role"], language=language)
        sections.append(
            {
                "segment": stage,
                "content_type": definition["content_type"],
                "caption_role": "dialogue-caption",
                "title_role": definition["title_role"],
                "font_candidates": [item["family"] for item in match["candidates"]],
                "selected_font": match["selected_family"],
                "template": definition["template"],
                "animation": definition["template"],
                "reason": f'{stage} content uses logical role {definition["title_role"]}; body captions remain caption-sans.',
                "confidence": 0.9,
                "fallback": "caption-sans",
            }
        )
    payload = {
        "version": "1.0",
        "story_evidence_sha256": story["evidence_sha256"],
        "language": language,
        "body_caption": catalog.match("caption-sans", language=language),
        "sections": sections,
        "policy": "Distinctive fonts are limited to short titles and emphasis; dialogue captions remain readable and unified.",
    }
    _atomic_json(subtitle_root(project_dir) / "typography.plan.json", payload)
    return payload


def apply_typography_plan(manager: ProjectManager, plan_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(plan_path).expanduser().resolve() if plan_path else subtitle_root(manager.project_dir) / "typography.plan.json"
    if not path.is_file():
        raise FileNotFoundError("Typography plan was not found. Run `facut typography plan` first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    story_path = Path(manager.project_dir) / "cache" / "vlog" / "story.plan.json"
    if not story_path.is_file():
        raise FileNotFoundError("Current StoryGraph plan was not found.")
    current_story = json.loads(story_path.read_text(encoding="utf-8"))
    if payload.get("story_evidence_sha256") != current_story.get("evidence_sha256"):
        raise ValueError("Typography plan is stale because the StoryGraph evidence changed.")
    catalog = FontCatalog()
    for section in payload["sections"]:
        current = catalog.match(section["title_role"], language=payload["language"])
        if current["selected_family"] != section["selected_font"]:
            raise ValueError(
                f'Typography font "{section["selected_font"]}" is no longer the active match '
                f'for role {section["title_role"]}; regenerate the typography plan.'
            )
    by_segment = {item["segment"]: item for item in payload["sections"]}

    def operation(document: ProjectDocument):
        changed = []
        for overlay in document.text_overlays:
            segment = str(overlay.metadata.get("story_stage") or "")
            definition = by_segment.get(segment)
            if not definition:
                continue
            overlay.style.font_family = definition["selected_font"]
            overlay.metadata.update(
                {
                    "font_role": definition["title_role"],
                    "typography_reason": definition["reason"],
                    "typography_confidence": definition["confidence"],
                }
            )
            changed.append(overlay.id)
        document.settings["typography"] = payload
        return changed

    changed, state = manager.mutate(
        "typography.apply",
        "Applied content-adaptive typography plan",
        operation,
        command={"plan": str(path)},
    )
    return {"changed_overlays": changed, "stored_policy": True, "project_revision": state.revision}


def subtitle_director_schemas() -> dict[str, Any]:
    return {
        "transcript_plan": TranscriptPlan.model_json_schema(),
        "transcript_cue": TranscriptCuePlan.model_json_schema(),
    }
