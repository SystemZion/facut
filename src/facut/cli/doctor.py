"""Environment diagnostics for media tooling."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
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


def collect_diagnostics(config: AppConfig) -> tuple[dict[str, Any], list[str]]:
    """Collect bounded diagnostics without decoding any user media."""

    warnings: list[str] = []
    ffmpeg_path = shutil.which(config.tools.ffmpeg)
    ffprobe_path = shutil.which(config.tools.ffprobe)
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
    hardware_encoders = {
        "nvenc": "h264_nvenc" in encoders_text or "hevc_nvenc" in encoders_text,
        "qsv": "h264_qsv" in encoders_text or "hevc_qsv" in encoders_text,
        "amf": "h264_amf" in encoders_text or "hevc_amf" in encoders_text,
        "videotoolbox": "h264_videotoolbox" in encoders_text,
    }

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
                "executable": Path(ffmpeg_path).name if ffmpeg_path else None,
                "version": ffmpeg_version,
            },
            "ffprobe": {
                "available": bool(ffprobe_version),
                "executable": Path(ffprobe_path).name if ffprobe_path else None,
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
