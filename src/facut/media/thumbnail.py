"""Thumbnail and contact-sheet generation."""

from __future__ import annotations

import math
from pathlib import Path

from .probe import probe_media
from .tools import ensure_output_available, find_executable, run_tool


def generate_thumbnail(
    source: str | Path,
    output: str | Path,
    *,
    at: float = 0.0,
    width: int | None = None,
    ffmpeg: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    input_path = Path(source).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f'Media file "{input_path}" was not found.')
    output_path = ensure_output_available(output, overwrite)
    executable = find_executable("ffmpeg", ffmpeg)
    filters: list[str] = []
    if width:
        filters.append(f"scale={int(width)}:-2")
    argv: list[str | Path] = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-ss",
        f"{at:.9f}",
        "-i",
        input_path,
        "-frames:v",
        "1",
    ]
    if filters:
        argv.extend(["-vf", ",".join(filters)])
    argv.append(output_path)
    run_tool(argv)
    return output_path


def generate_contact_sheet(
    source: str | Path,
    output: str | Path,
    *,
    interval: float = 5.0,
    columns: int = 5,
    width: int = 320,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    if interval <= 0 or columns <= 0 or width <= 0:
        raise ValueError("interval, columns, and width must be positive")
    input_path = Path(source).expanduser().resolve()
    output_path = ensure_output_available(output, overwrite)
    info = probe_media(input_path, ffprobe=ffprobe)
    if not info.duration:
        raise ValueError("A contact sheet requires media with a known duration.")
    count = max(1, math.ceil(info.duration / interval))
    rows = math.ceil(count / columns)
    executable = find_executable("ffmpeg", ffmpeg)
    vf = (
        f"fps=1/{interval:.9f},"
        f"scale={width}:-2,"
        f"tile={columns}x{rows}:nb_frames={count}:padding=4:margin=4"
    )
    run_tool(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            input_path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            output_path,
        ],
        timeout=max(60.0, info.duration * 1.5),
    )
    return output_path
