"""Content-adaptive, license-audited typography plan commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.responses import success_response
from facut.subtitles.director import apply_typography_plan, build_typography_plan


typography_app = typer.Typer(help="Plan and apply content-adaptive VLOG title typography.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, *, revision=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


@typography_app.command("plan")
def typography_plan(
    ctx: typer.Context,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
) -> None:
    """Match story sections to logical font roles and real installed fonts."""

    try:
        manager = manager_for(_state(ctx))
        data = build_typography_plan(manager.project_dir, language=language)
        source = manager.project_dir / "cache" / "subtitles" / "typography.plan.json"
        if output is not None:
            destination = output.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            data["output"] = str(destination)
        else:
            data["output"] = str(source.resolve())
        _emit(ctx, "typography.plan", data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "typography.plan", error)


@typography_app.command("apply")
def typography_apply(
    ctx: typer.Context,
    plan: Annotated[Path | None, typer.Option("--plan")] = None,
) -> None:
    """Apply matched fonts to compatible short-title overlays and save the policy."""

    try:
        manager = manager_for(_state(ctx))
        data = apply_typography_plan(manager, plan)
        _emit(ctx, "typography.apply", data, revision=data["project_revision"])
    except Exception as error:
        _fail(ctx, "typography.apply", error)
