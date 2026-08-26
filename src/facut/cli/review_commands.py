"""Director Studio Review Room and Timeline Patch commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.director.review_room import (
    apply_patch, capture_request, close_room, create_patch, export_review, open_room,
    preview_patch, room_status,
)
from facut.responses import success_response

review_app = typer.Typer(help="Open the local Review Room and apply evidence-bound Timeline Patches.")
patch_app = typer.Typer(help="Plan, preview, and atomically apply timeline.patch.v1 files.")
review_app.add_typer(patch_app, name="patch")

def _state(ctx: typer.Context):
    from facut.cli.main import CliState
    return ctx.ensure_object(CliState)

def _emit(ctx: typer.Context, command: str, data, warnings: list[str] | None = None) -> None:
    from facut.cli.main import emit
    revision = None
    try: revision = manager_for(_state(ctx)).require_document().revision
    except Exception: pass
    emit(_state(ctx), success_response(command, data, warnings=warnings or [], project_revision=revision), human=json.dumps(data, ensure_ascii=False, indent=2))

def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail
    fail(_state(ctx), command, public_error(error))

def _project(ctx: typer.Context, project: Path | None):
    if project is not None:
        _state(ctx).project = project
    return manager_for(_state(ctx))

@review_app.command("open")
def review_open(ctx: typer.Context, browser: Annotated[bool, typer.Option("--browser/--no-browser")] = True, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    """Start a loopback-only token-authenticated Director Studio session."""
    try: _emit(ctx, "review.session.create", open_room(_project(ctx, project), open_browser=browser))
    except Exception as error: _fail(ctx, "review.session.create", error)

@review_app.command("status")
def review_status(ctx: typer.Context, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    try: _emit(ctx, "review.session.status", room_status(_project(ctx, project)))
    except Exception as error: _fail(ctx, "review.session.status", error)

@review_app.command("close")
def review_close(ctx: typer.Context, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    try: _emit(ctx, "review.session.close", close_room(_project(ctx, project)))
    except Exception as error: _fail(ctx, "review.session.close", error)

@review_app.command("export")
def review_export(ctx: typer.Context, output: Annotated[Path, typer.Option("--output", "-o")], overwrite: Annotated[bool, typer.Option("--overwrite")] = False, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    try:
        path = export_review(_project(ctx, project), output, overwrite=overwrite)
        _emit(ctx, "review.session.export", {"output": str(path)})
    except Exception as error: _fail(ctx, "review.session.export", error)

@patch_app.command("plan")
def patch_plan(ctx: typer.Context, source: Path, output: Annotated[Path | None, typer.Option("--output", "-o")] = None, overwrite: Annotated[bool, typer.Option("--overwrite")] = False) -> None:
    command = "review.patch.plan"
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        data = create_patch(manager_for(_state(ctx)), payload)
        if output:
            destination = output.expanduser().resolve()
            if destination.exists() and not overwrite: raise FileExistsError(f'Output "{destination}" already exists.')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps({key:value for key,value in data.items() if key != "path"}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            data["output"] = str(destination)
        _emit(ctx, command, data)
    except Exception as error: _fail(ctx, command, error)

@patch_app.command("preview")
def patch_preview(ctx: typer.Context, source: Path, approved_only: Annotated[bool, typer.Option("--approved-only/--all")] = False) -> None:
    try: _emit(ctx, "review.patch.preview", preview_patch(manager_for(_state(ctx)), source, approved_only=approved_only))
    except Exception as error: _fail(ctx, "review.patch.preview", error)

@patch_app.command("apply")
def patch_apply(ctx: typer.Context, source: Path, approved_only: Annotated[bool, typer.Option("--approved-only/--all")] = True, dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    command = "review.patch.apply"
    try:
        manager = manager_for(_state(ctx))
        data = preview_patch(manager, source, approved_only=approved_only) if dry_run else apply_patch(manager, source, approved_only=approved_only)
        data["dry_run"] = dry_run
        _emit(ctx, command, data)
    except Exception as error: _fail(ctx, command, error)

def edit_command(ctx: typer.Context, request: str, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    """Capture a natural-language edit request for an external Agent to structure."""
    try: _emit(ctx, "review.feedback.submit", capture_request(_project(ctx, project), request), ["REVIEW_REQUIRED: an Agent must return a validated timeline.patch.v1 before FACUT changes the project."])
    except Exception as error: _fail(ctx, "review.feedback.submit", error)
