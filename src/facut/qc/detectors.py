"""FFmpeg-backed decode, black-frame, silence, loudness and tile checks."""

from __future__ import annotations

import math
from pathlib import Path
import re

from facut.media.tools import ensure_output_available

from .models import CheckResult, QCStatus
from .process import run_streaming


_BLACK = re.compile(
    r"black_start:(?P<start>.*?)\s+black_end:(?P<end>-?[\d.]+)"
    r"\s+black_duration:(?P<duration>[\d.]+)"
)
_SILENCE_START = re.compile(r"silence_start:\s*(?P<start>-?[\d.]+)")
_SILENCE_END = re.compile(
    r"silence_end:\s*(?P<end>-?[\d.]+)\s*\|\s*"
    r"silence_duration:\s*(?P<duration>[\d.]+)"
)
_FREEZE_START = re.compile(r"freeze_start:\s*(?P<start>-?[\d.]+)")
_FREEZE_END = re.compile(
    r"freeze_duration:\s*(?P<duration>[\d.]+).*?freeze_end:\s*(?P<end>-?[\d.]+)"
)


def check_decode(
    source: Path, ffmpeg: str, *, timeout: float | None = None
) -> CheckResult:
    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-i",
            source,
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ],
        timeout=timeout,
    )
    errors = [line for line in result.stderr_tail if line.strip()]
    if result.returncode != 0 or errors:
        return CheckResult(
            status=QCStatus.FAIL,
            summary="Full decode reported errors.",
            data={
                "returncode": result.returncode,
                "elapsed_seconds": round(result.elapsed_seconds, 3),
            },
            errors=errors,
        )
    return CheckResult(
        status=QCStatus.PASS,
        summary="All selected video and audio streams decoded successfully.",
        data={
            "returncode": result.returncode,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
        },
    )


def check_black_frames(
    source: Path,
    ffmpeg: str,
    *,
    minimum_duration: float = 0.5,
    pixel_threshold: float = 0.10,
    picture_ratio: float = 0.98,
    timeout: float | None = None,
) -> CheckResult:
    segments: list[dict[str, float]] = []

    def parse(line: str) -> None:
        match = _BLACK.search(line)
        if match:
            # Some vendor FFmpeg builds print "invalid param" for a black
            # interval that begins at the first frame. Its end/duration remain
            # valid and unambiguously imply a zero start.
            try:
                start = float(match["start"])
            except ValueError:
                start = max(0.0, float(match["end"]) - float(match["duration"]))
            segments.append(
                {
                    "start": start,
                    "end": float(match["end"]),
                    "duration": float(match["duration"]),
                }
            )

    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"blackdetect=d={minimum_duration}:pic_th={picture_ratio}:pix_th={pixel_threshold}",
            "-f",
            "null",
            "-",
        ],
        on_stderr=parse,
        timeout=timeout,
    )
    if result.returncode != 0:
        return CheckResult(
            status=QCStatus.FAIL,
            summary="Black-frame analysis failed.",
            data={"returncode": result.returncode},
            errors=result.stderr_tail[-20:],
        )
    status = QCStatus.WARNING if segments else QCStatus.PASS
    summary = (
        f"Detected {len(segments)} black segment(s)."
        if segments
        else "No black segment met the configured threshold."
    )
    return CheckResult(
        status=status,
        summary=summary,
        data={
            "segments": segments,
            "minimum_duration_seconds": minimum_duration,
            "pixel_threshold": pixel_threshold,
            "picture_ratio": picture_ratio,
        },
    )


def check_silence(
    source: Path,
    ffmpeg: str,
    *,
    threshold_db: float = -50.0,
    minimum_duration: float = 0.5,
    media_duration: float | None = None,
    timeout: float | None = None,
) -> CheckResult:
    segments: list[dict[str, float]] = []
    open_start: float | None = None

    def parse(line: str) -> None:
        nonlocal open_start
        start_match = _SILENCE_START.search(line)
        if start_match:
            open_start = float(start_match["start"])
        end_match = _SILENCE_END.search(line)
        if end_match:
            end = float(end_match["end"])
            duration = float(end_match["duration"])
            start = open_start if open_start is not None else end - duration
            segments.append({"start": start, "end": end, "duration": duration})
            open_start = None

    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"silencedetect=n={threshold_db}dB:d={minimum_duration}",
            "-f",
            "null",
            "-",
        ],
        on_stderr=parse,
        timeout=timeout,
    )
    if result.returncode != 0:
        return CheckResult(
            status=QCStatus.FAIL,
            summary="Silence analysis failed.",
            data={"returncode": result.returncode},
            errors=result.stderr_tail[-20:],
        )
    if open_start is not None and media_duration is not None and media_duration > open_start:
        segments.append(
            {
                "start": open_start,
                "end": media_duration,
                "duration": media_duration - open_start,
            }
        )
    status = QCStatus.WARNING if segments else QCStatus.PASS
    summary = (
        f"Detected {len(segments)} silence segment(s)."
        if segments
        else "No silence segment met the configured threshold."
    )
    return CheckResult(
        status=status,
        summary=summary,
        data={
            "segments": segments,
            "threshold_db": threshold_db,
            "minimum_duration_seconds": minimum_duration,
        },
    )


