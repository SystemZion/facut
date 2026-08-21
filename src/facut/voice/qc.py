"""Bounded-memory quality checks for PCM WAV voice training samples."""

from __future__ import annotations

import math
from pathlib import Path
import struct
import wave
from typing import Any

from .models import VoiceProfile


def _issue(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _analyze(path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    try:
        stream = wave.open(str(path), "rb")
    except (wave.Error, EOFError, OSError) as error:
        return {
            "path": path.name,
            "status": "fail",
            "metrics": {},
            "issues": [_issue("UNSUPPORTED_WAV", "error", str(error))],
        }
    with stream:
        channels = stream.getnchannels()
        rate = stream.getframerate()
        width = stream.getsampwidth()
        frames = stream.getnframes()
        duration = frames / rate if rate else 0.0
        if width != 2:
            return {
                "path": path.name,
                "status": "fail",
                "metrics": {"sample_width": width},
                "issues": [_issue("UNSUPPORTED_PCM", "error", "Only 16-bit PCM WAV is supported in voice QC v1.")],
            }
        total = clipped = silent = 0
        sum_squares = 0.0
        peak = 0
        while data := stream.readframes(8192):
            for (value,) in struct.iter_unpack("<h", data):
                magnitude = abs(value)
                total += 1
                peak = max(peak, magnitude)
                clipped += magnitude >= 32760
                silent += magnitude <= 104
                sum_squares += value * value
    rms = math.sqrt(sum_squares / total) if total else 0.0
    rms_dbfs = 20 * math.log10(rms / 32768) if rms else -120.0
    peak_dbfs = 20 * math.log10(peak / 32768) if peak else -120.0
    clipping_ratio = clipped / total if total else 0.0
    silence_ratio = silent / total if total else 1.0
    if duration < 0.5:
        issues.append(_issue("TOO_SHORT", "error", "A voice sample must be at least 0.5 seconds."))
    if channels != 1:
        issues.append(_issue("NOT_MONO", "warning", "Mono recording is recommended for voice profiles."))
    if rate < 24000:
        issues.append(_issue("LOW_SAMPLE_RATE", "error", "Sample rate below 24 kHz is not accepted."))
    elif rate != 48000:
        issues.append(_issue("NOT_48KHZ", "warning", "48 kHz recording is recommended."))
    if clipping_ratio > 0.001:
        issues.append(_issue("CLIPPING", "error", f"Clipping ratio {clipping_ratio:.3%} exceeds 0.1%."))
    if rms_dbfs < -35:
        issues.append(_issue("TOO_QUIET", "warning", f"RMS level {rms_dbfs:.1f} dBFS is low."))
    elif rms_dbfs > -10:
        issues.append(_issue("TOO_LOUD", "warning", f"RMS level {rms_dbfs:.1f} dBFS leaves little headroom."))
    if silence_ratio > 0.8:
        issues.append(_issue("MOSTLY_SILENT", "error", "More than 80% of samples are near digital silence."))
    status = "fail" if any(item["severity"] == "error" for item in issues) else "warning" if issues else "pass"
    return {
        "path": path.name,
        "status": status,
        "metrics": {
            "duration_seconds": round(duration, 3),
            "sample_rate": rate,
            "channels": channels,
            "sample_width": width,
            "rms_dbfs": round(rms_dbfs, 2),
            "peak_dbfs": round(peak_dbfs, 2),
            "clipping_ratio": round(clipping_ratio, 6),
            "silence_ratio": round(silence_ratio, 6),
        },
        "issues": issues,
    }


def validate_voice_samples(
    paths: list[str | Path], *, recommended_total_seconds: float = 600.0
) -> dict[str, Any]:
    files = [_analyze(Path(path).expanduser().resolve()) for path in paths]
    total = sum(float(item["metrics"].get("duration_seconds", 0)) for item in files)
    issues: list[dict[str, str]] = []
    if not files:
        issues.append(_issue("NO_SAMPLES", "error", "The voice profile contains no samples."))
    elif total < recommended_total_seconds:
        issues.append(
            _issue(
                "INSUFFICIENT_DURATION",
                "warning",
                f"Recorded duration is {total:.1f}s; {recommended_total_seconds:.0f}s is recommended.",
            )
        )
    if any(item["status"] == "fail" for item in files) or any(item["severity"] == "error" for item in issues):
        status = "fail"
    elif any(item["status"] == "warning" for item in files) or issues:
        status = "warning"
    else:
        status = "pass"
    return {
        "version": "1.0",
        "status": status,
        "summary": {
            "files": len(files),
            "passed": sum(item["status"] == "pass" for item in files),
            "warnings": sum(item["status"] == "warning" for item in files),
            "failed": sum(item["status"] == "fail" for item in files),
            "total_duration_seconds": round(total, 3),
            "recommended_duration_seconds": recommended_total_seconds,
        },
        "files": files,
        "issues": issues,
    }


def validate_voice_profile(
    profile: VoiceProfile,
    paths: list[str | Path],
    *,
    recommended_total_seconds: float = 120.0,
) -> dict[str, Any]:
    """Add synthesis usability and per-style coverage to file-level PCM QC."""

    report = validate_voice_samples(
        paths, recommended_total_seconds=recommended_total_seconds
    )
    delivery_seconds: dict[str, float] = {}
    usable_seconds = 0.0
    for sample, file_report in zip(profile.samples, report["files"], strict=True):
        duration = float(file_report["metrics"].get("duration_seconds", 0))
        if file_report["status"] != "fail":
            usable_seconds += duration
            delivery = sample.delivery or "unlabelled"
            delivery_seconds[delivery] = delivery_seconds.get(delivery, 0.0) + duration
    public_styles = {"natural", "broadcast", "chat", "daily-chat", "comedy", "excited"}
    covered = sorted(style for style in public_styles if delivery_seconds.get(style, 0) >= 3)
    missing = sorted(public_styles - set(covered))
    synthesis_usable = usable_seconds >= 30 and bool(covered)
    report["profile_assessment"] = {
        "synthesis_usable": synthesis_usable,
        "quality_status": report["status"],
        "coverage_status": "complete" if not missing else "partial",
        "usable_duration_seconds": round(usable_seconds, 3),
        "delivery_seconds": {
            key: round(value, 3) for key, value in sorted(delivery_seconds.items())
        },
        "covered_styles": covered,
        "missing_styles": missing,
        "derived_reference_processing": {
            "raw_samples_modified": False,
            "normalization_peak_dbfs": -3.0,
            "quiet_samples_deprioritized": True,
        },
        "recommendation": (
            "Ready for synthesis; missing styles can be added later."
            if synthesis_usable
            else "Record at least 30 seconds of clean, style-labelled speech before synthesis."
        ),
    }
    return report
