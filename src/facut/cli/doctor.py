"""Environment diagnostics for media tooling."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from facut import __version__
from facut.config import AppConfig


def _run(arguments: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _first_line(result: subprocess.CompletedProcess[str] | None) -> str | None:
    if result is None or result.returncode != 0:
        return None
    output = result.stdout or result.stderr
    return output.splitlines()[0].strip() if output else None


def _writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".facut-", delete=True):
            return True
    except OSError:
        return False


def _font_discovery() -> dict[str, Any]:
    candidates: list[Path]
    if sys.platform == "win32":
        candidates = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    elif sys.platform == "darwin":
        candidates = [Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        candidates = [Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"]
    available = [str(path) for path in candidates if path.is_dir()]
    return {"available": bool(available), "directories": available}


_HARDWARE_ENCODERS = {
    "nvenc": ("h264_nvenc", "hevc_nvenc"),
    "qsv": ("h264_qsv", "hevc_qsv"),
    "amf": ("h264_amf", "hevc_amf"),
    "videotoolbox": ("h264_videotoolbox", "hevc_videotoolbox"),
}


def _encoder_failure_summary(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None:
        return "FFmpeg could not be started or the encoder test timed out."
    output = result.stderr or result.stdout or ""
    lines = [
        re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
        for line in output.splitlines()
        if line.strip()
    ]
    if not lines:
        return f"FFmpeg exited with code {result.returncode}."
    selected = lines if len(lines) <= 4 else [*lines[:2], *lines[-2:]]
    return " | ".join(selected)[:1000]


def _probe_hardware_encoder(
    ffmpeg_path: str,
    backend: str,
    available_encoders: set[str],
) -> dict[str, Any]:
    """Run a tiny generated encode so listed hardware is not mistaken for usable hardware."""

    candidates = _HARDWARE_ENCODERS[backend]
    encoder = next((name for name in candidates if name in available_encoders), candidates[0])
    detected = any(name in available_encoders for name in candidates)
    diagnostic: dict[str, Any] = {
        "detected": detected,
        "usable": False,
        "implemented": True,
        "encoder": encoder,
    }
    if not detected:
        diagnostic["test"] = {
            "status": "not_run",
            "reason": "No matching encoder is listed by FFmpeg.",
        }
        return diagnostic

    frame_count = 6
    started = time.perf_counter()
    result = _run(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=24:d=0.25",
            "-frames:v",
            str(frame_count),
            "-an",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-f",
            "null",
            "-",
        ],
        timeout=6.0,
    )
    elapsed = max(time.perf_counter() - started, 0.000001)
    elapsed_seconds = round(elapsed, 4)
    if result is not None and result.returncode == 0:
        diagnostic["usable"] = True
        diagnostic["test"] = {
            "status": "success",
            "elapsed_seconds": elapsed_seconds,
            "frames": frame_count,
            "frames_per_second": round(frame_count / elapsed, 2),
        }
    else:
        diagnostic["test"] = {
            "status": "failed",
            "elapsed_seconds": elapsed_seconds,
            "failure_summary": _encoder_failure_summary(result),
        }
    return diagnostic


def _listed_encoder_names(encoders_text: str) -> set[str]:
    names: set[str] = set()
    for line in encoders_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return names


def collect_diagnostics(config: AppConfig) -> tuple[dict[str, Any], list[str]]:
    """Collect bounded diagnostics without decoding any user media."""

    warnings: list[str] = []
    located_ffmpeg = shutil.which(config.tools.ffmpeg)
    located_ffprobe = shutil.which(config.tools.ffprobe)
    ffmpeg_path = str(Path(located_ffmpeg).resolve()) if located_ffmpeg else None
    ffprobe_path = str(Path(located_ffprobe).resolve()) if located_ffprobe else None
    ffmpeg_version = _first_line(_run([ffmpeg_path, "-version"])) if ffmpeg_path else None
    ffprobe_version = _first_line(_run([ffprobe_path, "-version"])) if ffprobe_path else None

    encoders_text = ""
    filters_text = ""
    if ffmpeg_path:
        encoders = _run([ffmpeg_path, "-hide_banner", "-encoders"])
        filters = _run([ffmpeg_path, "-hide_banner", "-filters"])
        # Older FFmpeg builds do not know -hide_banner. Retry with the
        # long-standing flags so doctor remains useful on legacy installations.
        if encoders is None or encoders.returncode != 0:
            encoders = _run([ffmpeg_path, "-encoders"])
        if filters is None or filters.returncode != 0:
            filters = _run([ffmpeg_path, "-filters"])
        if encoders and encoders.returncode == 0:
            encoders_text = f"{encoders.stdout}\n{encoders.stderr}"
        if filters and filters.returncode == 0:
            filters_text = f"{filters.stdout}\n{filters.stderr}"
    else:
        warnings.append("FFmpeg was not found; preview and render commands are unavailable.")
    if not ffprobe_path:
        warnings.append("FFprobe was not found; media inspection and import analysis are unavailable.")

    video_encoders = {
        "h264": any(name in encoders_text for name in ("libx264", "h264_mf", "h264_videotoolbox")),
        "h265": any(name in encoders_text for name in ("libx265", "hevc_mf", "hevc_videotoolbox")),
        "av1": any(name in encoders_text for name in ("libaom-av1", "libsvtav1", "av1_")),
        "prores": "prores_" in encoders_text,
        "vp9": "libvpx-vp9" in encoders_text,
    }
    audio_encoders = {
        "aac": " aac " in encoders_text,
        "flac": " flac " in encoders_text,
        "opus": "libopus" in encoders_text or " opus " in encoders_text,
        "pcm": "pcm_s16le" in encoders_text,
    }
    listed_encoders = _listed_encoder_names(encoders_text)
    hardware_encoders = (
        {
            name: _probe_hardware_encoder(ffmpeg_path, name, listed_encoders)
            for name in _HARDWARE_ENCODERS
        }
        if ffmpeg_path
        else {
            name: {
                "detected": False,
                "usable": False,
                "implemented": True,
                "encoder": candidates[0],
                "test": {"status": "not_run", "reason": "FFmpeg is unavailable."},
            }
            for name, candidates in _HARDWARE_ENCODERS.items()
        }
    )
    for name, diagnostic in hardware_encoders.items():
        if diagnostic["detected"] and not diagnostic["usable"]:
            warnings.append(
                f"{name.upper()} is listed by FFmpeg but failed the sample encode: "
                f"{diagnostic['test'].get('failure_summary', 'unknown failure')}"
            )

    temp_path = config.temporary_directory
    cache_path = config.cache.directory
    temp_writable = _writable_directory(temp_path)
    cache_writable = _writable_directory(cache_path)
    if not temp_writable:
        warnings.append("The configured temporary directory is not writable.")
    if not cache_writable:
        warnings.append("The configured cache directory is not writable.")
    disk_anchor = temp_path if temp_path.exists() else Path(tempfile.gettempdir())
    try:
        free_bytes = shutil.disk_usage(disk_anchor).free
    except OSError:
        free_bytes = None

    filters_to_check = ("xfade", "overlay", "scale", "concat", "loudnorm", "subtitles")
    return (
        {
            "facut": {"version": __version__},
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "python": {
                "version": platform.python_version(),
                "supported": sys.version_info >= (3, 11),
            },
            "ffmpeg": {
                "available": bool(ffmpeg_version),
                "executable": ffmpeg_path,
                "version": ffmpeg_version,
            },
            "ffprobe": {
                "available": bool(ffprobe_version),
                "executable": ffprobe_path,
                "version": ffprobe_version,
            },
            "encoders": {
                "video": video_encoders,
                "audio": audio_encoders,
                "hardware": hardware_encoders,
            },
            "filters": {name: name in filters_text for name in filters_to_check},
            "directories": {
                "temporary_writable": temp_writable,
                "cache_writable": cache_writable,
                "free_bytes": free_bytes,
            },
            "fonts": _font_discovery(),
            "sample_render": {
                "status": "not_run",
                "reason": "Use --sample-render to run the optional codec smoke test.",
            },
            "healthy": bool(ffmpeg_version and ffprobe_version and temp_writable and cache_writable),
        },
        warnings,
    )


def run_sample_render(config: AppConfig) -> dict[str, Any]:
    """Render one tiny generated frame to verify the FFmpeg execution path."""

    executable = shutil.which(config.tools.ffmpeg)
    if not executable:
        return {"status": "skipped", "reason": "FFmpeg is unavailable."}
    config.temporary_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=config.temporary_directory, prefix="doctor-") as folder:
        output = Path(folder) / "sample.mp4"
        result = _run(
            [
                executable,
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=160x90:r=1:d=1",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(output),
            ],
            timeout=20,
        )
        if result is not None and result.returncode == 0 and output.is_file() and output.stat().st_size:
            return {"status": "success", "encoder": "libx264"}
        error = (result.stderr if result else "Unable to start FFmpeg.").strip()
        return {"status": "failed", "error": error[-1000:]}
