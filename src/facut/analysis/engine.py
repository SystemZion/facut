"""FFmpeg-backed analysis primitives with bounded memory usage."""

from __future__ import annotations

from array import array
import hashlib
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any

from facut.exceptions import DependencyMissingError, NotImplementedFacutError
from facut.media.probe import probe_raw
from facut.media.tools import find_executable, run_tool
from facut.qc.engine import QCEngine, QCSource


_SCENE_TIME = re.compile(r"pts_time:(?P<time>-?\d+(?:\.\d+)?)")
_BLUR_MEAN = re.compile(r"blur mean:\s*(?P<value>\d+(?:\.\d+)?)", re.I)
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=(?P<value>\d+(?:\.\d+)?)")


def analyze_scenes(
    path: str | Path,
    *,
    threshold: float = 0.4,
    ffmpeg: str | Path | None = None,
) -> dict[str, Any]:
    """Detect likely scene boundaries without decoding frames into Python."""

    if not 0.0 < threshold < 1.0:
        raise ValueError("Scene threshold must be between 0 and 1.")
    source = Path(path).expanduser().resolve()
    executable = find_executable("ffmpeg", ffmpeg)
    result = run_tool(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            source,
            "-an",
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            os.devnull,
        ],
        timeout=None,
        check=False,
    )
    boundaries = sorted(
        {
            round(float(match.group("time")), 6)
            for match in _SCENE_TIME.finditer(result.stderr)
            if float(match.group("time")) >= 0
        }
    )
    points = [0.0, *boundaries]
    scenes = [
        {
            "id": f"scene_{index + 1:04d}",
            "start": start,
            "end": points[index + 1] if index + 1 < len(points) else None,
            "confidence": None,
        }
        for index, start in enumerate(points)
    ]
    return {
        "source": str(source),
        "threshold": threshold,
        "boundaries": boundaries,
        "scenes": scenes,
    }


def analyze_beats(
    path: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    sample_rate: int = 8000,
    window_seconds: float = 0.10,
    minimum_interval: float = 0.28,
) -> dict[str, Any]:
    """Return deterministic energy-onset beat candidates from decoded mono PCM."""

    source = Path(path).expanduser().resolve()
    executable = find_executable("ffmpeg", ffmpeg)
    window_samples = max(64, round(sample_rate * window_seconds))
    argv = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert process.stdout is not None
    energies: list[float] = []
    chunk_bytes = window_samples * 4
    while chunk := process.stdout.read(chunk_bytes):
        samples = array("f")
        samples.frombytes(chunk[: len(chunk) - len(chunk) % 4])
        if samples:
            energies.append(math.sqrt(sum(value * value for value in samples) / len(samples)))
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-2000:])
    if len(energies) < 3:
        return {"source": str(source), "beats": [], "tempo_bpm": None}
    baseline = statistics.median(energies) or 1e-9
    candidates: list[dict[str, Any]] = []
    last_time = -minimum_interval
    for index in range(1, len(energies) - 1):
        current = energies[index]
        previous = energies[index - 1]
        time_seconds = index * window_seconds
        if (
            current > energies[index + 1]
            and current > previous * 1.35
            and current > baseline * 1.5
            and time_seconds - last_time >= minimum_interval
        ):
            confidence = min(1.0, (current / baseline - 1.0) / 4.0)
            candidates.append(
                {"time": round(time_seconds, 6), "confidence": round(confidence, 4)}
            )
            last_time = time_seconds
    intervals = [
        right["time"] - left["time"]
        for left, right in zip(candidates, candidates[1:])
        if right["time"] > left["time"]
    ]
    tempo = 60.0 / statistics.median(intervals) if intervals else None
    return {
        "source": str(source),
        "method": "energy_onset",
        "beats": candidates,
        "tempo_bpm": round(tempo, 2) if tempo else None,
    }


def analyze_quality(
    path: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> dict[str, Any]:
    """Combine automated QC with sampled blur and exposure evidence."""

    source = Path(path).expanduser().resolve()
    engine = QCEngine(ffmpeg=ffmpeg, ffprobe=ffprobe)
    result = engine.analyze_file(QCSource(source))
    executable = find_executable("ffmpeg", ffmpeg)
    sampled = run_tool(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            source,
            "-an",
            "-vf",
            "fps=1,signalstats,metadata=print,blurdetect",
            "-f",
            "null",
            os.devnull,
        ],
        check=False,
        timeout=None,
    )
    log = sampled.stdout + "\n" + sampled.stderr
    luminance = [float(item.group("value")) for item in _YAVG.finditer(log)]
    blur = [float(item.group("value")) for item in _BLUR_MEAN.finditer(log)]
    warnings = sum(
        1
        for check in result.checks.values()
        if check.status.value in {"warning", "fail"}
    )
    exposure_penalty = sum(value < 20 or value > 235 for value in luminance)
    blur_mean = statistics.mean(blur) if blur else None
    score = max(0.0, 100.0 - warnings * 12.0 - exposure_penalty * 0.5)
    return {
        "source": str(source),
        "quality_score": round(score, 2),
        "qc": result.model_dump(mode="json"),
        "visual_samples": {
            "count": len(luminance),
            "luminance_mean": round(statistics.mean(luminance), 3)
            if luminance
            else None,
            "under_or_overexposed_samples": exposure_penalty,
            "blur_mean": round(blur_mean, 6) if blur_mean is not None else None,
        },
    }


def transcribe_local(
    path: str | Path,
    *,
    model_path: str | Path,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe with a user-supplied local faster-whisper model directory."""

    model = Path(model_path).expanduser().resolve()
    if not model.is_dir():
        raise FileNotFoundError(
            "A local Whisper model directory is required; facut will not download one implicitly."
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise DependencyMissingError(
            "Local transcription requires the optional faster-whisper package.",
            suggestion='Install facut with the "analysis" optional dependencies.',
        ) from error
    whisper = WhisperModel(str(model), device="auto", compute_type="int8")
    segments, info = whisper.transcribe(str(Path(path).resolve()), language=language)
    items = [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
            "confidence": math.exp(segment.avg_logprob),
        }
        for segment in segments
    ]
    return {
        "source": str(Path(path).resolve()),
        "language": info.language,
        "language_probability": info.language_probability,
        "segments": items,
    }


def analyze_song_metadata(
    path: str | Path, *, ffprobe: str | Path | None = None
) -> dict[str, Any]:
    """Return embedded song candidates plus a stable local fingerprint seed."""

    source = Path(path).expanduser().resolve()
    raw = probe_raw(source, ffprobe=ffprobe)
    tags: dict[str, Any] = {}
    tags.update((raw.get("format") or {}).get("tags") or {})
    for stream in raw.get("streams") or []:
        if stream.get("codec_type") == "audio":
            tags.update(stream.get("tags") or {})
    normalized = {str(key).lower(): value for key, value in tags.items()}
    title = normalized.get("title")
    artist = normalized.get("artist") or normalized.get("album_artist")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        digest.update(stream.read(1024 * 1024))
    candidates = (
        [{"title": title, "artist": artist, "confidence": 1.0, "source": "metadata"}]
        if title or artist
        else []
    )
    return {
        "source": str(source),
        "candidates": candidates,
        "fingerprint_seed": digest.hexdigest(),
        "recognition_status": "metadata_match" if candidates else "plugin_required",
        "limitation": None
        if candidates
        else "Acoustic title matching requires a configured local fingerprint database plugin.",
    }
