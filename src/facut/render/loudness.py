"""Audio-only two-pass EBU R128 mastering with video stream copy."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Callable


ProgressCallback = Callable[[dict[str, Any]], None]


class LoudnessMasterError(RuntimeError):
    """Raised when an audio-only mastering pass fails."""

    def __init__(self, message: str, *, command: list[str], stderr: str, log_path: Path):
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.log_path = log_path


def _number(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _run_progress(
    args: list[str], *, duration: float, stage: str, progress: ProgressCallback | None,
    log_directory: Path, log_prefix: str | None = None,
) -> tuple[str, Path]:
    log_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    log_path = log_directory / f"{log_prefix or stage}-{stamp}.log"
    with tempfile.NamedTemporaryFile(prefix="facut-audio-", suffix=".log", delete=False) as stream:
        stderr_path = Path(stream.name)
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=stream, text=True,
            encoding="utf-8", errors="replace",
        )
        assert process.stdout is not None
        event: dict[str, str] = {}
        started = time.monotonic()
        if progress:
            progress({"event": "progress", "stage": stage, "progress": 0.0,
                      "out_time_seconds": 0.0, "frame": 0, "fps": 0.0,
                      "speed": None, "eta_seconds": None})
        for raw in process.stdout:
            line = raw.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            event[key] = value
            if key == "progress" and progress:
                out_time = _number(event.get("out_time_us")) / 1_000_000
                fraction = min(1.0, out_time / max(duration, 1e-9))
                elapsed = max(time.monotonic() - started, 1e-6)
                progress({
                    "event": "progress", "stage": stage, "progress": fraction,
                    "out_time_seconds": out_time, "frame": 0, "fps": 0.0,
                    "speed": event.get("speed"),
                    "eta_seconds": 0.0 if fraction >= 1 else elapsed / max(fraction, 1e-9) * (1 - fraction),
                })
                event = {}
        returncode = process.wait()
        if progress and returncode == 0:
            progress({"event": "progress", "stage": stage, "progress": 1.0,
                      "out_time_seconds": duration, "frame": 0, "fps": 0.0,
                      "speed": None, "eta_seconds": 0.0})
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    stderr_path.unlink(missing_ok=True)
    log_path.write_text(
        "command_json:\n" + json.dumps(args, ensure_ascii=False, indent=2)
        + "\nstderr:\n" + stderr,
        encoding="utf-8",
    )
    if returncode != 0:
        raise LoudnessMasterError(
            f"FFmpeg {stage} failed.", command=args, stderr=stderr[-8000:], log_path=log_path
        )
    return stderr, log_path


def measure_loudness(
    ffmpeg: str, source: Path, *, duration: float, target_lufs: float,
    true_peak: float, loudness_range: float, progress: ProgressCallback | None,
    log_directory: Path,
) -> dict[str, float]:
    """Measure one file's audio without decoding or filtering its video."""

    loudnorm = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={loudness_range}:print_format=json"
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-i", str(source),
        "-map", "0:a:0", "-vn", "-af", loudnorm, "-f", "null",
        "-progress", "pipe:1", "-nostats", "-",
    ]
    stderr, log_path = _run_progress(
        args, duration=duration, stage="loudness_scan", progress=progress,
        log_directory=log_directory, log_prefix="loudness-pass1",
    )
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, re.DOTALL)
    if not matches:
        raise LoudnessMasterError(
            "FFmpeg produced no EBU R128 measurements.", command=args,
            stderr=stderr[-8000:], log_path=log_path,
        )
    payload = json.loads(matches[-1])
    return {name: float(payload[name]) for name in (
        "input_i", "input_tp", "input_lra", "input_thresh", "target_offset"
    )}


def _apply(
    ffmpeg: str, source: Path, destination: Path, *, duration: float,
    target_lufs: float, safe_peak: float, loudness_range: float,
    measured: dict[str, float], audio_codec: str, audio_bitrate: str,
    audio_sample_rate: int | None, overwrite: bool, progress: ProgressCallback | None,
    log_directory: Path,
) -> None:
    loudnorm = ":".join([
        f"loudnorm=I={target_lufs}", f"TP={safe_peak}", f"LRA={loudness_range}",
        f"measured_I={measured['input_i']}", f"measured_TP={measured['input_tp']}",
        f"measured_LRA={measured['input_lra']}",
        f"measured_thresh={measured['input_thresh']}",
        f"offset={measured['target_offset']}", "linear=true",
    ])
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y" if overwrite else "-n", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
        "-af", loudnorm, "-c:a", audio_codec, "-b:a", audio_bitrate,
        *(["-ar", str(audio_sample_rate)] if audio_sample_rate else []),
        "-t", f"{duration:.9f}", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(destination),
    ]
    _run_progress(
        args, duration=duration, stage="loudness_apply", progress=progress,
        log_directory=log_directory, log_prefix="loudness-pass2",
    )


