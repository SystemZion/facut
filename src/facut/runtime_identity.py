"""Runtime/build identity and PATH shadowing diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from facut import __version__


def _manifest_path() -> Path | None:
    candidates = [Path(sys.executable).resolve().parent / "facut-build.json"]
    candidates.append(Path(__file__).resolve().parent / "facut-build.json")
    return next((item for item in candidates if item.is_file()), None)


def path_candidates() -> list[Path]:
    """Return every FACUT executable visible through PATH, in precedence order."""

    command = ["where.exe", "facut"] if os.name == "nt" else ["which", "-a", "facut"]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=3, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        located = shutil.which("facut")
        return [Path(located).resolve()] if located else []
    result: list[Path] = []
    for line in completed.stdout.splitlines():
        path = Path(line.strip())
        if path.is_file() and path.resolve() not in result:
            result.append(path.resolve())
    return result


def runtime_identity() -> dict[str, Any]:
    manifest_path = _manifest_path()
    manifest: dict[str, Any] = {}
    if manifest_path is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            manifest = {"invalid": True}
    executable = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    candidates = path_candidates()
    shadowed = bool(executable and candidates and candidates[0] != executable)
    return {
        "version": __version__,
        "commit": manifest.get("commit"),
        "executable_sha256": manifest.get("executable_sha256"),
        "build_time_utc": manifest.get("build_time_utc"),
        "executable": str(executable) if executable else None,
        "manifest": str(manifest_path) if manifest_path else None,
        "path_candidates": [str(item) for item in candidates],
        "path_shadowed": shadowed,
        "path_winner": str(candidates[0]) if candidates else None,
    }


def version_label() -> str:
    identity = runtime_identity()
    commit = str(identity.get("commit") or "").strip()
    return f"facut {__version__}" + (f" (commit {commit[:12]})" if commit else "")


__all__ = ["path_candidates", "runtime_identity", "version_label"]
