"""Explainable, conservative travel-footage classification primitives."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from facut.core.models import MediaTechnicalInfo


_TOKENS: dict[str, tuple[str, ...]] = {
    "city": ("city", "城市", "街道", "street", "downtown"),
    "attraction": ("景点", "museum", "博物馆", "天文馆", "temple", "古城"),
    "transport": ("airport", "机场", "train", "高铁", "地铁", "drive", "自驾"),
    "hotel": ("hotel", "酒店", "民宿", "room"),
    "food": ("food", "美食", "餐厅", "restaurant", "早餐", "晚餐"),
    "snow-sports": ("滑雪", "ski", "snowboard", "雪场"),
    "aerial": ("drone", "航拍", "无人机", "dji mavic"),
    "timelapse": ("timelapse", "time-lapse", "延时"),
    "a-roll": ("口播", "talking", "interview", "旁白"),
    "detail": ("detail", "细节", "特写", "closeup", "close-up"),
}


def analyze_travel_metadata(
    path: str | Path, technical: MediaTechnicalInfo
) -> dict[str, Any]:
    """Return evidence-backed candidates without claiming visual recognition."""

    source = Path(path).expanduser().resolve()
    searchable = " ".join((source.name, *source.parts[-4:])).casefold()
    searchable = searchable.replace("_", " ").replace("-", " ")
    candidates: list[dict[str, Any]] = []
    for label, tokens in _TOKENS.items():
        matched = sorted({token for token in tokens if token.casefold() in searchable})
        if matched:
            candidates.append(
                {
                    "dimension": "travel_category",
                    "label": label,
                    "confidence": 0.68,
                    "evidence": [{"type": "path_token", "value": item} for item in matched],
                }
            )

    device = _device_candidate(technical)
    if device:
        candidates.append(device)
    daypart = _daypart_candidate(technical.creation_time, technical.timezone_offset)
    if daypart:
        candidates.append(daypart)
    if technical.dynamic_range != "unknown":
        candidates.append(
            {
                "dimension": "dynamic_range",
                "label": technical.dynamic_range,
                "confidence": 0.98,
                "evidence": [
                    {"type": "ffprobe", "field": "color_transfer", "value": technical.color_transfer}
                ],
            }
        )
    return {
        "source": str(source),
        "method": "metadata-and-path-rules-v1",
        "candidates": candidates,
        "visual_semantics": {
            "status": "plugin_required",
            "limitation": "Scene meaning, people, actions, reactions and shot quality require a configured vision analyzer.",
        },
        "uncertainties": [
            "Path-token labels describe user organization and are not proof of frame contents."
        ],
    }


def _device_candidate(technical: MediaTechnicalInfo) -> dict[str, Any] | None:
    description = " ".join(
        item for item in (technical.camera_make, technical.camera_model) if item
    )
    normalized = description.casefold()
    if not normalized:
        return None
    if any(token in normalized for token in ("mavic", "phantom", "inspire")):
        label, confidence = "drone", 0.94
    elif any(token in normalized for token in ("gopro", "insta360", "osmo action")):
        label, confidence = "action-camera", 0.94
    elif any(token in normalized for token in ("iphone", "pixel", "huawei", "xiaomi", "oppo", "vivo")):
        label, confidence = "phone", 0.90
    else:
        label, confidence = "camera", 0.72
    return {
        "dimension": "capture_device",
        "label": label,
        "confidence": confidence,
        "evidence": [{"type": "camera_metadata", "value": description}],
    }


def _daypart_candidate(
    creation_time: str | None, timezone_offset: str | None
) -> dict[str, Any] | None:
    if not creation_time:
        return None
    try:
        parsed = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    hour = parsed.hour
    if 5 <= hour < 8:
        label = "sunrise-window"
    elif 8 <= hour < 17:
        label = "day"
    elif 17 <= hour < 20:
        label = "sunset-window"
    else:
        label = "night"
    reliable_timezone = parsed.tzinfo is not None or timezone_offset is not None
    return {
        "dimension": "daypart",
        "label": label,
        "confidence": 0.86 if reliable_timezone else 0.52,
        "evidence": [
            {
                "type": "creation_time",
                "value": creation_time,
                "timezone_offset": timezone_offset,
            }
        ],
        "warning": None if reliable_timezone else "Timezone is missing; local daypart may be wrong.",
    }