def _copy_silent_master(
    ffmpeg: str, source: Path, destination: Path, *, duration: float,
    overwrite: bool, progress: ProgressCallback | None, log_directory: Path,
) -> None:
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y" if overwrite else "-n", "-i", str(source), "-map", "0:v:0",
        "-map", "0:a:0", "-c", "copy", "-t", f"{duration:.9f}",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        str(destination),
    ]
    _run_progress(
        args, duration=duration, stage="loudness_apply", progress=progress,
        log_directory=log_directory, log_prefix="loudness-silence-copy",
    )


def master_loudness(
    ffmpeg: str, source: Path, destination: Path, *, duration: float,
    target_lufs: float, true_peak: float, loudness_range: float,
    audio_codec: str, audio_bitrate: str, audio_sample_rate: int | None,
    overwrite: bool, progress: ProgressCallback | None, log_directory: Path,
) -> dict[str, Any]:
    """Master audio, verify AAC overshoot, and never re-encode video."""

    safety = 0.5 if audio_codec.casefold() == "aac" else 0.0
    safe_peak = max(-9.0, true_peak - safety)
    measured = measure_loudness(
        ffmpeg, source, duration=duration, target_lufs=target_lufs,
        true_peak=safe_peak, loudness_range=loudness_range, progress=progress,
        log_directory=log_directory,
    )
    if not math.isfinite(measured["input_i"]) or not math.isfinite(measured["input_tp"]):
        _copy_silent_master(
            ffmpeg, source, destination, duration=duration, overwrite=overwrite,
            progress=progress, log_directory=log_directory,
        )
        return {
            "input_lufs": None, "output_lufs": None,
            "actual_true_peak_db": None, "target_lufs": target_lufs,
            "true_peak_limit_db": true_peak, "aac_safety_margin_db": safety,
            "video_master_reused": True, "audio_retry": False,
            "skipped": "digital_silence",
        }
    _apply(
        ffmpeg, source, destination, duration=duration, target_lufs=target_lufs,
        safe_peak=safe_peak, loudness_range=loudness_range, measured=measured,
        audio_codec=audio_codec, audio_bitrate=audio_bitrate,
        audio_sample_rate=audio_sample_rate, overwrite=overwrite, progress=progress,
        log_directory=log_directory,
    )
    verified = measure_loudness(
        ffmpeg, destination, duration=duration, target_lufs=target_lufs,
        true_peak=safe_peak, loudness_range=loudness_range, progress=None,
        log_directory=log_directory,
    )
    retried = False
    if verified["input_tp"] > true_peak + 0.05 and safe_peak > -9.0:
        retried = True
        safety = min(1.5, safety + verified["input_tp"] - true_peak + 0.2)
        safe_peak = max(-9.0, true_peak - safety)
        destination.unlink(missing_ok=True)
        _apply(
            ffmpeg, source, destination, duration=duration, target_lufs=target_lufs,
            safe_peak=safe_peak, loudness_range=loudness_range, measured=measured,
            audio_codec=audio_codec, audio_bitrate=audio_bitrate,
            audio_sample_rate=audio_sample_rate, overwrite=True, progress=progress,
            log_directory=log_directory,
        )
        verified = measure_loudness(
            ffmpeg, destination, duration=duration, target_lufs=target_lufs,
            true_peak=safe_peak, loudness_range=loudness_range, progress=None,
            log_directory=log_directory,
        )
    return {
        "input_lufs": measured["input_i"], "output_lufs": verified["input_i"],
        "actual_true_peak_db": verified["input_tp"], "target_lufs": target_lufs,
        "true_peak_limit_db": true_peak, "aac_safety_margin_db": safety,
        "video_master_reused": True, "audio_retry": retried,
    }
