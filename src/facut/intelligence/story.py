"""Inspectable travel-story candidate generation and deterministic application."""

from __future__ import annotations

from typing import Any

from facut.core.models import Clip, ProjectDocument, Track, TrackType
from facut.core.project_manager import ProjectManager
from facut.core.sequences import snapshot_sequence


_ARCS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "travel-documentary": [
        ("opening", ("aerial", "city", "attraction", "sunrise-window")),
        ("departure", ("transport", "airport", "train", "出发")),
        ("arrival", ("抵达", "hotel", "city")),
        ("exploration", ("attraction", "food", "detail", "street")),
        ("climax", ("reaction", "sunset-window", "aerial", "snow-sports")),
        ("farewell", ("night", "sunset-window", "告别", "family")),
    ],
    "immersive-vlog": [
        ("hook", ("reaction", "a-roll", "高潮")),
        ("setup", ("departure", "transport", "hotel")),
        ("experience", ("food", "attraction", "detail", "snow-sports")),
        ("reflection", ("a-roll", "sunset-window", "night")),
    ],
    "cinematic-travel": [
        ("visual-hook", ("aerial", "sunrise-window", "city")),
        ("motion", ("transport", "action-camera", "snow-sports")),
        ("human-detail", ("detail", "reaction", "food", "family")),
        ("release", ("sunset-window", "night", "aerial")),
    ],
}


def _matches(observation: dict[str, Any], terms: tuple[str, ...]) -> bool:
    text = " ".join(
        [
            str(observation.get("text", "")),
            *[str(value) for value in observation.get("tags", [])],
        ]
    ).casefold()
    return any(term.casefold() in text for term in terms)


def build_story_plan(
    document: ProjectDocument,
    semantic_index: dict[str, Any],
    *,
    style: str = "travel-documentary",
    target_duration: float = 600.0,
) -> dict[str, Any]:
    if style not in _ARCS:
        raise ValueError(f'Unknown story style "{style}".')
    observations = semantic_index.get("observations", [])
    selected_ids: set[str] = set()
    segments: list[dict[str, Any]] = []
    stages = _ARCS[style]
    budget = max(2.0, target_duration / max(1, len(stages)))
    cursor = 0.0
    for stage, terms in stages:
        candidates = [item for item in observations if _matches(item, terms)]
        candidates.sort(
            key=lambda item: (
                -float(item.get("quality_score") or 50),
                -float(item.get("confidence", 0.5)),
            )
        )
        used = 0.0
        for item in candidates:
            key = str(item.get("id"))
            if key in selected_ids or used >= budget:
                continue
            available = float(item["end"]) - float(item["start"])
            duration = min(available, max(2.0, min(8.0, budget - used)))
            if duration <= 0:
                continue
            segments.append(
                {
                    "stage": stage,
                    "media_id": item["media_id"],
                    "source_in": float(item["start"]),
                    "source_out": float(item["start"]) + duration,
                    "timeline_start": cursor,
                    "duration": duration,
                    "confidence": item.get("confidence", 0.5),
                    "evidence": item.get("evidence", []),
                }
            )
            selected_ids.add(key)
            used += duration
            cursor += duration
    warnings: list[str] = []
    missing_stages = [stage for stage, _ in stages if not any(s["stage"] == stage for s in segments)]
    if missing_stages:
        warnings.append("No evidence-backed material found for stages: " + ", ".join(missing_stages))
    if not segments:
        warnings.append("The semantic index has no matching observations; no timeline is generated.")
    return {
        "version": "1.0",
        "style": style,
        "target_duration": target_duration,
        "estimated_duration": cursor,
        "segments": segments,
        "missing_stages": missing_stages,
        "warnings": warnings,
        "apply_contract": "Creates a named sequence and makes it the working timeline only with apply=true.",
    }


def apply_story_plan(
    manager: ProjectManager, plan: dict[str, Any], *, sequence: str
) -> ProjectDocument:
    if not plan.get("segments"):
        raise ValueError("Story plan has no segments to apply.")

    def operation(document: ProjectDocument) -> None:
        clips = [
            Clip(
                media_id=item["media_id"],
                track_id="V1",
                timeline_start=item["timeline_start"],
                source_in=item["source_in"],
                source_out=item["source_out"],
                metadata={
                    "story_stage": item["stage"],
                    "confidence": item["confidence"],
                    "evidence": item["evidence"],
                },
            )
            for item in plan["segments"]
        ]
        document.tracks = [Track(id="V1", name="Story V1", type=TrackType.VIDEO, clips=clips)]
        document.transitions = []
        document.subtitle_cues = []
        document.text_overlays = []
        document.markers = []
        document.recompute_duration()
        snapshot_sequence(document, sequence)

    _, state = manager.mutate(
        "story.apply",
        f"Applied {plan['style']} story plan to sequence {sequence}",
        operation,
        command={"style": plan["style"], "sequence": sequence},
    )
    return state
