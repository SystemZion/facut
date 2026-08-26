"""Public 0.9 Director Loop interface coverage."""

from typer.testing import CliRunner

from facut.agent.registry import action_schema
from facut.cli.main import app
from facut.recipe.models import RecipeDocument
from facut.vlog.models import vlog_workflow_schema


def test_director_loop_actions_are_public_and_strict() -> None:
    expected = {
        "vlog.atlas.build",
        "vlog.atlas.status",
        "vlog.inspect.batch",
        "vlog.observe.batch",
        "vlog.story.brief",
        "vlog.story.submit",
        "vlog.story.validate",
        "vlog.opening.plan",
        "vlog.ending.plan",
        "vlog.continuity.check",
        "vlog.review.create",
        "vlog.review.submit",
        "vlog.review.plan",
        "vlog.review.apply",
        "vlog.soundscape.analyze",
        "vlog.soundscape.plan",
        "vlog.soundscape.apply",
        "vlog.bible.audit",
        "vlog.bible.rename",
    }
    for action in expected:
        schema = action_schema(action)
        assert schema["rpc"] is True
        assert schema["parameters"]["additionalProperties"] is False
    assert action_schema("vlog.review.apply")["mutates"] is True
    assert action_schema("vlog.soundscape.apply")["mutates"] is True
    assert action_schema("vlog.atlas.build")["parameters"]["properties"]["sampling"]["default"] == "adaptive"
    assert action_schema("vlog.soundscape.analyze")["parameters"]["properties"]["measure"]["default"] is False


def test_workflow_prefers_external_story_and_keeps_deterministic_fallback() -> None:
    steps = {item["action"]: item for item in vlog_workflow_schema()["steps"]}
    assert steps["vlog.story.submit"]["preferred"] is True
    assert steps["vlog.plan"]["fallback"] == "deterministic-baseline"
    assert steps["vlog.inspect.batch"]["repeat_until"] == "atlas pending_tasks == 0"


def test_cli_exposes_director_loop_groups() -> None:
    result = CliRunner().invoke(app, ["vlog", "--help"], terminal_width=160)
    assert result.exit_code == 0, result.output
    for command in ("atlas", "story", "opening", "ending", "continuity", "review", "soundscape"):
        assert command in result.output


def test_recipe_schema_accepts_review_and_soundscape_plans() -> None:
    recipe = RecipeDocument.model_validate(
        {
            "review": {"plan": "review.plan.json", "approved_only": True},
            "soundscape": {"plan": "soundscape.plan.json", "approved_only": True},
        }
    )
    assert recipe.review is not None
    assert recipe.soundscape is not None
