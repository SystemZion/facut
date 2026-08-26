"""Explicit, auditable director preferences.

Project feedback never enters this store implicitly.  Only ``remember`` writes
global preferences, which keeps one project's experiment from contaminating
future edits.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field

from facut.exceptions import InvalidArgumentError, ResourceNotFoundError


class DirectorPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    id: str
    category: str
    value: str
    original_feedback: str
    source_feedback_id: str
    applies_to: list[str] = Field(default_factory=list)
    created_at: str


def _atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


class TasteStore:
    """Store only preferences explicitly approved by the user."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path(user_data_path("facut", appauthor=False))
        self.path = self.root / "director-profile.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": "1.0", "preferences": []}
        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        return {
            "version": "1.0",
            "preferences": [
                DirectorPreference.model_validate(item).model_dump(mode="json")
                for item in payload.get("preferences", [])
            ],
        }

    def show(self) -> dict[str, Any]:
        payload = self.load()
        return {**payload, "count": len(payload["preferences"]), "path": str(self.path.resolve())}

    def remember(
        self,
        feedback_id: str,
        *,
        category: str,
        value: str,
        original_feedback: str,
        applies_to: list[str] | None = None,
    ) -> dict[str, Any]:
        if not all(item.strip() for item in (feedback_id, category, value, original_feedback)):
            raise InvalidArgumentError("Preference id, category, value, and feedback are required.")
        seed = f"{feedback_id}\0{category}\0{value}".encode("utf-8")
        preference = DirectorPreference(
            id=f"preference_{hashlib.sha256(seed).hexdigest()[:16].upper()}",
            category=category.strip(),
            value=value.strip(),
            original_feedback=original_feedback.strip(),
            source_feedback_id=feedback_id.strip(),
            applies_to=sorted(set(applies_to or [])),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = self.load()
        payload["preferences"] = [
            item for item in payload["preferences"] if item["id"] != preference.id
        ] + [preference.model_dump(mode="json")]
        _atomic(self.path, payload)
        return preference.model_dump(mode="json")

    def forget(self, preference_id: str) -> dict[str, Any]:
        payload = self.load()
        selected = next(
            (item for item in payload["preferences"] if item["id"] == preference_id), None
        )
        if selected is None:
            raise ResourceNotFoundError(f'Preference "{preference_id}" was not found.')
        payload["preferences"] = [
            item for item in payload["preferences"] if item["id"] != preference_id
        ]
        _atomic(self.path, payload)
        return {"forgotten": preference_id, "remaining": len(payload["preferences"])}

    def export(self, output: str | Path, *, overwrite: bool = False) -> Path:
        destination = Path(output).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise InvalidArgumentError(
                f'Output "{destination}" already exists.', suggestion="Use --overwrite to replace it."
            )
        _atomic(destination, self.load())
        return destination
