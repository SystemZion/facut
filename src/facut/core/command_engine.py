"""Semantic command dispatcher for CLI and AI batch operations."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from facut.core.models import ProjectDocument
from facut.core.project_manager import ProjectManager
from facut.core.timeline_engine import TimelineEngine


class CommandEngineError(ValueError):
    """Invalid or unsupported semantic edit command."""

    code = "INVALID_COMMAND"


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


class CommandEngine:
    """Execute a stable action vocabulary against a project manager."""

    def __init__(self, manager: ProjectManager) -> None:
        self.manager = manager

    def execute(
        self, action: str, parameters: dict[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        command = {"action": action, **parameters}

        def operation(document: ProjectDocument) -> Any:
            return self.apply(document, command)

        result, state = self.manager.mutate(
            action,
            self.summary(action, parameters),
            operation,
            command=command,
            dry_run=dry_run,
        )
        return {
            "status": "success",
            "command": action,
            "data": _serialize(result),
            "warnings": [],
            "errors": [],
            "project_revision": state.revision,
            "dry_run": dry_run,
        }

    def run_batch(self, payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """Execute commands atomically unless payload explicitly opts out."""

        commands = payload.get("commands")
        if not isinstance(commands, list):
            raise CommandEngineError('Batch payload requires a "commands" list.')
        atomic = bool(payload.get("atomic", True))
        if not atomic:
            results = []
            for command in commands:
                if not isinstance(command, dict) or "action" not in command:
                    raise CommandEngineError("Every command requires an action.")
                params = {key: value for key, value in command.items() if key != "action"}
                results.append(self.execute(command["action"], params, dry_run=dry_run))
            return {
                "status": "success",
                "command": "run",
                "data": {"results": results, "atomic": False},
                "warnings": [],
                "errors": [],
                "project_revision": self.manager.require_document().revision,
                "dry_run": dry_run,
            }

        results: list[Any] = []

        def operation(document: ProjectDocument) -> list[Any]:
            for command in commands:
                if not isinstance(command, dict) or "action" not in command:
                    raise CommandEngineError("Every command requires an action.")
                results.append(self.apply(document, command))
            return results

        _, state = self.manager.mutate(
            "run.batch",
            f"Applied {len(commands)} commands atomically",
            operation,
            command={"atomic": True, "commands": commands},
            dry_run=dry_run,
        )
        return {
            "status": "success",
            "command": "run",
            "data": {"results": _serialize(results), "atomic": True},
            "warnings": [],
            "errors": [],
            "project_revision": state.revision,
            "dry_run": dry_run,
        }

    @staticmethod
    def apply(document: ProjectDocument, command: dict[str, Any]) -> Any:
        """Apply one command to an in-memory document without saving it."""

        action = command.get("action")
        if not isinstance(action, str):
            raise CommandEngineError("Command action must be a string.")
        args = {key: value for key, value in command.items() if key != "action"}
        timeline = TimelineEngine(document)
        aliases = {"track": "track_id"}
        if action == "timeline.add":
            aliases.update({"in": "source_in", "out": "source_out"})
        elif action == "audio.add":
            aliases.update({"track": "track_id", "in": "source_in", "out": "source_out"})
        elif action == "timeline.track.add":
            aliases.update({"type": "track_type"})
        elif action == "transition.add":
            aliases.update(
                {
                    "from": "from_clip_id",
                    "to": "to_clip_id",
                    "to_clip": "to_clip_id",
                    "type": "transition_type",
                }
            )
        for old, new in aliases.items():
            if old in args and new not in args:
                args[new] = args.pop(old)
        handlers = {
            "timeline.track.add": timeline.add_track,
            "timeline.add": timeline.add_clip,
            "audio.add": timeline.add_audio_clip,
            "audio.volume": timeline.set_audio_volume,
            "audio.fade": timeline.set_audio_fades,
            "clip.move": timeline.move_clip,
            "clip.duplicate": timeline.duplicate_clip,
            "clip.transform": timeline.transform_clip,
            "clip.freeze": timeline.freeze_clip,
            "clip.composite": timeline.configure_composite,
            "effect.add": timeline.add_effect,
            "effect.remove": timeline.remove_effect,
            "adjustment.add": timeline.add_adjustment,
            "clip.split": timeline.split_clip,
            "clip.trim": timeline.trim_clip,
            "clip.delete": timeline.delete_clip,
            "clip.speed": timeline.set_speed,
            "transition.add": timeline.add_transition,
            "transition.remove": timeline.remove_transition,
        }
        handler = handlers.get(action)
        if handler is None:
            raise CommandEngineError(f"Unsupported action: {action}")
        return handler(**args)

    @staticmethod
    def summary(action: str, parameters: dict[str, Any]) -> str:
        identifiers = [
            parameters.get("clip_id"),
            parameters.get("media_id"),
            parameters.get("track_id") or parameters.get("track"),
        ]
        subject = next((str(item) for item in identifiers if item), "")
        return f"{action}{' ' + subject if subject else ''}"


def load_batch(path: str | Path) -> dict[str, Any]:
    """Read a JSON batch file. Standard-input handling belongs to the CLI."""

    import json

    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
