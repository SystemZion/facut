"""Stable user-facing voice delivery presets shared with AI callers."""

from __future__ import annotations


VOICE_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "natural": {
        "name_zh": "自然版",
        "description": "松弛、真实，适合普通旅行和生活VLOG。",
        "recording_delivery": "natural",
    },
    "broadcast": {
        "name_zh": "播音版",
        "description": "清晰、稳定、有信息感，但避免过度字正腔圆。",
        "recording_delivery": "broadcast",
    },
    "chat": {
        "name_zh": "聊天版",
        "description": "像对熟人说话，允许轻微思考和口语停顿。",
        "recording_delivery": "chat",
    },
    "daily-chat": {
        "name_zh": "日常聊天版",
        "description": "更接近旅行现场随口交流，保留自然停顿、轻微语气词和不刻意组织的表达。",
        "recording_delivery": "daily-chat",
    },
    "comedy": {
        "name_zh": "搞笑版",
        "description": "轻松俏皮，有反差和笑意，但不使用夸张卡通腔。",
        "recording_delivery": "comedy",
    },
    "excited": {
        "name_zh": "激动版",
        "description": "真实兴奋、节奏稍快，适合到达和惊喜时刻。",
        "recording_delivery": "excited",
    },
}

LEGACY_VOICE_STYLES = {
    "natural-vlog",
    "warm",
    "reflective",
    "energetic",
    "documentary",
    "reference",
}


def voice_style_catalog() -> list[dict[str, str]]:
    return [{"id": key, **value} for key, value in VOICE_STYLE_PRESETS.items()]


def validate_voice_styles(values: list[str]) -> list[str]:
    allowed = set(VOICE_STYLE_PRESETS) | LEGACY_VOICE_STYLES
    normalized = [item.strip() for item in values if item.strip()]
    unknown = [item for item in normalized if item not in allowed]
    if unknown:
        raise ValueError(
            f'Unknown voice style "{unknown[0]}". Run `facut voice styles --json` to list styles.'
        )
    if not normalized:
        raise ValueError("At least one voice style is required.")
    return list(dict.fromkeys(normalized))
