"""Hash-addressed local audio catalog with platform license auditing."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from platformdirs import user_data_path

from facut.analysis.engine import analyze_beats
from facut.media.probe import probe_media


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


class MediaLibrary:
    """Reference user-owned assets in place; never copy or modify source audio."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path(user_data_path("facut", appauthor=False)) / "library"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "catalog.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": "1.0", "assets": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def add(
        self,
        source: str | Path,
        *,
        kind: Literal["music", "sfx"],
        tags: list[str] | None = None,
        moods: list[str] | None = None,
        platforms: list[str] | None = None,
        license_file: str | Path | None = None,
        ffmpeg: str | Path | None = None,
        ffprobe: str | Path | None = None,
        analyze: bool = True,
    ) -> dict[str, Any]:
        path = Path(source).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f'Audio asset "{path}" was not found or is empty.')
        technical = probe_media(path, ffprobe=ffprobe)
        if not technical.audio_codec:
            raise ValueError(f'Asset "{path.name}" has no audio stream.')
        license_path = Path(license_file).expanduser().resolve() if license_file else None
        if license_path is not None and not license_path.is_file():
            raise FileNotFoundError(f'License file "{license_path}" was not found.')
        digest = _hash(path)
        beat_data = (
            analyze_beats(path, ffmpeg=ffmpeg)
            if kind == "music" and analyze
            else {"beats": [], "tempo_bpm": None}
        )
        record = {
            "id": f"library_{digest[:16].upper()}",
            "kind": kind,
            "path": str(path),
            "name": path.name,
            "sha256": digest,
            "duration": technical.duration,
            "tags": sorted(set(tags or [])),
            "moods": sorted(set(moods or [])),
            "platforms": sorted(set(platforms or [])),
            "license_status": "verified-file" if license_path else "unverified",
            "license_file": str(license_path) if license_path else None,
            "license_sha256": _hash(license_path) if license_path else None,
            "tempo_bpm": beat_data.get("tempo_bpm"),
            "beats": beat_data.get("beats", []),
            "source_type": "user-local",
        }
        catalog = self.load()
        catalog["assets"] = [item for item in catalog["assets"] if item["sha256"] != digest]
        catalog["assets"].append(record)
        catalog["assets"].sort(key=lambda item: (item["kind"], item["name"].casefold()))
        _atomic(self.path, catalog)
        return record

    def search(
        self,
        *,
        kind: str | None = None,
        mood: str | None = None,
        tag: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        results = []
        for item in self.load()["assets"]:
            if kind and item["kind"] != kind:
                continue
            if mood and mood not in item["moods"]:
                continue
            if tag and tag not in item["tags"]:
                continue
            if platform and platform not in item["platforms"]:
                continue
            results.append(item)
        return {"count": len(results), "results": results}

    def audit(self, platform: str) -> dict[str, Any]:
        issues = []
        for item in self.load()["assets"]:
            if item["license_status"] != "verified-file":
                issues.append({"code": "LICENSE_UNVERIFIED", "asset_id": item["id"]})
            if platform not in item["platforms"]:
                issues.append(
                    {"code": "PLATFORM_NOT_AUTHORIZED", "asset_id": item["id"], "platform": platform}
                )
            if not Path(item["path"]).is_file():
                issues.append({"code": "ASSET_OFFLINE", "asset_id": item["id"]})
        return {
            "status": "pass" if not issues else "fail",
            "platform": platform,
            "asset_count": len(self.load()["assets"]),
            "issues": issues,
        }
