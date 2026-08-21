"""Evidence-grounded VLOG narration suggestions and deterministic draft lines."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from facut.core.models import ProjectDocument, TrackType

if TYPE_CHECKING:
    from .narration_plan import NarrationPlan


_ANGLES: tuple[tuple[set[str], str], ...] = (
    ({"transport", "airport", "train"}, "交代出发方式、抵达过程和第一印象。"),
    ({"food", "restaurant", "detail"}, "描述味道、做法或最有记忆点的细节。"),
    ({"family", "reaction", "people"}, "保留人物真实反应，补充当时的感受和关系。"),
    ({"hotel", "room"}, "说明住宿选择、位置和实际体验。"),
    ({"sunset", "scenery", "wide", "drone"}, "少讲事实，多说这一幕在旅程中的情绪作用。"),
)


def _summary(observation: dict[str, Any]) -> str:
    for key in ("caption", "text", "label", "category"):
        value = str(observation.get(key) or "").strip()
        if value:
            return value.rstrip("。.!！")
    tags = [
        str(value).strip()
        for value in (observation.get("tags") or [])
        if str(value).strip()
    ]
    return "、".join(tags[:4])


def _angle(observation: dict[str, Any]) -> str:
    tags = {
        str(value).casefold()
        for value in [
            *(observation.get("tags") or []),
            observation.get("label", ""),
            observation.get("category", ""),
            observation.get("shot_type", ""),
        ]
    }
    for expected, suggestion in _ANGLES:
        if tags & expected:
            return suggestion
    return "说明画面前后发生了什么，并补充镜头本身看不出的真实感受。"


def _draft(summary: str, *, style: str, first: bool, last: bool) -> str:
    if first:
        return f"这次的故事，就从{summary}开始。"
    if last:
        return f"最后，把{summary}留在这段记录的结尾。"
    if style == "travel-documentary":
        return f"眼前这一段是{summary}，也把这趟行程的现场状态完整地留了下来。"
    if style == "cinematic-travel":
        return f"镜头来到{summary}，旅程的节奏也在这里慢了下来。"
    return f"接下来看到的是{summary}，我们继续顺着现场往前走。"


def build_narration_plan(
    document: ProjectDocument,
    semantic_index: dict[str, Any],
    *,
    style: str = "natural-vlog",
    language: str = "zh-CN",
    max_lines: int = 12,
    minimum_confidence: float = 0.55,
) -> dict[str, Any]:
    """Create review-first narration ideas mapped to exact timeline ranges."""

    if style not in {"natural-vlog", "travel-documentary", "cinematic-travel"}:
        raise ValueError(f'Unsupported narration style "{style}".')
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1.")
    observations = list(semantic_index.get("observations", []))
    tracks = sorted(
        (
            track
            for track in document.tracks
            if track.enabled and track.type in {TrackType.VIDEO, TrackType.IMAGE}
        ),
        key=lambda item: item.order,
    )
    if not tracks:
        raise ValueError("Narration suggestions require a video or image timeline.")
    base = tracks[0]
    candidates: list[dict[str, Any]] = []
    used: set[str] = set()
    for clip in sorted((item for item in base.clips if item.enabled), key=lambda item: item.timeline_start):
        matches = sorted(
            (
                item
                for item in observations
                if item.get("media_id") == clip.media_id
                and float(item.get("confidence", 0.0)) >= minimum_confidence
                and float(item.get("start", 0.0)) < clip.source_out
                and float(item.get("end", clip.source_out)) > clip.source_in
                and _summary(item)
            ),
            key=lambda item: (-float(item.get("confidence", 0.0)), float(item.get("start", 0.0))),
        )
        if not matches:
            continue
        observation = matches[0]
        observation_id = str(observation.get("id") or "")
        if observation_id and observation_id in used:
            continue
        used.add(observation_id)
        speed = abs(clip.speed)
        source_start = max(clip.source_in, float(observation.get("start", clip.source_in)))
        source_end = min(clip.source_out, float(observation.get("end", clip.source_out)))
        if clip.speed > 0:
            timeline_start = clip.timeline_start + (source_start - clip.source_in) / speed
            timeline_end = clip.timeline_start + (source_end - clip.source_in) / speed
        else:
            timeline_start = clip.timeline_start + (clip.source_out - source_end) / speed
            timeline_end = clip.timeline_start + (clip.source_out - source_start) / speed
        candidates.append(
            {
                "clip_id": clip.id,
                "media_id": clip.media_id,
                "timeline_range": {
                    "start": round(timeline_start, 3),
                    "end": round(timeline_end, 3),
                },
                "visual_summary": _summary(observation),
                "suggestion": _angle(observation),
                "confidence": round(float(observation.get("confidence", 0.0)), 3),
                "evidence": {
                    "observation_id": observation_id or None,
                    "provider": observation.get("provider"),
                    "source_range": {"start": source_start, "end": source_end},
                    "items": observation.get("evidence", []),
                },
            }
        )
        if len(candidates) >= max_lines:
            break
    for index, item in enumerate(candidates):
        item["draft_text"] = _draft(
            item["visual_summary"],
            style=style,
            first=index == 0,
            last=index == len(candidates) - 1 and len(candidates) > 1,
        )
    low_evidence = any(item.get("provider") == "facut-metadata-v1" for item in observations)
    warnings = []
    if not candidates:
        warnings.append(
            "No sufficiently confident visual observation overlaps the active timeline; index richer observations first."
        )
    if low_evidence:
        warnings.append(
            "Filename-only metadata is not used as narration fact unless its confidence passes the requested threshold."
        )
    return {
        "version": "1.0",
        "status": "review_required",
        "mode": "evidence-grounded-draft",
        "style": style,
        "language": language,
        "line_count": len(candidates),
        "lines": candidates,
        "agent_generation_brief": {
            "instruction": (
                "Rewrite only from the supplied visual summaries and evidence. Keep a natural spoken tone, "
                "do not invent people, places, dates, prices or emotions, and preserve each timeline range."
            ),
            "output_fields": ["timeline_range", "draft_text", "confidence", "evidence"],
        },
        "warnings": warnings,
        "limitations": [
            "Draft text is deterministic and must be reviewed before adding it to the timeline.",
            "Personal-voice synthesis requires an explicitly configured offline facut-voice-provider/1.0 executable.",
            "This command does not call an online language model; an Agent may rewrite only from the supplied evidence.",
        ],
    }


def generate_narration_plan(
    document: ProjectDocument,
    semantic_index: dict[str, Any],
    *,
    style: str = "natural-vlog",
    language: str = "zh-CN",
    max_lines: int = 12,
    minimum_confidence: float = 0.55,
    provider: str = "deterministic",
) -> "NarrationPlan":
    """Build a validated plan without pretending that a text model was called.

    ``deterministic`` is always available and preserves the existing evidence-grounded
    draft behavior.  Other provider names are rejected until an actual local provider
    adapter has been configured by the caller.
    """

    from .narration_plan import NarrationProviderNotConfigured, narration_plan_from_suggestions

    if provider != "deterministic":
        raise NarrationProviderNotConfigured(
            f'Narration text provider "{provider}" is not configured.',
            suggestion=(
                "Use provider=deterministic for an evidence-grounded review draft, "
                "or configure a real local text provider before requesting it."
            ),
            details={"requested_provider": provider, "fallback_used": False},
        )
    normalized_style = "natural-vlog" if style == "weekend-vlog" else style
    suggestions = build_narration_plan(
        document,
        semantic_index,
        style=normalized_style,
        language=language,
        max_lines=max_lines,
        minimum_confidence=minimum_confidence,
    )
    if style == "weekend-vlog":
        suggestions["style"] = style
    return narration_plan_from_suggestions(document, suggestions, provider=provider)
