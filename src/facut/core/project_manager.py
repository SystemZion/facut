"""Non-destructive facut project creation, validation and persistence."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from .history import HistoryStore
from .cutgraph import CutGraphStore, semantic_diff
from .models import HistoryEntry, ProjectDocument, ProjectSettings

T = TypeVar("T")


class ProjectError(RuntimeError):
    """A recoverable project-layer error suitable for CLI translation."""

    code = "PROJECT_ERROR"
    exit_code = 3

    def __init__(self, message: str, *, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.suggestion = suggestion


class ProjectNotFoundError(ProjectError):
    code = "PROJECT_NOT_FOUND"


class InvalidProjectError(ProjectError):
    code = "INVALID_PROJECT"
    exit_code = 2


class ProjectManager:
    """Own a project document and save each mutation atomically."""

    FILE_NAME = "facut.json"
    SUBDIRECTORIES = ("media", "proxies", "cache", "previews", "renders", "history")

    def __init__(self, project_path: str | Path) -> None:
        supplied = Path(project_path).expanduser()
        self.project_file = supplied if supplied.suffix.lower() == ".json" else supplied / self.FILE_NAME
        self.project_dir = self.project_file.parent
        self.document: ProjectDocument | None = None
        self.history = HistoryStore(self.project_dir)
        self.cutgraph = CutGraphStore(self.project_dir)

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        name: str | None = None,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        sample_rate: int = 48000,
        background: str = "#000000",
        exist_ok: bool = False,
    ) -> "ProjectManager":
        destination = Path(path).expanduser().resolve()
        project_file = destination if destination.suffix.lower() == ".json" else destination / cls.FILE_NAME
        project_dir = project_file.parent
        if project_file.exists() and not exist_ok:
            raise ProjectError(
                f'Project file "{project_file}" already exists.',
                suggestion="Choose another directory or explicitly allow an existing empty project.",
            )
        project_dir.mkdir(parents=True, exist_ok=True)
        for child in cls.SUBDIRECTORIES:
            (project_dir / child).mkdir(exist_ok=True)
        manager = cls(project_file)
        manager.document = ProjectDocument(
            project=ProjectSettings(
                name=name or project_dir.name,
                width=width,
                height=height,
                fps=fps,
                sample_rate=sample_rate,
                background=background,
            )
        )
        manager.save(create_snapshot=True)
        manager.cutgraph.initialize(manager.document, migrate_legacy=False)
        return manager

    def load(self) -> ProjectDocument:
        if not self.project_file.is_file():
            raise ProjectNotFoundError(
                f'Project "{self.project_file}" was not found.',
                suggestion="Run `facut init <directory>` to create it.",
            )
        try:
            self.document = ProjectDocument.model_validate_json(
                self.project_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise InvalidProjectError(
                f'Project "{self.project_file}" is invalid: {exc}',
                suggestion="Run `facut project validate --verbose` for details.",
            ) from exc
        return self.document

    def require_document(self) -> ProjectDocument:
        return self.document if self.document is not None else self.load()

    def save(
        self,
        document: ProjectDocument | None = None,
        *,
        create_snapshot: bool = True,
    ) -> Path:
        if document is not None:
            self.document = document
        document = self.require_document()
        document.recompute_duration()
        document.project.updated_at = document.project.updated_at.__class__.now(
            document.project.updated_at.tzinfo
        )
        # Round-trip validation before replacing the current file.
        payload = document.model_dump(mode="json")
        ProjectDocument.model_validate(payload)
        _atomic_json_write(self.project_file, payload)
        if create_snapshot:
            self.history.save_snapshot(document)
        return self.project_file

    def validate(self) -> list[str]:
        """Return warnings after strict structural and on-disk checks."""

        document = self.require_document()
        ProjectDocument.model_validate(document.model_dump(mode="json"))
        warnings: list[str] = []
        for asset in document.media:
            source = self.resolve_path(asset.path)
            if not source.is_file():
                warnings.append(f"Media {asset.id} is offline: {asset.original_name}")
                asset.offline = True
            else:
                asset.offline = False
        return warnings

    def mutate(
        self,
        action: str,
        summary: str,
        operation: Callable[[ProjectDocument], T],
        *,
        command: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> tuple[T, ProjectDocument]:
        """Apply one mutation transaction and return its result plus new state."""

        current = self.require_document()
        if not dry_run:
            self.cutgraph.initialize(current)
        candidate = current.model_copy(deep=True)
        result = operation(candidate)
        candidate.revision = (
            current.revision + 1
            if dry_run
            else max(current.revision, self.cutgraph.max_revision()) + 1
        )
        actor_value = str((command or {}).get("_actor", "user"))
        actor = actor_value if actor_value in {"user", "agent", "system"} else "user"
        intent = (command or {}).get("_intent")
        candidate.history.append(
            HistoryEntry(
                revision=candidate.revision,
                action=action,
                summary=summary,
                command=command or {},
                actor=actor,
                intent=str(intent) if intent is not None else None,
            )
        )
        candidate.recompute_duration()
        ProjectDocument.model_validate(candidate.model_dump(mode="json"))
        if dry_run:
            return result, candidate
        self.history.save_snapshot(current)
        self.history.clear_redo()
        commit_id, branch = self.cutgraph.prepare_commit(
            candidate,
            action=action,
            summary=summary,
            actor=actor,
            intent=str(intent) if intent is not None else None,
        )
        self.document = candidate
        self.save(create_snapshot=True)
        self.cutgraph.finalize_commit(branch, commit_id)
        return result, candidate

    def undo(self) -> ProjectDocument:
        current = self.require_document()
        self.cutgraph.initialize(current)
        restored = self.cutgraph.undo()
        self.document = restored
        self.save(create_snapshot=False)
        return restored

    def redo(self) -> ProjectDocument:
        current = self.require_document()
        self.cutgraph.initialize(current)
        restored = self.cutgraph.redo()
        self.document = restored
        self.save(create_snapshot=True)
        return restored

    def checkout(self, revision: int) -> ProjectDocument:
        current = self.require_document()
        self.cutgraph.initialize(current)
        matching = []
        for path in self.cutgraph.objects.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if int(payload.get("revision", -1)) == revision:
                matching.append(payload["commit_id"])
        if len(matching) != 1:
            raise ProjectError(
                f"Project revision {revision} was not found or is ambiguous.",
                suggestion="Use `facut history log --graph` and restore by commit ID.",
            )
        restored = self.cutgraph.document_at(matching[0])
        self.document = restored
        self.save(create_snapshot=False)
        return restored

    def switch_branch(self, name: str) -> ProjectDocument:
        current = self.require_document()
        self.cutgraph.initialize(current)
        restored = self.cutgraph.switch_branch(name)
        self.document = restored
        self.save(create_snapshot=False)
        return restored

    def ensure_experiment_branch(self, prefix: str = "agent") -> str:
        """Keep generated edits off main by switching to a unique experiment branch."""

        current = self.require_document()
        self.cutgraph.initialize(current)
        branch = self.cutgraph.current_branch()
        if branch != "main":
            return branch
        base = f"{prefix}/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        candidate = base
        existing = {item["name"] for item in self.cutgraph.list_branches()}
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        self.cutgraph.create_branch(candidate)
        self.document = self.cutgraph.switch_branch(candidate)
        self.save(create_snapshot=False)
        return candidate

    def accept_branch(self, source: str, target: str = "main") -> tuple[dict[str, Any], ProjectDocument]:
        current = self.require_document()
        self.cutgraph.initialize(current)
        result, restored = self.cutgraph.accept_branch(source, target)
        if restored is not None:
            self.document = restored
            self.save(create_snapshot=False)
        return result, self.require_document()

    def restore_commit(self, identifier: str) -> ProjectDocument:
        current = self.require_document()
        self.cutgraph.initialize(current)
        target = self.cutgraph.document_at(identifier)
        candidate = target.model_copy(deep=True)
        candidate.revision = max(current.revision, self.cutgraph.max_revision()) + 1
        candidate.history = list(current.history)
        candidate.history.append(
            HistoryEntry(
                revision=candidate.revision,
                action="history.restore",
                summary=f"Restored project state from {identifier}",
                command={"commit": identifier},
            )
        )
        commit_id, branch = self.cutgraph.prepare_commit(
            candidate,
            action="history.restore",
            summary=f"Restored project state from {identifier}",
        )
        self.history.save_snapshot(current)
        self.document = candidate
        self.save(create_snapshot=True)
        self.cutgraph.finalize_commit(branch, commit_id)
        return candidate

    def diff_history(self, before: str, after: str) -> dict[str, Any]:
        current = self.require_document()
        self.cutgraph.initialize(current)
        result = semantic_diff(
            self.cutgraph.document_at(before),
            self.cutgraph.document_at(after),
        )
        result["before"] = self.cutgraph.resolve(before)
        result["after"] = self.cutgraph.resolve(after)
        return result

    def resolve_path(self, stored_path: str) -> Path:
        path = Path(stored_path)
        return path if path.is_absolute() else (self.project_dir / path).resolve()

    def store_path(self, path: str | Path) -> str:
        """Prefer portable paths when a file is inside the project."""

        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.project_dir.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    @classmethod
    def discover(cls, start: str | Path = ".") -> "ProjectManager":
        manager = cls(find_project(start))
        manager.load()
        return manager

    def import_paths(
        self,
        paths: list[str | Path],
        *,
        recursive: bool = False,
        exclude_proxy_candidates: bool = False,
        dry_run: bool = False,
    ) -> list[Any]:
        """Import paths through the media service without coupling the CLI to it."""

        from facut.media.importer import MediaImporter

        return MediaImporter(self).import_paths(
            paths,
            recursive=recursive,
            exclude_proxy_candidates=exclude_proxy_candidates,
            dry_run=dry_run,
        )

    def resolve_media(self, media_or_path: str) -> Any:
        document = self.require_document()
        media = document.find_media(media_or_path)
        if media is not None:
            return media
        candidate = Path(media_or_path).expanduser().resolve()
        for asset in document.media:
            if self.resolve_path(asset.path) == candidate:
                return asset
        raise ProjectError(
            f'Media "{media_or_path}" was not found.',
            suggestion="Run `facut media list` or `facut timeline show --json`.",
        )

    def clean(self) -> list[str]:
        removed: list[str] = []
        for directory in ("cache", "previews"):
            root = self.project_dir / directory
            if not root.is_dir():
                continue
            for item in root.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed.append(str(item))
        return removed


def find_project(start: str | Path = ".") -> Path:
    """Search *start* and its parents for facut.json."""

    current = Path(start).expanduser().resolve()
    if current.is_file():
        return current
    for directory in (current, *current.parents):
        candidate = directory / ProjectManager.FILE_NAME
        if candidate.is_file():
            return candidate
    raise ProjectNotFoundError(
        f'No "{ProjectManager.FILE_NAME}" was found from "{current}".',
        suggestion="Use --project or run this command inside a facut project.",
    )


def init_project(
    path: str | Path,
    *,
    name: str | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    sample_rate: int = 48000,
    background: str = "#000000",
) -> ProjectManager:
    """Convenience API used by CLI and embedders."""

    return ProjectManager.create(
        path,
        name=name,
        width=width,
        height=height,
        fps=fps,
        sample_rate=sample_rate,
        background=background,
    )


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
