"""Content-addressed render cache utilities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def cache_key(
    *,
    inputs: list[str | Path],
    parameters: dict[str, Any],
    software_version: str,
    ffmpeg_version: str,
) -> str:
    """Build a stable cache key from sources and all render-affecting state."""

    source_state = []
    for item in inputs:
        path = Path(item)
        stat = path.stat()
        source_state.append(
            {
                "path": str(path.resolve()),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    payload = {
        "inputs": source_state,
        "parameters": parameters,
        "software_version": software_version,
        "ffmpeg_version": ffmpeg_version,
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RenderCache:
    """Simple file cache whose entries are immutable once committed."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, key: str, suffix: str = ".mp4") -> Path:
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid cache key")
        return self.root / key[:2] / f"{key}{suffix}"

    def lookup(self, key: str, suffix: str = ".mp4") -> Path | None:
        path = self.path_for(key, suffix)
        return path if path.is_file() else None
