"""Safe subprocess helpers for FFmpeg and FFprobe."""

from __future__ import annotations

import os
import shutil
import subprocess
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
    """Resolve a media executable without invoking a shell."""

    configured = explicit or os.environ.get(f"FACUT_{name.upper()}")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        resolved = shutil.which(str(configured))
        if resolved:
            return resolved
    resolved = shutil.which(name)
    if not resolved:
        raise MediaDependencyError(
            f"{name} was not found.",
            suggestion=f"Install {name} or set FACUT_{name.upper()} to its executable path.",
        )
    return resolved


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
