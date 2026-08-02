"""Evidence-backed B-roll coverage diagnostics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from facut.core.models import ProjectDocument, TrackType


def diagnose_broll(
    document: ProjectDocument,
    semantic_index: dict[str, Any] | None = None,
    *,
    maximum_aroll_seconds: float = 8.0,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    video_tracks = sorted(
        [track for track in document.tracks if track.type in {TrackType.VIDEO, TrackType.IMAGE}],
        key=lambda item: item.order,
    )
    clips = [clip for track in video_tracks for clip in track.clips if clip.enabled]
    usage = Counter(clip.media_id for clip in clips)
    for media_id, count in usage.items():
        if count > 1:
            issues.append(
                {
                    "code": "REPEATED_SOURCE",
                    "severity": "warning",
                    "message": f"Media {media_id} is used {count} times.",
                    "evidence": {"media_id": media_id, "use_count": count},
                    "suggestion": "Confirm repetition is intentional or replace one use with a related detail/reaction shot.",
                }
            )
    if video_tracks:
        primary = video_tracks[0]
        for clip in primary.clips:
            asset = document.find_media(clip.media_id)
            travel = asset.metadata.get("analysis", {}).get("travel", {}) if asset else {}
            labels = {str(value.get("label")) for value in travel.get("candidates", [])}
            semantic_aroll = [
                item
                for item in (semantic_index or {}).get("observations", [])
                if item.get("media_id") == clip.media_id
                and float(item.get("start", 0.0)) < clip.source_out
                and float(item.get("end", clip.source_out)) > clip.source_in
                and "a-roll"
                in {
                    str(value).casefold()
                    for value in [
                        *(item.get("tags") or []),
                        item.get("label", ""),
                        item.get("category", ""),
                    ]
                }
            ]
            is_aroll = (
                "a-roll" in labels
                or clip.metadata.get("role") == "a-roll"
                or bool(semantic_aroll)
            )
            covered = any(
                other.enabled
                and other.timeline_start < clip.end
                and other.end > clip.timeline_start
                for track in video_tracks[1:]
                for other in track.clips
            )
            if is_aroll and clip.duration > maximum_aroll_seconds and not covered:
                issues.append(
                    {
                        "code": "LONG_AROLL_WITHOUT_BROLL",
                        "severity": "warning",
                        "message": f"A-roll clip {clip.id} remains uncovered for {clip.duration:.2f}s.",
                        "range": {"start": clip.timeline_start, "end": clip.end},
                        "evidence": {
                            "clip_id": clip.id,
                            "travel_labels": sorted(labels),
                            "semantic_observation_ids": [
                                item["id"] for item in semantic_aroll
                            ],
                        },
                        "suggestion": "Add two or more relevant B-roll shots while preserving useful original speech.",
                    }
                )
    observations = (semantic_index or {}).get("observations", [])
    indexed_media = {item.get("media_id") for item in observations}
    for overlay in document.text_overlays:
        words = overlay.text.casefold()
        concepts = {
            "hotel": ("酒店", "hotel"),
            "transport": ("机场", "高铁", "火车", "airport", "train"),
            "food": ("美食", "餐厅", "吃", "food", "restaurant"),
        }
        for concept, tokens in concepts.items():
            if any(token in words for token in tokens):
                relevant = [
                    item
                    for item in observations
                    if concept in " ".join(map(str, item.get("tags", []))).casefold()
                ]
                visible = {clip.media_id for clip in clips if clip.timeline_start < overlay.end and clip.end > overlay.at}
                if relevant and not (visible & {item["media_id"] for item in relevant}):
                    issues.append(
                        {
                            "code": "NARRATION_VISUAL_MISMATCH",
                            "severity": "info",
                            "message": f'Text mentions {concept}, but no indexed {concept} shot covers it.',
                            "range": {"start": overlay.at, "end": overlay.end},
                            "candidates": [item["id"] for item in relevant[:5]],
                            "suggestion": f"Review the listed {concept} observations as B-roll candidates.",
                        }
                    )
    return {
        "status": "pass" if not issues else "review_required",
        "issue_count": len(issues),
        "issues": issues,
        "checked": {
            "video_tracks": len(video_tracks),
            "clips": len(clips),
            "indexed_media": len(indexed_media),
            "maximum_aroll_seconds": maximum_aroll_seconds,
        },
        "limitations": [
            "Results are only as complete as saved travel labels and visual observations."
        ],
    }
