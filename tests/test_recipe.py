from __future__ import annotations

import pytest

from facut.core.command_engine import CommandEngineError
from facut.core.project_manager import ProjectManager
from facut.recipe import RecipeDocument, RecipeEngine, RecipeError


def test_recipe_plan_and_build_use_one_atomic_revision(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    recipe = RecipeDocument.model_validate(
        {
            "version": "1.0",
            "tracks": [
                {"id": "V1", "type": "video", "name": "Main"},
                {"id": "A1", "type": "audio", "name": "Music"},
            ],
            "commands": [
                {"action": "timeline.track.add", "type": "subtitle", "name": "Captions", "track_id": "S1"}
            ],
            "render": {"preset": "youtube-4k"},
            "qc": {"black_frames": True},
        }
    )
    engine = RecipeEngine(manager)
    validation = engine.validate(recipe)
    assert validation["valid"] is True
    assert validation["command_count"] == 3
    assert manager.require_document().revision == 0

    planned = engine.plan(recipe, output=tmp_path / "final.mp4")
    assert planned.render_request["output"].endswith("final.mp4")
    result = engine.build(recipe, output=tmp_path / "final.mp4")
    assert result["edit"]["project_revision"] == 1
    assert result["render_executed"] is False
    assert {track.id for track in manager.require_document().tracks} == {"V1", "A1", "S1"}

    restored = manager.undo()
    assert restored.revision == 0
    assert restored.tracks == []


def test_recipe_validation_simulates_commands_without_mutating(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    recipe = RecipeDocument.model_validate(
        {"version": "1.0", "commands": [{"action": "not.real"}]}
    )
    with pytest.raises(CommandEngineError):
        RecipeEngine(manager).validate(recipe)
    assert manager.require_document().revision == 0


def test_recipe_rejects_non_atomic_and_empty_payloads() -> None:
    with pytest.raises(ValueError):
        RecipeDocument.model_validate({"version": "1.0", "atomic": False, "commands": []})
    with pytest.raises(ValueError):
        RecipeDocument.model_validate({"version": "1.0"})
