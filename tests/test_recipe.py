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
    assert manager.cutgraph.current_branch().startswith("recipe/")
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


def test_recipe_compiles_reverse_and_speed_curve(tmp_path) -> None:
    from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo

    manager = ProjectManager.create(tmp_path / "project")
    manager.document.media.append(
        MediaAsset(
            id="media_01",
            kind=MediaKind.VIDEO,
            path="source.mp4",
            original_name="source.mp4",
            size=100,
            sha256="a" * 64,
            technical=MediaTechnicalInfo(duration=10, video_codec="h264"),
        )
    )
    manager.save()
    recipe = RecipeDocument.model_validate(
        {
            "version": "1.0",
            "tracks": [{"id": "V1", "type": "video", "name": "Main"}],
            "clips": [
                {
                    "id": "reverse_clip",
                    "media_id": "media_01",
                    "track": "V1",
                    "at": 0.0,
                    "in": 0.0,
                    "out": 2.0,
                    "reverse": True,
                },
                {
                    "id": "curve_clip",
                    "media_id": "media_01",
                    "track": "V1",
                    "at": 2.0,
                    "in": 2.0,
                    "out": 6.0,
                    "speed_curve": {
                        "mode": "step",
                        "points": [{"at": 0.0, "rate": 1}, {"at": 2.0, "rate": 2}],
                    },
                },
            ],
        }
    )
    plan = RecipeEngine(manager).plan(recipe)
    assert any(item["action"] == "clip.speed" and item["rate"] == -1 for item in plan.commands)
    assert any(item["action"] == "clip.speed_curve" for item in plan.commands)
