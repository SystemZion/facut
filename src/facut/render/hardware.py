"""Hardware encoder discovery and safe software fallback."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import shutil
import subprocess
import time


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


@lru_cache(maxsize=8)
def hardware_device_present(hardware: str) -> bool:
    """Cheaply reject hardware families that have no device on this host.

    FFmpeg builds often list NVENC even after an eGPU has been unplugged. In
    auto mode, asking that encoder to initialize adds latency and a frightening
    failure warning despite a healthy Intel QSV device being available.
    ``nvidia-smi -L`` is the vendor-supported device presence check; other
    families continue to use the real FFmpeg initialization probe below.
    """

    if hardware != "nvenc":
        return True
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "GPU " in result.stdout


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


@lru_cache(maxsize=32)
def probe_encoder_usable(ffmpeg: str, encoder: str) -> tuple[bool, str, float]:
    """Prove that a listed hardware encoder can initialize on this machine.

    FFmpeg commonly lists NVENC/AMF even when the corresponding device or
    runtime is absent.  A generated six-frame encode is cheap, deterministic,
    and cached for the lifetime of the FACUT process.
    """

    started = time.perf_counter()
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=24:d=0.25",
                "-frames:v",
                "6",
                "-an",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error), round(time.perf_counter() - started, 4)
    elapsed = round(time.perf_counter() - started, 4)
    if process.returncode == 0:
        return True, "", elapsed
    lines = [line.strip() for line in (process.stderr or process.stdout).splitlines() if line.strip()]
    summary = " | ".join(lines[-4:])[:1000] or f"FFmpeg exited with code {process.returncode}."
    return False, summary, elapsed


def choose_h264_encoder(
    ffmpeg: str,
    requested: str = "auto",
    *,
    encoders: set[str] | None = None,
    usable_encoders: set[str] | None = None,
) -> EncoderChoice:
    """Select a detected *and initialized* H.264 encoder."""

    supported = available_encoders(ffmpeg) if encoders is None else encoders
    warnings: list[str] = []

    def usable(encoder: str) -> bool:
        if usable_encoders is not None:
            return encoder in usable_encoders
        ok, failure, _ = probe_encoder_usable(ffmpeg, encoder)
        if not ok:
            warnings.append(f"{encoder} is listed but unusable: {failure}")
        return ok

    if requested in {"none", "software", "libx264"}:
        return EncoderChoice("libx264", "none")
    if requested == "auto":
        for hardware in ("nvenc", "qsv", "amf", "videotoolbox"):
            encoder = HARDWARE_ENCODERS[hardware]
            if usable_encoders is None and not hardware_device_present(hardware):
                continue
            if encoder in supported and usable(encoder):
                return EncoderChoice(encoder, hardware, tuple(warnings))
        return EncoderChoice("libx264", "none", tuple(warnings))
    if requested not in HARDWARE_ENCODERS:
        raise ValueError(
            "hardware must be auto, nvenc, qsv, amf, videotoolbox, or none"
        )
    encoder = HARDWARE_ENCODERS[requested]
    if encoder in supported and usable(encoder):
        return EncoderChoice(encoder, requested, tuple(warnings))
    return EncoderChoice(
        "libx264",
        "none",
        tuple([*warnings, f"Hardware encoder {encoder} is unavailable; falling back to libx264."]),
    )
