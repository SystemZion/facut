"""Validated delivery presets and source-frame-rate resolution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


RENDER_PRESETS: dict[str, dict[str, Any]] = {
    "youtube-1080p": {
        "platform": "youtube",
        "width": 1920,
        "height": 1080,
        "fps": None,
        "bitrate": "8M",
        "high_fps_bitrate": "12M",
        "audio_bitrate": "384k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "YouTube recommended upload encoding settings",
    },
    "youtube-4k": {
        "platform": "youtube",
        "width": 3840,
        "height": 2160,
        "fps": None,
        "bitrate": "45M",
        "high_fps_bitrate": "68M",
        "audio_bitrate": "384k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "YouTube recommended upload encoding settings",
    },
    "youtube-4k-sdr": {
        "inherits": "youtube-4k",
        "description": "4K SDR, source frame rate, BT.709, AAC 48 kHz, Fast Start.",
    },
    "bilibili-1080p": {
        "platform": "bilibili",
        "width": 1920,
        "height": 1080,
        "fps": None,
        "bitrate": "12M",
        "high_fps_bitrate": "18M",
        "audio_bitrate": "320k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "FACUT creator workflow default; not represented as an official maximum.",
    },
    "bilibili-4k": {
        "platform": "bilibili",
        "width": 3840,
        "height": 2160,
        "fps": None,
        "bitrate": "45M",
        "high_fps_bitrate": "60M",
        "audio_bitrate": "320k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "FACUT creator workflow default; not represented as an official maximum.",
    },
    "shorts-9x16": {
        "platform": "youtube-shorts",
        "width": 2160,
        "height": 3840,
        "fps": None,
        "bitrate": "45M",
        "high_fps_bitrate": "68M",
        "audio_bitrate": "384k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "YouTube codec guidance with a vertical canvas.",
    },
    "tiktok-1080x1920": {
        "platform": "tiktok",
        "width": 1080,
        "height": 1920,
        "fps": None,
        "bitrate": "12M",
        "high_fps_bitrate": "18M",
        "audio_bitrate": "320k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "FACUT workflow default.",
    },
    "community-1x1": {
        "platform": "community",
        "width": 2160,
        "height": 2160,
        "fps": None,
        "bitrate": "24M",
        "high_fps_bitrate": "36M",
        "audio_bitrate": "320k",
        "audio_sample_rate": 48000,
        "color_space": "bt709",
        "fast_start": True,
        "source": "FACUT workflow default.",
    },
}


def _definition(name: str) -> dict[str, Any]:
    if name not in RENDER_PRESETS:
        raise KeyError(name)
    item = deepcopy(RENDER_PRESETS[name])
    parent = item.pop("inherits", None)
    if parent:
        base = _definition(parent)
        base.update(item)
        return base
    return item


def resolve_render_preset(
    name: str, *, source_fps: float, requested_fps: float | None = None
) -> dict[str, Any]:
    """Resolve inheritance, retain source fps, and select the matching bitrate tier."""

    item = _definition(name)
    fps = requested_fps or item.get("fps") or source_fps
    item["fps"] = float(fps)
    if float(fps) >= 48 and item.get("high_fps_bitrate"):
        item["bitrate"] = item["high_fps_bitrate"]
    item["name"] = name
    return item


def list_render_presets() -> dict[str, dict[str, Any]]:
    return {name: _definition(name) for name in RENDER_PRESETS}
