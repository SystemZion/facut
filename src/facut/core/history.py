"""Revision snapshots and undo/redo support."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import HistoryEntry, ProjectDocument


class HistoryStore:
    """Persist full project snapshots using small, atomic JSON files.

    Full snapshots are deliberately used in v1: they are robust, inspectable and
    avoid replaying partially understood commands.  A future SQLite backend can
    implement the same interface.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.history_dir = self.project_dir / "history"
        self.redo_dir = self.history_dir / "redo"

    def ensure(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.redo_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_path(self, revision: int) -> Path:
        return self.history_dir / f"{revision:08d}.json"

    def save_snapshot(self, document: ProjectDocument) -> Path:
        self.ensure()
        destination = self.snapshot_path(document.revision)
        _atomic_json_write(destination, document.model_dump(mode="json"))
        return destination

    def load_snapshot(self, revision: int) -> ProjectDocument:
        path = self.snapshot_path(revision)
        if not path.is_file():
            raise FileNotFoundError(f"Project revision {revision} was not found.")
        return ProjectDocument.model_validate_json(path.read_text(encoding="utf-8"))

    def list_entries(self, document: ProjectDocument) -> list[HistoryEntry]:
        return list(document.history)

    def clear_redo(self) -> None:
        self.ensure()
        for path in self.redo_dir.glob("*.json"):
            path.unlink()

    def push_redo(self, document: ProjectDocument) -> Path:
        self.ensure()
        destination = self.redo_dir / f"{document.revision:08d}.json"
        _atomic_json_write(destination, document.model_dump(mode="json"))
        return destination

    def pop_redo(self) -> ProjectDocument | None:
        self.ensure()
        candidates = sorted(self.redo_dir.glob("*.json"))
        if not candidates:
            return None
        path = candidates[-1]
        document = ProjectDocument.model_validate_json(path.read_text(encoding="utf-8"))
        path.unlink()
        return document


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    """Write JSON next to its destination and atomically replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
