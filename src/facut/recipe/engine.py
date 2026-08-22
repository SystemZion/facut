"""Compile recipes into existing semantic commands and execute them atomically."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from facut.core.command_engine import CommandEngine
from facut.core.project_manager import ProjectManager

from .models import RecipeDocument, RecipeError


@dataclass(frozen=True, slots=True)
class RecipePlan:
    version: str
    recipe_sha256: str
    project_revision: int
    commands: list[dict[str, Any]]
    render_request: dict[str, Any] | None
    qc_request: dict[str, Any] | None
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "recipe_sha256": self.recipe_sha256,
            "project_revision": self.project_revision,
            "atomic": True,
            "commands": deepcopy(self.commands),
            "render_request": deepcopy(self.render_request),
            "qc_request": deepcopy(self.qc_request),
            "warnings": list(self.warnings),
        }


def load_recipe(path: str | Path) -> RecipeDocument:
    source = Path(path).expanduser().resolve()
    try:
        return RecipeDocument.model_validate_json(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RecipeError(f'Could not read recipe "{source}": {exc}') from exc
    except (ValueError, ValidationError) as exc:
        raise RecipeError(f'Recipe "{source}" is invalid: {exc}') from exc


class RecipeEngine:
    """Validate, plan and atomically apply a declarative recipe."""

    def __init__(self, manager: ProjectManager) -> None:
        self.manager = manager

    def plan(
        self,
        recipe: RecipeDocument,
        *,
        recipe_path: str | Path | None = None,
        output: str | Path | None = None,
    ) -> RecipePlan:
        source_dir = (
            Path(recipe_path).expanduser().resolve().parent
            if recipe_path is not None
            else self.manager.project_dir
        )
        document = self.manager.require_document()
        declared_track_ids = {track.id for track in recipe.tracks if track.id}
        duplicate_tracks = declared_track_ids & {track.id for track in document.tracks}
        if duplicate_tracks:
            raise RecipeError(
                "Recipe declares track ids which already exist: " + ", ".join(sorted(duplicate_tracks)),
                suggestion="Remove those track declarations and reference the existing tracks directly.",
            )
        commands = self._compile(recipe, source_dir=source_dir, document=document)
        command_engine = CommandEngine(self.manager)
        prepared = [command_engine.prepare_command(command) for command in commands]

        candidate = self.manager.require_document().model_copy(deep=True)
        for command in prepared:
            CommandEngine.apply(candidate, command)
        warnings: list[str] = []
        render_request = None
        if recipe.render is not None or output is not None:
            render_request = recipe.render.model_dump(mode="json") if recipe.render else {}
            if output is not None:
                render_request["output"] = str(Path(output).expanduser())
            if not render_request.get("output"):
                warnings.append("Recipe render settings have no output; the caller must supply one.")
        if recipe.qc is not None and render_request is None:
            warnings.append("QC is deferred until the caller provides or renders an output file.")
        digest_payload = recipe.model_dump(mode="json", by_alias=True)
        digest = hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return RecipePlan(
            version="1.0",
            recipe_sha256=digest,
            project_revision=self.manager.require_document().revision,
            commands=commands,
            render_request=render_request,
            qc_request=deepcopy(recipe.qc),
            warnings=warnings,
        )

    def validate(
        self, recipe: RecipeDocument, *, recipe_path: str | Path | None = None
    ) -> dict[str, Any]:
        plan = self.plan(recipe, recipe_path=recipe_path)
        return {
            "valid": True,
            "recipe_sha256": plan.recipe_sha256,
            "command_count": len(plan.commands),
            "project_revision": plan.project_revision,
            "warnings": plan.warnings,
        }

    def build(
        self,
        recipe: RecipeDocument,
        *,
        recipe_path: str | Path | None = None,
        output: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(recipe, recipe_path=recipe_path, output=output)
        if not dry_run:
            self.manager.ensure_experiment_branch("recipe")
        result = CommandEngine(self.manager).run_batch(
            {
                "version": "1.0",
                "atomic": True,
                "actor": "agent",
                "intent": f"Apply recipe {plan.recipe_sha256[:12]}",
                "commands": plan.commands,
            },
            dry_run=dry_run,
        )
        return {
            "status": "success",
            "recipe_sha256": plan.recipe_sha256,
            "edit": result,
            "render_request": plan.render_request,
            "render_executed": False,
            "qc_request": plan.qc_request,
            "qc_executed": False,
            "warnings": [
                *plan.warnings,
                *(
                    ["Timeline edits are committed; render/QC requests must be executed by the CLI backend."]
                    if plan.render_request or plan.qc_request
                    else []
                ),
            ],
            "dry_run": dry_run,
        }

    @staticmethod
    def _compile(
        recipe: RecipeDocument, *, source_dir: Path, document: Any
    ) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        if recipe.vlog is not None:
            commands.append(
                {
                    "action": "vlog.apply",
                    "candidate_id": recipe.vlog.candidate_id,
                    "preset": recipe.vlog.preset,
                }
            )
        if recipe.review is not None:
            plan_path = Path(recipe.review.plan).expanduser()
            if not plan_path.is_absolute():
                plan_path = source_dir / plan_path
            commands.append(
                {
                    "action": "vlog.review.apply",
                    "plan": str(plan_path.resolve()),
                    "approved_only": recipe.review.approved_only,
                }
            )
        if recipe.soundscape is not None:
            plan_path = Path(recipe.soundscape.plan).expanduser()
            if not plan_path.is_absolute():
                plan_path = source_dir / plan_path
            commands.append(
                {
                    "action": "vlog.soundscape.apply",
                    "plan": str(plan_path.resolve()),
                    "approved_only": recipe.soundscape.approved_only,
                }
            )
        track_types: dict[str, str] = {
            track.id: track.type.value for track in document.tracks
        }
        for index, track in enumerate(recipe.tracks, start=1):
            track_id = track.id or f"RECIPE_{track.type.upper()}_{index}"
            track_types[track_id] = track.type
            commands.append(
                {
                    "action": "timeline.track.add",
                    "type": track.type,
                    "name": track.name,
                    "track_id": track_id,
                    "metadata": {
                        **track.metadata,
                        **({"role": track.role} if track.role is not None else {}),
                    },
                }
            )
        for index, clip in enumerate(recipe.clips, start=1):
            clip_id = clip.id or f"recipe_clip_{index:03d}"
            action = "audio.add" if track_types.get(clip.track) == "audio" else "timeline.add"
            command: dict[str, Any] = {
                "action": action,
                "media_id": clip.media_id,
                "track_id": clip.track,
                "at": clip.at,
                "source_in": clip.source_in,
                "clip_id": clip_id,
                "append": clip.append,
            }
            if clip.source_out is not None:
                command["source_out"] = clip.source_out
            if clip.duration is not None:
                command["duration"] = clip.duration
            if action == "audio.add":
                command.update(clip.audio)
                command.pop("append", None)  # audio.add has no append option yet
            commands.append(command)
            if clip.transform:
                commands.append({"action": "clip.transform", "clip_id": clip_id, **clip.transform})
            if clip.speed is not None:
                commands.append({"action": "clip.speed", "clip_id": clip_id, "rate": clip.speed})
            elif clip.reverse:
                commands.append({"action": "clip.speed", "clip_id": clip_id, "rate": -1.0})
            elif clip.speed_curve is not None:
                commands.append(
                    {"action": "clip.speed_curve", "clip_id": clip_id, "curve": clip.speed_curve}
                )
            for effect in clip.effects:
                commands.append(
                    {
                        "action": "effect.add",
                        "clip_id": clip_id,
                        "effect_type": effect.type,
                        "parameters": effect.parameters,
                    }
                )
        for transition in recipe.transitions:
            command = {
                "action": "transition.add",
                "type": transition.type,
                "duration": transition.duration,
                "parameters": transition.parameters,
            }
            if transition.from_clip is not None:
                command.update({"from": transition.from_clip, "to": transition.to_clip})
            else:
                command.update({"track": transition.track, "at": transition.at})
            commands.append(command)
        commands.extend(deepcopy(recipe.commands))
        if recipe.narration is not None:
            plan_path = Path(recipe.narration.plan).expanduser()
            if not plan_path.is_absolute():
                plan_path = source_dir / plan_path
            commands.append(
                {
                    "action": "narration.apply",
                    "plan_path": str(plan_path.resolve()),
                    "approved_only": recipe.narration.approved_only,
                    "duck_music": recipe.narration.duck_music,
                    "preserve_original": recipe.narration.preserve_original,
                    "track_id": recipe.narration.track_id,
                    "allow_stale": recipe.narration.allow_stale,
                }
            )
        return commands
