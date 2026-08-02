"""Fast FFprobe-based media metadata extraction."""

from __future__ import annotations

import json
import re
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
    normalized_tags = {
        str(key).casefold(): value for key, value in {**tags, **video_tags}.items()
    }
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
    creation_time = _first_tag(
        normalized_tags, "creation_time", "com.apple.quicktime.creationdate", "date"
    )
    latitude, longitude, altitude = _location(normalized_tags)
    color_transfer = video.get("color_transfer")
    dynamic_range = _dynamic_range(color_transfer, normalized_tags)
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
        color_transfer=color_transfer,
        color_primaries=video.get("color_primaries"),
        audio_channels=_integer(audio.get("channels")),
        sample_rate=_integer(audio.get("sample_rate")),
        rotation=int(rotation or 0),
        has_subtitles=any(stream.get("codec_type") == "subtitle" for stream in streams),
        variable_frame_rate=variable,
        creation_time=creation_time,
        timecode=_first_tag(normalized_tags, "timecode", "com.apple.quicktime.timecode"),
        timezone_offset=_timezone_offset(creation_time)
        or _first_tag(normalized_tags, "time_zone", "timezone"),
        camera_make=_first_tag(
            normalized_tags, "make", "com.apple.quicktime.make", "manufacturer"
        ),
        camera_model=_first_tag(
            normalized_tags, "model", "com.apple.quicktime.model", "camera_model"
        ),
        lens_model=_first_tag(
            normalized_tags, "lens_model", "lensmodel", "com.apple.quicktime.lens.model"
        ),
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        dynamic_range=dynamic_range,
    )


def _first_tag(tags: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = tags.get(name.casefold())
        if value not in {None, ""}:
            return str(value)
    return None


_ISO6709 = re.compile(
    r"^(?P<lat>[+-]\d+(?:\.\d+)?)(?P<lon>[+-]\d+(?:\.\d+)?)"
    r"(?P<alt>[+-]\d+(?:\.\d+)?)?/?$"
)


def _location(tags: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    location = _first_tag(
        tags,
        "com.apple.quicktime.location.iso6709",
        "location",
        "location-eng",
    )
    if location:
        match = _ISO6709.match(location.strip())
        if match:
            return (
                _number(match.group("lat")),
                _number(match.group("lon")),
                _number(match.group("alt")),
            )
    return (
        _number(_first_tag(tags, "gps_latitude", "latitude")),
        _number(_first_tag(tags, "gps_longitude", "longitude")),
        _number(_first_tag(tags, "gps_altitude", "altitude")),
    )


def _timezone_offset(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"([+-]\d{2}:?\d{2}|Z)$", value.strip(), re.I)
    if not match:
        return None
    offset = match.group(1).upper()
    if offset == "Z":
        return "+00:00"
    return offset if ":" in offset else f"{offset[:3]}:{offset[3:]}"


def _dynamic_range(transfer: Any, tags: dict[str, Any]) -> str:
    normalized = str(transfer or "").casefold()
    if normalized in {"smpte2084", "pq"}:
        return "hdr-pq"
    if normalized in {"arib-std-b67", "hlg"}:
        return "hdr-hlg"
    descriptive = " ".join(
        str(value).casefold()
        for key, value in tags.items()
        if any(token in key for token in ("gamma", "profile", "color"))
    )
    if "log" in descriptive or normalized.startswith("log"):
        return "log"
    if normalized in {"bt709", "iec61966-2-1", "smpte170m", "bt470bg"}:
        return "sdr"
    return "unknown"


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
