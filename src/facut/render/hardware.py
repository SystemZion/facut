"""Hardware encoder discovery and safe software fallback."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess


@dataclass(frozen=True, slots=True)
class EncoderChoice:
    encoder: str
    hardware: str
    warnings: tuple[str, ...] = ()


HARDWARE_ENCODERS = {
    "nvenc": "h264_nvenc",
    "qsv": "h264_qsv",
    "amf": "h264_amf",
    "videotoolbox": "h264_videotoolbox",
}


def available_encoders(ffmpeg: str) -> set[str]:
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        return set()
    found: set[str] = set()
    for line in process.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            found.add(parts[1])
    return found


def choose_h264_encoder(
    ffmpeg: str, requested: str = "auto", *, encoders: set[str] | None = None
) -> EncoderChoice:
    """Select an H.264 encoder, warning rather than failing on absent hardware."""

    supported = available_encoders(ffmpeg) if encoders is None else encoders
    if requested in {"none", "software", "libx264"}:
        return EncoderChoice("libx264", "none")
    if requested == "auto":
        for hardware in ("nvenc", "qsv", "amf", "videotoolbox"):
            encoder = HARDWARE_ENCODERS[hardware]
            if encoder in supported:
                return EncoderChoice(encoder, hardware)
        return EncoderChoice("libx264", "none")
    if requested not in HARDWARE_ENCODERS:
        raise ValueError(
            "hardware must be auto, nvenc, qsv, amf, videotoolbox, or none"
        )
    encoder = HARDWARE_ENCODERS[requested]
    if encoder in supported:
        return EncoderChoice(encoder, requested)
    return EncoderChoice(
        "libx264",
        "none",
        (
            f"Hardware encoder {encoder} is unavailable; falling back to libx264.",
        ),
    )