def check_freeze(
    source: Path,
    ffmpeg: str,
    *,
    minimum_duration: float = 2.0,
    noise_db: float = -60.0,
    timeout: float | None = None,
) -> CheckResult:
    """Detect suspicious static video intervals without declaring intentional holds invalid."""

    segments: list[dict[str, float]] = []
    open_start: float | None = None

    def parse(line: str) -> None:
        nonlocal open_start
        start_match = _FREEZE_START.search(line)
        if start_match:
            open_start = float(start_match["start"])
        end_match = _FREEZE_END.search(line)
        if end_match:
            end = float(end_match["end"])
            duration = float(end_match["duration"])
            start = open_start if open_start is not None else end - duration
            segments.append({"start": start, "end": end, "duration": duration})
            open_start = None

    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"freezedetect=n={noise_db}dB:d={minimum_duration}",
            "-f",
            "null",
            "-",
        ],
        on_stderr=parse,
        timeout=timeout,
    )
    if result.returncode != 0:
        return CheckResult(
            status=QCStatus.FAIL,
            summary="Freeze analysis failed.",
            data={"returncode": result.returncode},
            errors=result.stderr_tail[-20:],
        )
    return CheckResult(
        status=QCStatus.WARNING if segments else QCStatus.PASS,
        summary=(
            f"Detected {len(segments)} long static segment(s); review intentional holds."
            if segments
            else "No suspicious static segment met the configured threshold."
        ),
        data={
            "segments": segments,
            "minimum_duration_seconds": minimum_duration,
            "noise_db": noise_db,
        },
    )


def check_loudness(
    source: Path,
    ffmpeg: str,
    *,
    target_lufs: float = -14.0,
    tolerance_lu: float = 2.0,
    maximum_true_peak_dbfs: float = -1.0,
    timeout: float | None = None,
) -> CheckResult:
    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        timeout=timeout,
        tail_lines=240,
    )
    if result.returncode != 0:
        return CheckResult(
            status=QCStatus.FAIL,
            summary="EBU R128 loudness analysis failed.",
            data={"returncode": result.returncode},
            errors=result.stderr_tail[-20:],
        )
    metrics = _parse_ebur128(result.stderr_tail)
    integrated = metrics.get("integrated_lufs")
    true_peak = metrics.get("true_peak_dbfs")
    issues: list[str] = []
    if integrated is None:
        issues.append("Integrated loudness was not measurable.")
    elif not target_lufs - tolerance_lu <= integrated <= target_lufs + tolerance_lu:
        issues.append(
            f"Integrated loudness {integrated:.1f} LUFS is outside "
            f"{target_lufs:.1f}±{tolerance_lu:.1f} LU."
        )
    if true_peak is not None and true_peak > maximum_true_peak_dbfs:
        issues.append(
            f"True peak {true_peak:.1f} dBFS exceeds {maximum_true_peak_dbfs:.1f} dBFS."
        )
    return CheckResult(
        status=QCStatus.WARNING if issues else QCStatus.PASS,
        summary=" ".join(issues) if issues else "Loudness is within configured limits.",
        data={
            **metrics,
            "target_lufs": target_lufs,
            "tolerance_lu": tolerance_lu,
            "maximum_true_peak_dbfs": maximum_true_peak_dbfs,
        },
    )


def _parse_number(value: str) -> float | None:
    lowered = value.lower()
    if lowered in {"-inf", "inf", "+inf", "nan"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _parse_ebur128(lines: list[str]) -> dict[str, float | None]:
    summary_index = max(
        (index for index, line in enumerate(lines) if "Summary:" in line),
        default=-1,
    )
    summary = lines[summary_index + 1 :] if summary_index >= 0 else lines
    section = ""
    result: dict[str, float | None] = {}
    patterns = {
        "integrated": ("integrated_lufs", re.compile(r"\bI:\s*([-+\w.]+)\s+LUFS")),
        "range": ("loudness_range_lu", re.compile(r"\bLRA:\s*([-+\w.]+)\s+LU")),
        "true_peak": ("true_peak_dbfs", re.compile(r"\bPeak:\s*([-+\w.]+)\s+dBFS")),
    }
    for line in summary:
        lowered = line.lower()
        if "integrated loudness:" in lowered:
            section = "integrated"
        elif "loudness range:" in lowered:
            section = "range"
        elif "true peak:" in lowered:
            section = "true_peak"
        key_pattern = patterns.get(section)
        if key_pattern:
            key, pattern = key_pattern
            match = pattern.search(line)
            if match:
                result[key] = _parse_number(match.group(1))
    return {
        "integrated_lufs": result.get("integrated_lufs"),
        "loudness_range_lu": result.get("loudness_range_lu"),
        "true_peak_dbfs": result.get("true_peak_dbfs"),
    }


def generate_contact_sheet(
    source: Path,
    output: Path,
    ffmpeg: str,
    *,
    duration: float | None,
    overwrite: bool = False,
    columns: int = 4,
    rows: int = 3,
    tile_width: int = 320,
    timeout: float | None = None,
) -> Path:
    destination = ensure_output_available(output, overwrite)
    interval = max((duration or 12.0) / (columns * rows), 0.1)
    filter_graph = (
        f"fps=1/{interval:.6f},"
        f"scale={tile_width}:-2:force_original_aspect_ratio=decrease,"
        f"tile={columns}x{rows}:padding=4:margin=4:color=black"
    )
    result = run_streaming(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y" if overwrite else "-n",
            "-v",
            "error",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            filter_graph,
            "-frames:v",
            "1",
            destination,
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not destination.is_file():
        raise RuntimeError(
            "Contact-sheet generation failed: "
            + (result.stderr_tail[-1] if result.stderr_tail else "no output was created")
        )
    return destination
