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
