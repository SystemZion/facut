"""Machine-readable capability and schema commands."""

from __future__ import annotations

import json

import typer

from facut.agent import action_schema, capabilities
from facut.responses import success_response
from facut.schemas import schema_path


schema_app = typer.Typer(help="Discover Agent capabilities and JSON Schemas.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: object) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


@schema_app.command("commands")
def commands(ctx: typer.Context) -> None:
    """Describe transports, response contracts and callable actions."""

    _emit(ctx, "schema.commands", capabilities())


@schema_app.command("action")
def action(ctx: typer.Context, name: str) -> None:
    """Return the JSON Schema and behavior contract for one action."""

    _emit(ctx, "schema.action", {"name": name, **action_schema(name)})


@schema_app.command("project")
def project(ctx: typer.Context) -> None:
    """Return the complete project JSON Schema."""

    data = json.loads(schema_path("project").read_text(encoding="utf-8"))
    _emit(ctx, "schema.project", data)


@schema_app.command("narration")
def narration(ctx: typer.Context) -> None:
    """Return the strict review-first narration plan schema."""

    from facut.intelligence import narration_plan_json_schema

    _emit(ctx, "schema.narration", narration_plan_json_schema())


@schema_app.command("recipe")
def recipe(ctx: typer.Context) -> None:
    """Return the declarative FACUT recipe schema."""

    from facut.recipe import recipe_json_schema

    _emit(ctx, "schema.recipe", recipe_json_schema())


@schema_app.command("workflow")
def workflow(ctx: typer.Context, name: str) -> None:
    """Return a complete multi-step Agent workflow contract."""

    if name.casefold() != "vlog":
        raise typer.BadParameter('The available workflow is "vlog".')
    from facut.vlog import vlog_workflow_schema

    _emit(ctx, "schema.workflow", vlog_workflow_schema())


@schema_app.command("subtitle-director")
def subtitle_director(ctx: typer.Context) -> None:
    """Return transcript review and caption plan schemas."""

    from facut.subtitles import subtitle_director_schemas

    _emit(ctx, "schema.subtitle-director", subtitle_director_schemas())


@schema_app.command("director-studio")
def director_studio(ctx: typer.Context) -> None:
    """Return Review Room, Timeline Patch, music and preference schemas."""

    from facut.director.review_room import TimelinePatch
    from facut.director.taste import DirectorPreference

    _emit(ctx, "schema.director-studio", {
        "review_session.v1": {
            "type": "object", "required": ["project", "host", "port", "pid"],
            "properties": {"project": {"type": "string"}, "host": {"const": "127.0.0.1"}, "port": {"type": "integer"}, "pid": {"type": "integer"}},
        },
        "review_feedback.v1": {
            "type": "object", "required": ["start", "end", "action"],
            "properties": {"start": {"type": "number", "minimum": 0}, "end": {"type": "number", "minimum": 0}, "action": {"type": "string"}, "comment": {"type": "string"}},
        },
        "timeline_patch.v1": TimelinePatch.model_json_schema(),
        "music_asset.v2": {"type": "object", "required": ["id", "sha256", "path", "duration", "tags", "license_status", "platforms"]},
        "music_query.v1": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "style": {"type": ["string", "null"]}, "scene": {"type": ["string", "null"]}, "duration": {"type": ["number", "null"]}, "platforms": {"type": "array"}}},
        "music_cue_plan.v1": {"type": "object", "required": ["asset_id", "timeline_start", "duration", "approved"]},
        "director_preference.v1": DirectorPreference.model_json_schema(),
    })
