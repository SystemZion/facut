"""Low-resolution proxy creation."""

from __future__ import annotations

from pathlib import Path

from .tools import ensure_output_available, find_executable, run_tool


def generate_proxy(
    source: str | Path,
    output: str | Path,
    *,
    height: int = 540,
    codec: str = "h264",
    ffmpeg: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    if height <= 0:
        raise ValueError("height must be positive")
    input_path = Path(source).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f'Media file "{input_path}" was not found.')
    output_path = ensure_output_available(output, overwrite)
    executable = find_executable("ffmpeg", ffmpeg)
    encoder = {
        "h264": "libx264",
        "h265": "libx265",
        "hevc": "libx265",
        "vp9": "libvpx-vp9",
    }.get(codec.lower(), codec)
    run_tool(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            input_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale=-2:{height}",
            "-c:v",
            encoder,
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path,
        ],
        timeout=None,
    )
    return output_path
