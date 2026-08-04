"""Content-addressed, branch-aware project history for safe AI experiments."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from facut.core.models import ProjectDocument


class CutGraphError(RuntimeError):
    code = "HISTORY_ERROR"


class CutGraphConflictError(CutGraphError):
    code = "HISTORY_CONFLICT"


_REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class CutGraphStore:
    """Store complete project commits as hash-addressed JSON objects."""

    def __init__(self, project_dir: str | Path) -> None:
        self.root = Path(project_dir) / "history"
        self.objects = self.root / "objects"
        self.heads = self.root / "refs" / "heads"
        self.redo_root = self.root / "cutgraph-redo"
        self.head_file = self.root / "HEAD"
        self.reflog = self.root / "reflog.jsonl"

    @property
    def initialized(self) -> bool:
        return self.head_file.is_file() and self.objects.is_dir()

    def _validate_branch(self, branch: str) -> str:
        normalized = branch.strip().replace("\\", "/")
        if not _REF_NAME.fullmatch(normalized) or ".." in normalized or normalized.endswith("/"):
            raise CutGraphError(
                "Branch names may contain letters, numbers, ., _, -, and / only."
            )
        return normalized

    def _branch_path(self, branch: str) -> Path:
        normalized = self._validate_branch(branch)
        path = (self.heads / normalized).resolve()
        if self.heads.resolve() not in path.parents:
            raise CutGraphError("Branch path escapes the history directory.")
        return path

    def current_branch(self) -> str:
        if not self.head_file.is_file():
            return "main"
        value = self.head_file.read_text(encoding="utf-8").strip()
        prefix = "ref: refs/heads/"
        if not value.startswith(prefix):
            raise CutGraphError("FACUT HEAD is invalid.")
        return value[len(prefix) :]

    def head(self, branch: str | None = None) -> str | None:
        path = self._branch_path(branch or self.current_branch())
        return path.read_text(encoding="utf-8").strip() if path.is_file() else None

    def _write_head(self, branch: str) -> None:
        normalized = self._validate_branch(branch)
        _atomic_text(self.head_file, f"ref: refs/heads/{normalized}\n")

    def _move_ref(self, branch: str, commit_id: str, action: str) -> None:
        previous = self.head(branch)
        _atomic_text(self._branch_path(branch), commit_id + "\n")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.reflog.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    {
                        "timestamp": _utc_now(),
                        "branch": branch,
                        "from": previous,
                        "to": commit_id,
                        "action": action,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _backup_legacy(self) -> None:
        snapshots = sorted(self.root.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json"))
        if not snapshots:
            return
        backup = self.root / "legacy-v06"
        backup.mkdir(parents=True, exist_ok=True)
        for source in snapshots:
            destination = backup / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
        redo = self.root / "redo"
        if redo.is_dir():
            redo_backup = backup / "redo"
            redo_backup.mkdir(exist_ok=True)
            for source in redo.glob("*.json"):
                destination = redo_backup / source.name
                if not destination.exists():
                    shutil.copy2(source, destination)

    def initialize(self, document: ProjectDocument, *, migrate_legacy: bool = True) -> str:
        if self.initialized:
            resolved = self.head()
            if resolved is None:
                raise CutGraphError("FACUT history has HEAD but no branch tip.")
            return resolved
        if migrate_legacy:
            self._backup_legacy()
        self.objects.mkdir(parents=True, exist_ok=True)
        self.heads.mkdir(parents=True, exist_ok=True)
        self.redo_root.mkdir(parents=True, exist_ok=True)
        commit_id = self._write_object(
            document,
            parents=[],
            branch="main",
            action="project.init" if document.revision == 0 else "history.migrate",
            summary="Initial CutGraph project state",
            actor="system",
            intent="Initialize content-addressed history",
        )
        self._write_head("main")
        self._move_ref("main", commit_id, "initialize")
        return commit_id

    def _write_object(
        self,
        document: ProjectDocument,
        *,
        parents: list[str],
        branch: str,
        action: str,
        summary: str,
        actor: str,
        intent: str | None,
    ) -> str:
        timestamp = _utc_now()
        document_payload = document.model_dump(mode="json")
        if document_payload.get("history"):
            document_payload["history"][-1]["commit_id"] = None
        core = {
            "version": "1.0",
            "parents": parents,
            "branch": branch,
            "revision": document.revision,
            "action": action,
            "summary": summary,
            "actor": actor,
            "intent": intent,
            "timestamp": timestamp,
            "document": document_payload,
        }
        commit_id = hashlib.sha256(_canonical(core)).hexdigest()
        if document.history:
            entry = document.history[-1]
            entry.commit_id = commit_id
            entry.parent_commit_ids = list(parents)
            entry.branch = branch
            entry.actor = actor
            entry.intent = intent
        core["document"] = document.model_dump(mode="json")
        payload = {"commit_id": commit_id, **core}
        destination = self.objects / f"{commit_id}.json"
        if not destination.exists():
            _atomic_json(destination, payload)
        return commit_id

    def prepare_commit(
        self,
        document: ProjectDocument,
        *,
        action: str,
        summary: str,
        actor: str = "user",
        intent: str | None = None,
    ) -> tuple[str, str]:
        branch = self.current_branch()
        parent = self.head(branch)
        if parent is None:
            raise CutGraphError(f'Branch "{branch}" has no commit.')
        commit_id = self._write_object(
            document,
            parents=[parent],
            branch=branch,
            action=action,
            summary=summary,
            actor=actor,
            intent=intent,
        )
        return commit_id, branch

    def finalize_commit(self, branch: str, commit_id: str) -> None:
        stack = self._redo_stack(branch)
        if stack:
            recovery = f"recovery/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            suffix = 1
            while self._branch_path(recovery).exists():
                recovery = f"recovery/{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
                suffix += 1
            self._move_ref(recovery, stack[0], "preserve-abandoned-future")
        self._write_redo_stack(branch, [])
        self._move_ref(branch, commit_id, "commit")

    def read_commit(self, identifier: str) -> dict[str, Any]:
        commit_id = self.resolve(identifier)
        return json.loads((self.objects / f"{commit_id}.json").read_text(encoding="utf-8"))

    def resolve(self, identifier: str) -> str:
        normalized = identifier.strip()
        if normalized == "HEAD":
            resolved = self.head()
            if resolved is None:
                raise CutGraphError("HEAD has no commit.")
            return resolved
        try:
            branch_path = self._branch_path(normalized)
        except CutGraphError:
            branch_path = Path()
        if branch_path.is_file():
            return branch_path.read_text(encoding="utf-8").strip()
        exact = self.objects / f"{normalized}.json"
        if exact.is_file():
            return normalized
        matches = list(self.objects.glob(f"{normalized}*.json")) if len(normalized) >= 7 else []
        if len(matches) == 1:
            return matches[0].stem
        if len(matches) > 1:
            raise CutGraphError(f'Commit prefix "{identifier}" is ambiguous.')
        raise CutGraphError(f'Commit or branch "{identifier}" was not found.')

    def document_at(self, identifier: str) -> ProjectDocument:
        return ProjectDocument.model_validate(self.read_commit(identifier)["document"])

    def list_branches(self) -> list[dict[str, Any]]:
        current = self.current_branch()
        result = []
        if not self.heads.is_dir():
            return result
        for path in sorted(self.heads.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(self.heads).as_posix()
            result.append(
                {
                    "name": name,
                    "current": name == current,
                    "commit_id": path.read_text(encoding="utf-8").strip(),
                }
            )
        return result

    def create_branch(self, name: str, *, start: str = "HEAD") -> dict[str, Any]:
        normalized = self._validate_branch(name)
        path = self._branch_path(normalized)
        if path.exists():
            raise CutGraphError(f'Branch "{normalized}" already exists.')
        commit_id = self.resolve(start)
        self._move_ref(normalized, commit_id, "branch.create")
        return {"name": normalized, "commit_id": commit_id, "current": False}

    def switch_branch(self, name: str) -> ProjectDocument:
        normalized = self._validate_branch(name)
        commit_id = self.head(normalized)
        if commit_id is None:
            raise CutGraphError(f'Branch "{normalized}" was not found.')
        self._write_head(normalized)
        return self.document_at(commit_id)

    def _redo_path(self, branch: str) -> Path:
        digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:24]
        return self.redo_root / f"{digest}.json"

    def _redo_stack(self, branch: str) -> list[str]:
        path = self._redo_path(branch)
        if not path.is_file():
            return []
        try:
            return list(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return []

    def _write_redo_stack(self, branch: str, stack: list[str]) -> None:
        _atomic_json(self._redo_path(branch), stack)

    def undo(self) -> ProjectDocument:
        branch = self.current_branch()
        current_id = self.head(branch)
        if current_id is None:
            raise CutGraphError("There is no operation to undo.")
        current = self.read_commit(current_id)
        parents = current.get("parents", [])
        if not parents:
            raise CutGraphError("There is no operation to undo.")
        stack = self._redo_stack(branch)
        stack.append(current_id)
        self._write_redo_stack(branch, stack)
        self._move_ref(branch, parents[0], "undo")
        return self.document_at(parents[0])

    def redo(self) -> ProjectDocument:
        branch = self.current_branch()
        stack = self._redo_stack(branch)
        if not stack:
            raise CutGraphError("There is no operation to redo.")
        commit_id = stack.pop()
        commit = self.read_commit(commit_id)
        if self.head(branch) not in commit.get("parents", []):
            raise CutGraphConflictError("Redo history diverged from the current branch.")
        self._write_redo_stack(branch, stack)
        self._move_ref(branch, commit_id, "redo")
        return ProjectDocument.model_validate(commit["document"])

    def log(self, identifier: str = "HEAD", *, limit: int = 50) -> list[dict[str, Any]]:
        commit_id = self.resolve(identifier)
        result: list[dict[str, Any]] = []
        while commit_id and len(result) < max(1, limit):
            item = self.read_commit(commit_id)
            result.append({key: value for key, value in item.items() if key != "document"})
            parents = item.get("parents", [])
            commit_id = parents[0] if parents else ""
        return result

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        target = self.resolve(ancestor)
        pending = [self.resolve(descendant)]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.read_commit(current).get("parents", []))
        return False

    def accept_branch(self, source: str, target: str = "main") -> tuple[dict[str, Any], ProjectDocument | None]:
        source_id = self.resolve(source)
        target_id = self.resolve(target)
        if not self.is_ancestor(target_id, source_id):
            raise CutGraphConflictError(
                f'Branch "{target}" changed after "{source}" diverged; automatic merge is disabled.'
            )
        self._move_ref(target, source_id, "branch.accept")
        document = None
        if self.current_branch() == target:
            document = self.document_at(source_id)
        return {
            "source": source,
            "target": target,
            "previous": target_id,
            "commit_id": source_id,
            "fast_forward": True,
        }, document

    def status(self) -> dict[str, Any]:
        branch = self.current_branch()
        return {
            "initialized": self.initialized,
            "branch": branch,
            "head": self.head(branch),
            "branches": self.list_branches(),
            "redo_available": bool(self._redo_stack(branch)),
            "dirty": False,
        }

    def max_revision(self) -> int:
        maximum = 0
        if not self.objects.is_dir():
            return maximum
        for path in self.objects.glob("*.json"):
            try:
                maximum = max(maximum, int(json.loads(path.read_text(encoding="utf-8"))["revision"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return maximum


def semantic_diff(before: ProjectDocument, after: ProjectDocument) -> dict[str, Any]:
    """Return stable entity-level changes instead of a noisy JSON text diff."""

    def entities(document: ProjectDocument) -> dict[str, dict[str, Any]]:
        project_format = document.project.model_dump(mode="json")
        project_format.pop("updated_at", None)
        project_format.pop("created_at", None)
        project_format.pop("duration", None)
        result: dict[str, dict[str, Any]] = {
            "project:format": project_format,
            "project:settings": document.settings,
        }
        for asset in document.media:
            result[f"media:{asset.id}"] = asset.model_dump(mode="json")
        for track in document.tracks:
            track_payload = track.model_dump(mode="json")
            clips = track_payload.pop("clips", [])
            result[f"track:{track.id}"] = track_payload
            for clip in clips:
                result[f"clip:{clip['id']}"] = clip
        for kind, items in (
            ("transition", document.transitions),
            ("subtitle", document.subtitle_cues),
            ("text", document.text_overlays),
            ("marker", document.markers),
        ):
            for item in items:
                result[f"{kind}:{item.id}"] = item.model_dump(mode="json")
        return result

    left, right = entities(before), entities(after)
    added = [key for key in right if key not in left]
    removed = [key for key in left if key not in right]
    modified = [
        {"entity": key, "before": left[key], "after": right[key]}
        for key in left.keys() & right.keys()
        if left[key] != right[key]
    ]
    changed_clip_ranges = []
    def clip_range(payload: dict[str, Any], entity: str) -> dict[str, Any]:
        duration = payload.get("timeline_duration") or (
            payload["source_out"] - payload["source_in"]
        ) / abs(payload.get("speed", 1))
        return {
            "from": float(payload["timeline_start"]),
            "to": float(payload["timeline_start"]) + float(duration),
            "entity": entity,
        }

    for key in added:
        if key.startswith("clip:"):
            changed_clip_ranges.append(clip_range(right[key], key))
        elif key.startswith("subtitle:"):
            changed_clip_ranges.append({"from": right[key]["start"], "to": right[key]["end"], "entity": key})
        elif key.startswith("text:"):
            changed_clip_ranges.append({"from": right[key]["at"], "to": right[key]["at"] + right[key]["duration"], "entity": key})
    for key in removed:
        if key.startswith("clip:"):
            changed_clip_ranges.append(clip_range(left[key], key))
        elif key.startswith("subtitle:"):
            changed_clip_ranges.append({"from": left[key]["start"], "to": left[key]["end"], "entity": key})
        elif key.startswith("text:"):
            changed_clip_ranges.append({"from": left[key]["at"], "to": left[key]["at"] + left[key]["duration"], "entity": key})
    for item in modified:
        entity = item["entity"]
        if entity == "project:format" or entity == "project:settings":
            changed_clip_ranges.append(
                {"from": 0.0, "to": max(before.project.duration, after.project.duration), "entity": entity}
            )
            continue
        if entity.startswith("subtitle:"):
            changed_clip_ranges.append(
                {
                    "from": min(item["before"]["start"], item["after"]["start"]),
                    "to": max(item["before"]["end"], item["after"]["end"]),
                    "entity": entity,
                }
            )
            continue
        if entity.startswith("text:"):
            before_end = item["before"]["at"] + item["before"]["duration"]
            after_end = item["after"]["at"] + item["after"]["duration"]
            changed_clip_ranges.append(
                {
                    "from": min(item["before"]["at"], item["after"]["at"]),
                    "to": max(before_end, after_end),
                    "entity": entity,
                }
            )
            continue
        if not entity.startswith("clip:"):
            continue
        starts = [item["before"]["timeline_start"], item["after"]["timeline_start"]]
        before_range = clip_range(item["before"], item["entity"])
        after_range = clip_range(item["after"], item["entity"])
        ends = [before_range["to"], after_range["to"]]
        changed_clip_ranges.append({"from": min(starts), "to": max(ends), "entity": entity})
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "changed_ranges": changed_clip_ranges,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },
    }
