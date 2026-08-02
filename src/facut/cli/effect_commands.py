"""Video effects, composite settings, and adjustment-layer commands."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from facut.cli.timeline_commands import _execute, _state
from facut.effects.registry import registry
from facut.responses import success_response


effect_app = typer.Typer(help="Add, remove, inspect, and layer video effects.")


def _value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parameters(items: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError('Effect parameters use "name=value" syntax.')
        name, raw = item.split("=", 1)
        result[name] = _value(raw)
    return result


@effect_app.command("list")
def effect_list(ctx: typer.Context) -> None:
    from facut.cli.main import emit

    data = [
        {
            "name": item.name,
            "category": item.category,
            "parameters": item.parameters,
            "implemented": True,
        }
        for item in registry.list()
    ]
    emit(
        _state(ctx),
        success_response("effect.list", data),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


@effect_app.command("describe")
def effect_describe(ctx: typer.Context, effect_type: str) -> None:
    from facut.cli.main import emit

    item = registry.get(effect_type)
    data = {
        "name": item.name,
        "category": item.category,
        "parameters": item.parameters,
        "implemented": True,
    }
    emit(
        _state(ctx),
        success_response("effect.describe", data),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


@effect_app.command("add")
def effect_add(
    ctx: typer.Context,
    clip_id: str,
    effect_type: Annotated[str, typer.Option("--type")],
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    _execute(
        ctx,
        "effect.add",
        {
            "clip_id": clip_id,
            "effect_type": effect_type,
            "parameters": _parameters(param or []),
        },
        dry_run,
    )


@effect_app.command("remove")
def effect_remove(
    ctx: typer.Context,
    effect_id: str,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    _execute(ctx, "effect.remove", {"effect_id": effect_id}, dry_run)


@effect_app.command("adjustment-add")
def adjustment_add(
    ctx: typer.Context,
    track: Annotated[str, typer.Option("--track")],
    effect_type: Annotated[str, typer.Option("--type")],
    at: Annotated[str, typer.Option("--at")],
    duration: Annotated[str, typer.Option("--duration")],
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    _execute(
        ctx,
        "adjustment.add",
        {
            "track_id": track,
            "effect_type": effect_type,
            "at": at,
            "duration": duration,
            "parameters": _parameters(param or []),
        },
        dry_run,
    )
