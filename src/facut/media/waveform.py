"""Audio waveform and EBU R128 loudness helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .tools import ensure_output_available, find_executable, run_tool


def generate_waveform(
    source: str | Path,
    output: str | Path,
    *,
    width: int = 1600,
    height: int = 360,
    color: str = "4FC3F7",
    ffmpeg: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    input_path = Path(source).expanduser().resolve()
    output_path = ensure_output_available(output, overwrite)
    executable = find_executable("ffmpeg", ffmpeg)
    run_tool(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            input_path,
            "-filter_complex",
            f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors={color}",
            "-frames:v",
            "1",
            output_path,
        ]
    )
    return output_path


def analyze_loudness(
    source: str | Path,
    *,
    ffmpeg: str | Path | None = None,
) -> dict[str, Any]:
    input_path = Path(source).expanduser().resolve()
    executable = find_executable("ffmpeg", ffmpeg)
    result = run_tool(
        [
            executable,
            "-hide_banner",
            "-nostats",
            "-i",
            input_path,
            "-map",
            "0:a:0",
            "-af",
            "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if result.returncode != 0:
        from .tools import MediaToolError

        raise MediaToolError(
            "Could not analyze audio loudness.",
            command=result.args,
            stderr=result.stderr,
        )
    matches = re.findall(r"\{[\s\S]*?\}", result.stderr)
    if not matches:
        raise ValueError("FFmpeg did not return loudness measurements.")
    payload = json.loads(matches[-1])
    return {
        "integrated_lufs": _float(payload.get("input_i")),
        "true_peak_dbtp": _float(payload.get("input_tp")),
        "loudness_range_lu": _float(payload.get("input_lra")),
        "threshold_lufs": _float(payload.get("input_thresh")),
    }


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
