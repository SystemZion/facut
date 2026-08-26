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
        if not path.is_file():
            return None
        digest_path = path.with_suffix(path.suffix + ".sha256")
        if path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            digest_path.unlink(missing_ok=True)
            return None
        actual = self._sha256(path)
        if digest_path.is_file():
            expected = digest_path.read_text(encoding="ascii", errors="ignore").strip()
            if expected != actual:
                path.unlink(missing_ok=True)
                digest_path.unlink(missing_ok=True)
                return None
        else:
            # Upgrade a valid legacy entry lazily. Entries remain immutable;
            # the sidecar records what was already present rather than changing
            # the media payload.
            digest_path.write_text(actual + "\n", encoding="ascii")
        return path

    def commit(self, path: str | Path) -> Path:
        """Record the digest of one fully rendered, non-empty cache artifact."""

        artifact = Path(path)
        if not artifact.is_file() or artifact.stat().st_size <= 0:
            raise ValueError("Cannot commit a missing or empty render cache artifact")
        sidecar = artifact.with_suffix(artifact.suffix + ".sha256")
        sidecar.write_text(self._sha256(artifact) + "\n", encoding="ascii")
        return artifact

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
