"""Cached low-rate audio envelopes for terminal timeline visualization."""

from __future__ import annotations

from array import array
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from facut.core.models import MediaAsset, ProjectDocument, TrackType
from facut.core.project_manager import ProjectManager
from facut.media.tools import find_executable


_BARS = " ▁▂▃▄▅▆▇█"


def _cache_key(asset: MediaAsset, sample_rate: int) -> str:
    payload = f"{asset.sha256}:{sample_rate}:waveform-v1".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def peak_envelope(
    manager: ProjectManager,
    asset: MediaAsset,
    *,
    ffmpeg: str | Path | None = None,
    sample_rate: int = 100,
) -> dict[str, Any]:
    """Decode a tiny mono envelope once and reuse it by content hash."""

    cache_dir = manager.project_dir / "cache" / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"{_cache_key(asset, sample_rate)}.json"
    if destination.is_file():
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
            if payload.get("sha256") == asset.sha256:
                payload["cached"] = True
                return payload
        except (OSError, ValueError, TypeError):
            pass
    source = manager.resolve_path(asset.path)
    executable = find_executable("ffmpeg", ffmpeg)
    result = subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Could not decode waveform for {asset.original_name}: "
            + result.stderr.decode("utf-8", errors="replace")[-1000:]
        )
    samples = array("f")
    samples.frombytes(result.stdout[: len(result.stdout) - len(result.stdout) % 4])
    peaks = [round(min(1.0, abs(float(value))), 4) for value in samples]
    payload = {
        "version": "1.0",
        "media_id": asset.id,
        "sha256": asset.sha256,
        "sample_rate": sample_rate,
        "duration": asset.technical.duration,
        "peaks": peaks,
        "cached": False,
    }
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    return payload


def timeline_waveforms(
    manager: ProjectManager,
    document: ProjectDocument,
    *,
    width: int = 100,
    from_time: float = 0.0,
    to_time: float | None = None,
    ffmpeg: str | Path | None = None,
    include_beats: bool = False,
) -> dict[str, Any]:
    """Project cached source envelopes onto visible timeline columns."""

    width = max(20, min(int(width), 400))
    upper = min(document.project.duration, to_time or document.project.duration)
    lower = max(0.0, from_time)
    if upper <= lower:
        raise ValueError("Waveform range must have positive duration.")
    duration = upper - lower
    tracks: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0
    for track in sorted(document.tracks, key=lambda item: item.order):
        if track.type not in {TrackType.VIDEO, TrackType.AUDIO} or not track.enabled:
            continue
        values = [0.0] * width
        active = False
        for clip in track.clips:
            if not clip.enabled or clip.audio.muted or clip.muted:
                continue
            asset = document.find_media(clip.media_id)
            if asset is None or not asset.technical.audio_codec:
                continue
            if clip.end <= lower or clip.timeline_start >= upper:
                continue
            envelope = peak_envelope(manager, asset, ffmpeg=ffmpeg)
            cache_hits += int(bool(envelope.get("cached")))
            cache_misses += int(not bool(envelope.get("cached")))
            peaks = envelope["peaks"]
            sample_rate = int(envelope["sample_rate"])
            if not peaks:
                continue
            active = True
            first_column = max(0, int((max(lower, clip.timeline_start) - lower) / duration * width))
            last_column = min(width, int((min(upper, clip.end) - lower) / duration * width) + 1)
            source_span = clip.source_out - clip.source_in
            for column in range(first_column, last_column):
                timeline_time = lower + (column + 0.5) / width * duration
                relative = max(0.0, timeline_time - clip.timeline_start)
                source_time = clip.source_in + relative * abs(clip.speed)
                if clip.loop and source_span > 0:
                    source_time = clip.source_in + (source_time - clip.source_in) % source_span
                index = min(len(peaks) - 1, max(0, int(source_time * sample_rate)))
                values[column] = max(values[column], float(peaks[index]))
        if active:
            bars = "".join(_BARS[min(8, round(value * 8))] for value in values)
            tracks.append(
                {
                    "track_id": track.id,
                    "name": track.name,
                    "type": track.type.value,
                    "samples": [round(value, 3) for value in values],
                    "bars": bars,
                }
            )
    beat_columns: list[int] = []
    if include_beats:
        for marker in document.markers:
            marker_text = f"{marker.label} {marker.metadata}".casefold()
            if "beat" not in marker_text and "节拍" not in marker_text:
                continue
            if lower <= marker.at <= upper:
                beat_columns.append(
                    min(width - 1, max(0, int((marker.at - lower) / duration * width)))
                )
    return {
        "from": lower,
        "to": upper,
        "width": width,
        "tracks": tracks,
        "beat_columns": sorted(set(beat_columns)),
        "cache": {"hits": cache_hits, "misses": cache_misses},
    }


def waveform_text(payload: dict[str, Any]) -> str:
    """Render a compact Unicode terminal waveform without ANSI escapes."""

    width = int(payload["width"])
    ruler = f"TIME  {payload['from']:.1f}s".ljust(width + 8) + f" {payload['to']:.1f}s"
    lines = [ruler]
    beat_columns = set(payload.get("beat_columns", []))
    if beat_columns:
        line = [" "] * width
        for column in beat_columns:
            line[column] = "│"
        lines.append(f"BEAT  {''.join(line)}")
    for track in payload["tracks"]:
        lines.append(f"{track['track_id'][:5]:<5} {track['bars']}")
    return "\n".join(lines)
