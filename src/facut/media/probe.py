"""Fast FFprobe-based media metadata extraction."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from facut.core.models import MediaTechnicalInfo

from .tools import MediaToolError, find_executable, run_tool


class MediaProbeError(MediaToolError):
    code = "MEDIA_PROBE_FAILED"
    exit_code = 4


def probe_media(
    path: str | Path,
    *,
    ffprobe: str | Path | None = None,
    include_keyframes: bool = False,
    timeout: float = 60.0,
) -> MediaTechnicalInfo:
    """Inspect container and streams without decoding the complete input."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f'Media file "{source}" was not found.')
    executable = find_executable("ffprobe", ffprobe)
    argv: list[str | Path] = [
        executable,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        source,
    ]
    try:
        result = run_tool(argv, timeout=timeout)
        payload = json.loads(result.stdout)
    except (MediaToolError, json.JSONDecodeError) as exc:
        raise MediaProbeError(
            f'Could not inspect "{source.name}".',
            command=argv,
            stderr=getattr(exc, "stderr", str(exc)),
            suggestion="Verify that FFprobe supports the file's container and codec.",
        ) from exc
    info = _parse_probe(payload)
    if include_keyframes and info.video_codec:
        info.keyframes = probe_keyframes(source, ffprobe=executable, timeout=timeout)
    return info


def probe_raw(
    path: str | Path, *, ffprobe: str | Path | None = None, timeout: float = 60.0
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    executable = find_executable("ffprobe", ffprobe)
    result = run_tool(
        [
            executable,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            source,
        ],
        timeout=timeout,
    )
    return json.loads(result.stdout)


def probe_keyframes(
    path: str | Path,
    *,
    ffprobe: str | Path | None = None,
    timeout: float = 120.0,
) -> list[float]:
    """Return keyframe timestamps. FFprobe reads packet metadata, not decoded frames."""

    source = Path(path).expanduser().resolve()
    executable = find_executable("ffprobe", ffprobe)
    result = run_tool(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-skip_frame",
            "nokey",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            source,
        ],
        timeout=timeout,
    )
    keyframes: list[float] = []
    for line in result.stdout.splitlines():
        try:
            keyframes.append(float(line.strip().split(",", 1)[0]))
        except ValueError:
            continue
    return keyframes


def _parse_probe(payload: dict[str, Any]) -> MediaTechnicalInfo:
    streams = payload.get("streams") or []
    container = payload.get("format") or {}
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    tags = container.get("tags") or {}
    video_tags = video.get("tags") or {}
    side_data = video.get("side_data_list") or []
    rotation = video_tags.get("rotate")
    if rotation is None:
        rotation = next(
            (item.get("rotation") for item in side_data if item.get("rotation") is not None),
            0,
        )
    real_rate = _ratio(video.get("r_frame_rate"))
    average_rate = _ratio(video.get("avg_frame_rate"))
    variable = bool(
        real_rate
        and average_rate
        and abs(real_rate - average_rate) > max(0.001, average_rate * 0.001)
    )
    duration = _number(container.get("duration"))
    if duration is None:
        duration = max(
            (_number(stream.get("duration")) or 0.0 for stream in streams),
            default=0.0,
        )
    return MediaTechnicalInfo(
        container=container.get("format_name"),
        duration=duration,
        bitrate=_integer(container.get("bit_rate")),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        width=_integer(video.get("width")),
        height=_integer(video.get("height")),
        frame_rate=real_rate,
        average_frame_rate=average_rate,
        pixel_format=video.get("pix_fmt"),
        color_space=video.get("color_space"),
        color_transfer=video.get("color_transfer"),
        color_primaries=video.get("color_primaries"),
        audio_channels=_integer(audio.get("channels")),
        sample_rate=_integer(audio.get("sample_rate")),
        rotation=int(rotation or 0),
        has_subtitles=any(stream.get("codec_type") == "subtitle" for stream in streams),
        variable_frame_rate=variable,
        creation_time=tags.get("creation_time") or video_tags.get("creation_time"),
    )


def _ratio(value: Any) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
