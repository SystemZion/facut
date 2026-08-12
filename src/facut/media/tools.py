"""Safe subprocess helpers for FFmpeg and FFprobe."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import re
from functools import lru_cache
from pathlib import Path
from typing import Sequence


class MediaToolError(RuntimeError):
    code = "MEDIA_TOOL_ERROR"
    exit_code = 4

    def __init__(
        self,
        message: str,
        *,
        command: Sequence[str] | None = None,
        stderr: str = "",
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = list(command or ())
        self.stderr = stderr
        self.suggestion = suggestion


class MediaDependencyError(MediaToolError):
    code = "DEPENDENCY_MISSING"
    exit_code = 7


def find_executable(name: str, explicit: str | Path | None = None) -> str:
    """Resolve a modern media executable using FACUT's stable priority order.

    Order: explicit configuration/environment, embedded executable, portable
    install directory, reviewed source-checkout tool, then system PATH.
    """

    configured = explicit or os.environ.get(f"FACUT_{name.upper()}")
    candidates: list[tuple[str, Path]] = []
    executable = f"{name}.exe" if os.name == "nt" else name
    if configured and str(configured).strip().casefold() not in {
        "auto", name.casefold(), executable.casefold()
    }:
        path = Path(configured).expanduser()
        if path.is_file():
            candidates.append(("explicit", path))
        elif resolved := shutil.which(str(configured)):
            candidates.append(("explicit", Path(resolved)))
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(("embedded", Path(bundle_root) / "facut_bin" / executable))
    if getattr(sys, "frozen", False):
        candidates.append(("install", Path(sys.executable).resolve().parent / "facut_bin" / executable))
    candidates.append(("source-vendor", Path(__file__).resolve().parents[3] / "vendor" / "ffmpeg" / executable))
    if resolved := shutil.which(name):
        candidates.append(("path", Path(resolved)))
    rejected: list[str] = []
    for _, candidate in candidates:
        if not candidate.is_file():
            continue
        resolved_candidate = str(candidate.resolve())
        if name in {"ffmpeg", "ffprobe"} and not _modern_media_tool(resolved_candidate):
            rejected.append(resolved_candidate)
            continue
        return resolved_candidate
    if rejected:
        raise MediaDependencyError(
            f"No supported {name} executable was found; legacy builds were rejected.",
            suggestion="Use FACUT's bundled FFmpeg 5+ or configure a modern executable.",
        )
    else:
        raise MediaDependencyError(
            f"{name} was not found.",
            suggestion=f"Install {name} or set FACUT_{name.upper()} to its executable path.",
        )


@lru_cache(maxsize=16)
def _modern_media_tool(executable: str) -> bool:
    """Return whether an FFmpeg-family executable meets FACUT's baseline."""

    try:
        result = subprocess.run(
            [executable, "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    first = (result.stdout or result.stderr).splitlines()
    match = re.search(r"(?:ffmpeg|ffprobe) version\s+(\d+)", first[0] if first else "", re.I)
    return result.returncode == 0 and match is not None and int(match.group(1)) >= 5


def run_tool(
    arguments: Sequence[str | Path],
    *,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a prepared argv list and capture text output."""

    argv = [str(argument) for argument in arguments]
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError(
            f"{Path(argv[0]).name} timed out.",
            command=argv,
            suggestion="Retry with a longer timeout or inspect the input file.",
        ) from exc
    if check and result.returncode != 0:
        message = _friendly_message(result.stderr)
        raise MediaToolError(message, command=argv, stderr=result.stderr)
    return result


def ensure_output_available(output: str | Path, overwrite: bool) -> Path:
    path = Path(output).expanduser().resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f'Output "{path}" already exists. Use --overwrite to replace it.')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _friendly_message(stderr: str) -> str:
    lower = stderr.lower()
    if "no such file or directory" in lower:
        return "An input file does not exist."
    if "unknown encoder" in lower or "encoder not found" in lower:
        return "The requested encoder is unavailable."
    if "invalid argument" in lower:
        return "FFmpeg rejected an input, timestamp, or filter argument."
    if "permission denied" in lower:
        return "The media file could not be opened because access was denied."
    if "no space left on device" in lower:
        return "There is not enough free disk space."
    tail = [line.strip() for line in stderr.splitlines() if line.strip()]
    return tail[-1] if tail else "The media tool failed."
