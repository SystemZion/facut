"""SQLite/FTS based, license-gated Music Library v2."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote_plus
from uuid import uuid4

from platformdirs import user_data_path

from facut.analysis.engine import analyze_beats
from facut.exceptions import InvalidArgumentError, LicenseReviewRequiredError, ResourceNotFoundError, ReviewRequiredError
from facut.media.probe import probe_media
from facut.media.tools import find_executable
from facut.render.loudness import measure_loudness


AUDIO_SUFFIXES = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus"}
TAG_DIMENSIONS = (
    "mood", "genre", "energy", "pacing", "scene_role", "edit_behavior",
    "cultural_tone", "instrument", "vocal_type",
)
SCENE_ROLES = (
    "opening", "arrival", "exploration", "humanities", "landscape",
    "comedy-setup", "comedy-failure", "recovery", "climax", "reflection", "ending",
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _structure(duration: float, beats: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Create transparent low-confidence technical regions, never semantic truth."""

    if duration <= 0:
        return [], [], []
    windows = 8
    width = duration / windows
    counts = [0] * windows
    for beat in beats:
        index = min(windows - 1, max(0, int(float(beat.get("time", 0)) / max(width, 1e-9))))
        counts[index] += 1
    maximum = max(counts) if counts else 0
    energy = [
        {"start": round(index * width, 6), "end": round(min(duration, (index + 1) * width), 6),
         "value": round(count / maximum, 4) if maximum else 0.0, "method": "beat-density"}
        for index, count in enumerate(counts)
    ]
    climax_index = counts.index(maximum) if maximum else max(0, windows // 2)
    sections = [
        {"role": "intro", "start": 0.0, "end": round(min(duration, width), 6), "confidence": 0.35, "review_status": "candidate"},
        {"role": "climax", "start": round(climax_index * width, 6), "end": round(min(duration, (climax_index + 1) * width), 6), "confidence": 0.35, "review_status": "candidate"},
        {"role": "outro", "start": round(max(0.0, duration - width), 6), "end": round(duration, 6), "confidence": 0.35, "review_status": "candidate"},
    ]
    loops = []
    if duration >= 16:
        loops.append({"start": round(width, 6), "end": round(duration - width, 6), "method": "technical-candidate", "review_status": "candidate"})
    return energy, sections, loops


class MusicCatalog:
    """Reference user audio in place and index it without modifying originals."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path(user_data_path("facut", appauthor=False)) / "library"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "music-v2.sqlite3"
        self._initialize()
        self._migrate_legacy()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS music_assets (
                    id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0, file_mtime_ns INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL, author TEXT, source TEXT NOT NULL,
                    source_url TEXT, acquired_at TEXT, duration REAL NOT NULL,
                    sample_rate INTEGER, channels INTEGER, lufs REAL, true_peak REAL,
                    bpm REAL, beats_json TEXT NOT NULL, energy_json TEXT NOT NULL,
                    sections_json TEXT NOT NULL, loops_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL, vocal_type TEXT,
                    license_status TEXT NOT NULL, license_type TEXT,
                    license_text TEXT, license_snapshot TEXT, license_sha256 TEXT,
                    platforms_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, query TEXT NOT NULL,
                    url TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_log (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS music_fts USING fts5(
                    id UNINDEXED, title, author, tags, source
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(music_assets)")}
            if "file_size" not in columns:
                db.execute("ALTER TABLE music_assets ADD COLUMN file_size INTEGER NOT NULL DEFAULT 0")
            if "file_mtime_ns" not in columns:
                db.execute("ALTER TABLE music_assets ADD COLUMN file_mtime_ns INTEGER NOT NULL DEFAULT 0")

    def _migrate_legacy(self) -> None:
        legacy = self.root / "catalog.json"
        if not legacy.is_file():
            return
        with self.connect() as db:
            done = db.execute("SELECT 1 FROM migration_log WHERE key='catalog-json-v1'").fetchone()
            if done:
                return
        backup = self.root / "catalog.v1.backup.json"
        if not backup.exists():
            shutil.copy2(legacy, backup)
        payload = json.loads(legacy.read_text(encoding="utf-8-sig"))
        for item in payload.get("assets", []):
            if item.get("kind") != "music":
                continue
            path = Path(str(item.get("path", "")))
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            tags = {dimension: [] for dimension in TAG_DIMENSIONS}
            tags["mood"] = list(item.get("moods", []))
            tags["edit_behavior"] = list(item.get("tags", []))
            self._upsert(
                {
                    "id": item.get("id") or f"music_{item['sha256'][:16].upper()}",
                    "sha256": item["sha256"], "path": str(path.resolve()),
                    "file_size": path.stat().st_size, "file_mtime_ns": path.stat().st_mtime_ns,
                    "title": item.get("name") or path.name, "author": None,
                    "source": item.get("source_type", "user-local"), "source_url": None,
                    "acquired_at": None, "duration": float(item.get("duration") or 0),
                    "sample_rate": None, "channels": None, "lufs": None, "true_peak": None,
                    "bpm": item.get("tempo_bpm"), "beats": item.get("beats", []),
                    "energy_curve": [], "sections": [], "loop_regions": [], "tags": tags,
                    "vocal_type": None, "license_status": (
                        "verified" if item.get("license_status") == "verified-file" else "review_required"
                    ), "license_type": None, "license_text": None,
                    "license_snapshot": item.get("license_file"),
                    "license_sha256": item.get("license_sha256"),
                    "platforms": item.get("platforms", []),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO migration_log(key,value) VALUES('catalog-json-v1',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def _upsert(self, asset: dict[str, Any]) -> dict[str, Any]:
        columns = {
            "id": asset["id"], "sha256": asset["sha256"], "path": asset["path"],
            "file_size": asset.get("file_size", 0), "file_mtime_ns": asset.get("file_mtime_ns", 0),
            "title": asset["title"], "author": asset.get("author"), "source": asset["source"],
            "source_url": asset.get("source_url"), "acquired_at": asset.get("acquired_at"),
            "duration": asset["duration"], "sample_rate": asset.get("sample_rate"),
            "channels": asset.get("channels"), "lufs": asset.get("lufs"),
            "true_peak": asset.get("true_peak"), "bpm": asset.get("bpm"),
            "beats_json": _json(asset.get("beats", [])),
            "energy_json": _json(asset.get("energy_curve", [])),
            "sections_json": _json(asset.get("sections", [])),
            "loops_json": _json(asset.get("loop_regions", [])),
            "tags_json": _json(asset.get("tags", {})), "vocal_type": asset.get("vocal_type"),
            "license_status": asset["license_status"], "license_type": asset.get("license_type"),
            "license_text": asset.get("license_text"),
            "license_snapshot": asset.get("license_snapshot"),
            "license_sha256": asset.get("license_sha256"),
            "platforms_json": _json(asset.get("platforms", [])),
            "created_at": asset["created_at"], "updated_at": asset["updated_at"],
        }
        with self.connect() as db:
            names = ",".join(columns)
            placeholders = ",".join("?" for _ in columns)
            updates = ",".join(f"{name}=excluded.{name}" for name in columns if name not in {"id", "created_at"})
            db.execute(
                f"INSERT INTO music_assets({names}) VALUES({placeholders}) "
                f"ON CONFLICT(sha256) DO UPDATE SET {updates}", tuple(columns.values()),
            )
            db.execute("DELETE FROM music_fts WHERE id=?", (asset["id"],))
            db.execute(
                "INSERT INTO music_fts(id,title,author,tags,source) VALUES(?,?,?,?,?)",
                (asset["id"], asset["title"], asset.get("author") or "", _json(asset.get("tags", {})), asset["source"]),
            )
        return self.get(asset["id"])

    def ingest_file(
        self, source: str | Path, *, ffmpeg: str | Path | None = None,
        ffprobe: str | Path | None = None, tags: dict[str, list[str]] | None = None,
        platforms: list[str] | None = None, license_file: str | Path | None = None,
        license_type: str | None = None, license_text: str | None = None,
        author: str | None = None, source_name: str = "user-local",
        source_url: str | None = None, analyze: bool = True,
    ) -> dict[str, Any]:
        path = Path(source).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ResourceNotFoundError(f'Audio asset "{path}" was not found or is empty.')
        if path.suffix.casefold() not in AUDIO_SUFFIXES:
            raise InvalidArgumentError(f'Unsupported music file "{path.name}".')
        by_path = self.get_by_path(path)
        if by_path is not None:
            stat = path.stat()
            if stat.st_size == by_path.get("file_size") and stat.st_mtime_ns == by_path.get("file_mtime_ns"):
                return {**by_path, "reused": True}
        info = probe_media(path, ffprobe=ffprobe)
        if not info.audio_codec:
            raise InvalidArgumentError(f'Asset "{path.name}" has no audio stream.')
        digest = _hash(path)
        existing = self.get_by_hash(digest)
        if existing:
            return {**existing, "reused": True}
        license_path = Path(license_file).expanduser().resolve() if license_file else None
        if license_path is not None and not license_path.is_file():
            raise ResourceNotFoundError(f'License file "{license_path}" was not found.')
        normalized_tags = {dimension: [] for dimension in TAG_DIMENSIONS}
        for dimension, values in (tags or {}).items():
            if dimension not in TAG_DIMENSIONS:
                raise InvalidArgumentError(f'Unknown music tag dimension "{dimension}".')
            normalized_tags[dimension] = sorted(set(str(value) for value in values))
        beat = analyze_beats(path, ffmpeg=ffmpeg) if analyze else {"beats": [], "tempo_bpm": None}
        loudness = {"input_i": None, "input_tp": None}
        if analyze and info.duration:
            try:
                loudness = measure_loudness(
                    find_executable("ffmpeg", ffmpeg), path, duration=float(info.duration),
                    target_lufs=-14.0, true_peak=-1.0, loudness_range=11.0,
                    progress=None, log_directory=self.root / "analysis-logs",
                )
            except Exception:
                # A valid catalog entry is still useful when loudness analysis
                # fails; null remains explicit and final QC must measure again.
                loudness = {"input_i": None, "input_tp": None}
        energy_curve, sections, loop_regions = _structure(
            float(info.duration or 0), list(beat.get("beats", []))
        )
        now = datetime.now(timezone.utc).isoformat()
        asset = {
            "id": f"music_{digest[:16].upper()}", "sha256": digest, "path": str(path),
            "file_size": path.stat().st_size, "file_mtime_ns": path.stat().st_mtime_ns,
            "title": path.stem, "author": author, "source": source_name,
            "source_url": source_url, "acquired_at": now, "duration": float(info.duration or 0),
            "sample_rate": info.sample_rate, "channels": info.audio_channels,
            "lufs": loudness.get("input_i"), "true_peak": loudness.get("input_tp"), "bpm": beat.get("tempo_bpm"),
            "beats": beat.get("beats", []), "energy_curve": energy_curve, "sections": sections,
            "loop_regions": loop_regions, "tags": normalized_tags,
            "vocal_type": (normalized_tags["vocal_type"][0] if normalized_tags["vocal_type"] else None),
            "license_status": "verified" if license_path or license_text else "review_required",
            "license_type": license_type, "license_text": license_text,
            "license_snapshot": str(license_path) if license_path else None,
            "license_sha256": _hash(license_path) if license_path else None,
            "platforms": sorted(set(platforms or [])), "created_at": now, "updated_at": now,
        }
        return {**self._upsert(asset), "reused": False}

    def ingest_directory(self, source: str | Path, *, recursive: bool = False, progress: Callable[[dict[str, Any]], None] | None = None, **kwargs: Any) -> dict[str, Any]:
        root = Path(source).expanduser().resolve()
        if not root.is_dir():
            raise ResourceNotFoundError(f'Music directory "{root}" was not found.')
        iterator: Iterable[Path] = root.rglob("*") if recursive else root.glob("*")
        files = sorted(path for path in iterator if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES)
        task_seed = f"{root}\0{recursive}".encode("utf-8")
        task_id = f"music-ingest-{hashlib.sha256(task_seed).hexdigest()[:16]}"
        results, errors = [], []
        for index, path in enumerate(files, 1):
            try:
                results.append(self.ingest_file(path, **kwargs))
            except Exception as error:  # batch import records errors and continues
                errors.append({"path": str(path), "error": str(error)})
            if progress:
                progress({"event": "progress", "stage": "music_ingest", "task_id": task_id, "completed": index, "total": len(files), "progress": index / max(1, len(files)), "current": str(path)})
        return {"task_id": task_id, "resumable": True, "status": "completed" if not errors else "completed_with_errors", "total": len(files), "ingested": len(results), "failed": len(errors), "results": results, "errors": errors}

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key, target in (("beats_json", "beats"), ("energy_json", "energy_curve"), ("sections_json", "sections"), ("loops_json", "loop_regions"), ("tags_json", "tags"), ("platforms_json", "platforms")):
            item[target] = json.loads(item.pop(key))
        return item

    def get(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM music_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError(f'Music asset "{asset_id}" was not found.')
        return self._row(row)

    def get_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM music_assets WHERE sha256=?", (digest,)).fetchone()
        return self._row(row) if row else None

    def get_by_path(self, path: str | Path) -> dict[str, Any] | None:
        resolved = str(Path(path).expanduser().resolve())
        with self.connect() as db:
            row = db.execute("SELECT * FROM music_assets WHERE path=?", (resolved,)).fetchone()
        return self._row(row) if row else None

    def find(
        self, query: str, *, style: str | None = None, scene: str | None = None,
        duration: float | None = None, platforms: list[str] | None = None, top: int = 3,
        offset: int = 0,
    ) -> dict[str, Any]:
        if scene and scene not in SCENE_ROLES:
            raise InvalidArgumentError(f'Unknown scene role "{scene}".')
        with self.connect() as db:
            rows = []
            normalized_terms = [
                term.replace('"', '""')
                for term in query.replace("，", " ").replace(",", " ").split()
                if term
            ]
            if normalized_terms:
                expression = " OR ".join(f'"{term}"' for term in normalized_terms)
                rows = db.execute(
                    "SELECT m.* FROM music_assets m JOIN music_fts f ON f.id=m.id "
                    "WHERE music_fts MATCH ? ORDER BY m.title COLLATE NOCASE",
                    (expression,),
                ).fetchall()
            # Natural-language queries may not share literal words with externally
            # supplied tags.  Falling back keeps scene/style filters useful while
            # still making FTS the normal path for large literal-tag searches.
            if not rows:
                rows = db.execute("SELECT * FROM music_assets ORDER BY title COLLATE NOCASE").fetchall()
        results = []
        try:
            from facut.director.taste import TasteStore
            preferences = TasteStore().load().get("preferences", [])
        except (OSError, ValueError):
            preferences = []
        terms = {term.casefold() for term in query.replace("，", " ").replace(",", " ").split() if term}
        for row in rows:
            item = self._row(row)
            path = Path(item["path"])
            reasons, blockers = [], []
            if not path.is_file() or path.stat().st_size <= 0:
                blockers.append("asset_offline")
            else:
                stat = path.stat()
                if stat.st_size != item.get("file_size") or stat.st_mtime_ns != item.get("file_mtime_ns"):
                    if _hash(path) != item["sha256"]:
                        blockers.append("hash_changed")
            requested_platforms = platforms or []
            if item["license_status"] != "verified":
                blockers.append("license_review_required")
            for platform in requested_platforms:
                if platform not in item["platforms"]:
                    blockers.append(f"platform_not_authorized:{platform}")
            approved_loops = [loop for loop in item["loop_regions"] if loop.get("review_status") == "approved"]
            if duration is not None and item["duration"] + 1e-6 < duration and not approved_loops:
                blockers.append("duration_or_loop_insufficient")
            if blockers:
                continue
            haystack = " ".join([item["title"], item.get("author") or "", _json(item["tags"])]).casefold()
            score = sum(1.0 for term in terms if term in haystack)
            if scene and scene in item["tags"].get("scene_role", []):
                score += 4.0; reasons.append(f"scene_role={scene}")
            if style and style in item["tags"].get("edit_behavior", []):
                score += 2.0; reasons.append(f"style={style}")
            for preference in preferences:
                applies = preference.get("applies_to", [])
                if applies and style not in applies:
                    continue
                category = str(preference.get("category", ""))
                value = str(preference.get("value", ""))
                if category in TAG_DIMENSIONS and value in item["tags"].get(category, []):
                    score += 1.0; reasons.append(f"remembered_preference={preference['id']}")
            if terms and score:
                reasons.append("query_tags_matched")
            if duration is not None:
                reasons.append("duration_covered" if item["duration"] >= duration else "reviewed_loop_available")
            if requested_platforms:
                reasons.append("platform_license_verified")
            results.append({**item, "score": round(score, 3), "match_reasons": reasons or ["eligible_catalog_asset"]})
        results.sort(key=lambda item: (-item["score"], item["title"].casefold()))
        page_size = max(1, min(top, 100))
        selected = results[max(0, offset) : max(0, offset) + page_size]
        next_offset = max(0, offset) + len(selected)
        return {"version": "music_query.v1", "query": query, "count": len(selected), "total_eligible": len(results), "offset": max(0, offset), "next_offset": next_offset if next_offset < len(results) else None, "results": selected}

    def audit(self, platforms: list[str]) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM music_assets ORDER BY title COLLATE NOCASE").fetchall()
        issues = []
        for row in rows:
            item = self._row(row)
            path = Path(item["path"])
            if not path.is_file() or path.stat().st_size <= 0:
                issues.append({"code": "ASSET_OFFLINE", "asset_id": item["id"]})
            elif _hash(path) != item["sha256"]:
                issues.append({"code": "ASSET_HASH_CHANGED", "asset_id": item["id"]})
            if item["license_status"] != "verified":
                issues.append({"code": "LICENSE_REVIEW_REQUIRED", "asset_id": item["id"]})
            if item.get("license_snapshot"):
                license_path = Path(item["license_snapshot"])
                if not license_path.is_file():
                    issues.append({"code": "LICENSE_EVIDENCE_OFFLINE", "asset_id": item["id"]})
                elif item.get("license_sha256") and _hash(license_path) != item["license_sha256"]:
                    issues.append({"code": "LICENSE_EVIDENCE_CHANGED", "asset_id": item["id"]})
            for platform in platforms:
                if platform not in item["platforms"]:
                    issues.append({"code": "PLATFORM_NOT_AUTHORIZED", "asset_id": item["id"], "platform": platform})
        return {"status": "pass" if not issues else "fail", "platforms": platforms, "asset_count": len(rows), "issues": issues}

    def create_source_session(self, source: str, query: str) -> dict[str, Any]:
        urls = {
            "pixabay": f"https://pixabay.com/music/search/{quote_plus(query)}/",
            "youtube-audio-library": "https://studio.youtube.com/channel/UC/music",
        }
        if source not in urls:
            raise InvalidArgumentError(f'Unknown music source "{source}".')
        session = {
            "id": f"music-source-{uuid4().hex[:16]}", "source": source, "query": query,
            "url": urls[source], "status": "source_interaction_required",
            "code": "SOURCE_INTERACTION_REQUIRED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "automation": "visible_browser_only", "hidden_api": False,
                "next_action": "Open the URL in Edge or Chrome, use visible controls, and download a candidate.",
            },
        }
        with self.connect() as db:
            db.execute("INSERT INTO source_sessions VALUES(?,?,?,?,?,?,?)", (
                session["id"], source, query, session["url"], session["status"], session["created_at"], _json(session["metadata"])
            ))
        return session

    def source_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError(f'Music source session "{session_id}" was not found.')
        item = dict(row); item["metadata"] = json.loads(item.pop("metadata_json")); return item

    def import_source_download(self, source: str | Path, session_id: str, **kwargs: Any) -> dict[str, Any]:
        session = self.source_session(session_id)
        if not kwargs.get("license_file") and not kwargs.get("license_text"):
            raise LicenseReviewRequiredError(
                "Downloaded music has no captured license evidence.",
                suggestion="Save the visible license text or license file, then import again.",
                details={"session_id": session_id},
            )
        platforms = list(kwargs.get("platforms") or [])
        license_type = str(kwargs.get("license_type") or "").casefold()
        if session["source"] == "youtube-audio-library":
            if not platforms:
                kwargs["platforms"] = ["youtube"]
            elif any(platform != "youtube" for platform in platforms) and license_type not in {
                "cc-by", "creative-commons-attribution",
            }:
                raise LicenseReviewRequiredError(
                    "YouTube Audio Library rights were not proven for a non-YouTube platform.",
                    suggestion="Restrict the asset to youtube or capture explicit cross-platform license evidence.",
                    details={"platforms": platforms},
                )
        return self.ingest_file(
            source, source_name=session["source"], source_url=session["url"], **kwargs
        )

    def plan(self, candidate_id: str, *, start: float = 0.0, duration: float | None = None) -> dict[str, Any]:
        item = self.get(candidate_id)
        wanted = float(duration if duration is not None else item["duration"])
        if wanted <= 0:
            raise InvalidArgumentError("Music cue duration must be positive.")
        approved_loops = [loop for loop in item["loop_regions"] if loop.get("review_status") == "approved"]
        if item["duration"] < wanted and not approved_loops:
            raise ReviewRequiredError(
                "Music window exceeds the track and no reviewed loop region exists.",
                suggestion="Choose a shorter window, define a loop, or select another track.",
            )
        if item["license_status"] != "verified":
            raise LicenseReviewRequiredError("Music license is not verified for final delivery.")
        segments = []
        remaining = wanted
        timeline_at = start
        first_duration = min(item["duration"], remaining)
        segments.append({"timeline_start": timeline_at, "source_in": 0.0, "source_out": first_duration})
        remaining -= first_duration; timeline_at += first_duration
        while remaining > 1e-6:
            loop = approved_loops[0]
            loop_start, loop_end = float(loop["start"]), float(loop["end"])
            length = min(remaining, loop_end - loop_start)
            if length <= 0:
                raise ReviewRequiredError("Approved music loop has an invalid range.")
            segments.append({"timeline_start": timeline_at, "source_in": loop_start, "source_out": loop_start + length})
            timeline_at += length; remaining -= length
        return {
            "version": "music_cue_plan.v1", "id": f"music-plan-{uuid4().hex[:12]}",
            "status": "review_required", "asset_id": item["id"], "timeline_start": start,
            "duration": wanted, "source_in": 0.0, "loop": item["duration"] < wanted,
            "segments": segments,
            "crossfade_seconds": 0.0, "preserve_original_audio": True,
            "approved": False, "license_status": item["license_status"],
        }

    def export_json(self, output: str | Path, *, overwrite: bool = False) -> Path:
        destination = Path(output).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise InvalidArgumentError(f'Output "{destination}" already exists.')
        with self.connect() as db:
            assets = [self._row(row) for row in db.execute("SELECT * FROM music_assets ORDER BY id")]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp") as stream:
            json.dump({"version": "music_asset.v2", "assets": assets}, stream, ensure_ascii=False, indent=2)
            stream.write("\n"); temporary = Path(stream.name)
        os.replace(temporary, destination)
        return destination


def prepare_music_plan_apply(
    manager: Any, plan: str | Path | dict[str, Any], *, approved_only: bool = True
) -> dict[str, Any]:
    """Resolve an approved cue plan before entering a project transaction."""

    data = (
        json.loads(Path(plan).expanduser().resolve().read_text(encoding="utf-8-sig"))
        if isinstance(plan, (str, Path))
        else dict(plan)
    )
    if data.get("version") != "music_cue_plan.v1":
        raise InvalidArgumentError("Music plan must use music_cue_plan.v1.")
    if approved_only and not data.get("approved"):
        raise ReviewRequiredError("Music plan is not explicitly approved.")
    asset = MusicCatalog().get(str(data["asset_id"]))
    if asset["license_status"] != "verified":
        raise ReviewRequiredError("Music license is not verified for final delivery.")
    document = manager.require_document()
    media = next(
        (
            item for item in document.media
            if manager.resolve_path(item.path) == Path(asset["path"]).expanduser().resolve()
        ),
        None,
    )
    if media is None:
        raise ReviewRequiredError(
            "Approved music is not imported into this project.",
            suggestion=f"Run `facut import \"{asset['path']}\"`, then apply the plan again.",
        )
    track = next(
        (item for item in document.tracks if item.metadata.get("role") == "music"), None
    )
    if track is None:
        raise ReviewRequiredError(
            "Project has no music-role audio track.",
            suggestion="Create an audio track and set metadata role=music before applying.",
        )
    segments = data.get("segments") or [{
        "timeline_start": data["timeline_start"],
        "source_in": data.get("source_in", 0),
        "source_out": min(asset["duration"], data["duration"]),
    }]
    commands = [
        {
            "action": "timeline.add", "media_id": media.id, "track": track.id,
            "at": segment["timeline_start"], "in": segment["source_in"],
            "out": segment["source_out"],
        }
        for segment in segments
    ]
    return {
        "action": "library.music.apply.prepared",
        "plan_id": data["id"],
        "asset_id": asset["id"],
        "commands": commands,
        # This plan currently supports one cue (possibly repeated via a reviewed
        # loop). It does not claim a crossfade that the timeline has not applied.
        "crossfade_seconds": 0.0,
    }


def apply_prepared_music_plan(document: Any, command: dict[str, Any]) -> dict[str, Any]:
    """Apply prevalidated cue segments to an in-memory project document."""

    from facut.core.command_engine import CommandEngine

    results = [CommandEngine.apply(document, item) for item in command["commands"]]
    return {
        "plan_id": command["plan_id"],
        "asset_id": command["asset_id"],
        "clips": results,
        "crossfade_seconds": command["crossfade_seconds"],
    }


def apply_music_plan(
    manager: Any, plan: str | Path | dict[str, Any], *, approved_only: bool = True
) -> dict[str, Any]:
    """Apply one licensed cue as a single atomic and undoable revision."""

    prepared = prepare_music_plan_apply(manager, plan, approved_only=approved_only)
    result, state = manager.mutate(
        "library.music.apply",
        f"Applied approved music plan {prepared['plan_id']}",
        lambda document: apply_prepared_music_plan(document, prepared),
        command={
            "action": "library.music.apply", "plan_id": prepared["plan_id"],
            "_actor": "user", "_intent": "Apply explicitly approved licensed music cue",
        },
    )
    return {**result, "project_revision": state.revision}
