"""Project, media import, inspection, history, and batch commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import compact_project, manager_for, public_error
from facut.core.command_engine import CommandEngine
from facut.core.project_manager import ProjectManager
from facut.media.probe import probe_media
from facut.responses import success_response


project_app = typer.Typer(help="Inspect, validate, snapshot, and manage projects.")
history_app = typer.Typer(help="Inspect project revision history.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, human: str = "", warnings=None, revision=None):
    from facut.cli.main import emit

    state = _state(ctx)
    emit(
        state,
        success_response(
            command, data, warnings=warnings or [], project_revision=revision
        ),
        human=human,
    )


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def init_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="New project directory.")],
    width: Annotated[int, typer.Option("--width")] = 1920,
    height: Annotated[int, typer.Option("--height")] = 1080,
    fps: Annotated[float, typer.Option("--fps")] = 30.0,
    sample_rate: Annotated[int, typer.Option("--sample-rate")] = 48000,
    background: Annotated[str, typer.Option("--background")] = "#000000",
) -> None:
    """Create a validated non-destructive editing project."""

    try:
        manager = ProjectManager.create(
            path,
            width=width,
            height=height,
            fps=fps,
            sample_rate=sample_rate,
            background=background,
        )
        document = manager.require_document()
        _emit(
            ctx,
            "init",
            {"project": str(manager.project_file), "settings": document.project.model_dump(mode="json")},
            f"[green]Created project:[/green] {manager.project_file}",
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "init", error)


def import_command(
    ctx: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Media file(s) or directories.")],
    recursive: Annotated[bool, typer.Option("--recursive", "-r")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    porcelain: Annotated[
        bool,
        typer.Option(
            "--porcelain",
            help="Print only one stable media ID per line for shell scripts.",
        ),
    ] = False,
) -> None:
    """Import supported video, audio, image, and subtitle assets."""

    try:
        manager = manager_for(_state(ctx))
        assets = manager.import_paths(paths, recursive=recursive, dry_run=dry_run)
        if porcelain:
            if _state(ctx).json_output:
                raise ValueError("Use either --json or --porcelain, not both.")
            for asset in assets:
                typer.echo(asset.id)
            return
        revision = manager.require_document().revision + (1 if dry_run else 0)
        data = [asset.model_dump(mode="json") for asset in assets]
        _emit(
            ctx,
            "import",
            {"media": data, "dry_run": dry_run},
            "\n".join(f"[green]{asset.id}[/green]  {asset.original_name}" for asset in assets),
            revision=revision,
        )
    except Exception as error:
        _abort(ctx, "import", error)


def inspect_command(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Media ID or file path.")],
    keyframes: Annotated[bool, typer.Option("--keyframes")] = False,
) -> None:
    """Read media metadata without decoding the complete file."""

    try:
        state = _state(ctx)
        try:
            manager = manager_for(state)
            asset = manager.resolve_media(target)
            path = manager.resolve_path(asset.path)
            identity = asset.model_dump(mode="json")
        except Exception:
            path = Path(target).expanduser().resolve()
            identity = {"path": str(path), "original_name": path.name}
        info = probe_media(path, ffprobe=state.config.tools.ffprobe, include_keyframes=keyframes)
        data = {**identity, "technical": info.model_dump(mode="json")}
        _emit(ctx, "inspect", data, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as error:
        _abort(ctx, "inspect", error)


@project_app.command("info")
def project_info(ctx: typer.Context) -> None:
    """Show project settings and object counts."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        data = {
            "file": str(manager.project_file),
            "revision": document.revision,
            "project": document.project.model_dump(mode="json"),
            "counts": {
                "media": len(document.media),
                "tracks": len(document.tracks),
                "clips": sum(len(track.clips) for track in document.tracks),
                "transitions": len(document.transitions),
            },
        }
        _emit(ctx, "project.info", data, json.dumps(data, ensure_ascii=False, indent=2), revision=document.revision)
    except Exception as error:
        _abort(ctx, "project.info", error)


@project_app.command("validate")
def project_validate(ctx: typer.Context) -> None:
    """Strictly validate project structure and media availability."""

    try:
        manager = manager_for(_state(ctx))
        warnings = manager.validate()
        document = manager.require_document()
        _emit(
            ctx,
            "project.validate",
            {"valid": True, "file": str(manager.project_file)},
            "[green]Project is valid.[/green]",
            warnings=warnings,
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "project.validate", error)


@project_app.command("snapshot")
def project_snapshot(
    ctx: typer.Context,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Create an AI-readable complete state snapshot."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        warnings = manager.validate()
        data = compact_project(document)
        data["warnings"] = warnings
        if output:
            if output.exists():
                raise FileExistsError(f'Output "{output}" already exists.')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _emit(
            ctx,
            "project.snapshot",
            {"snapshot": data, "output": str(output) if output else None},
            f"[green]Snapshot ready{f': {output}' if output else ''}[/green]",
            warnings=warnings,
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "project.snapshot", error)


@history_app.command("list")
def history_list(ctx: typer.Context) -> None:
    """List revision-producing operations."""

    try:
        document = manager_for(_state(ctx)).require_document()
        data = [entry.model_dump(mode="json") for entry in document.history]
        text = "\n".join(
            f"{entry.revision:>4}  {entry.action:<24} {entry.summary}"
            for entry in document.history
        ) or "No edits yet."
        _emit(ctx, "history.list", data, text, revision=document.revision)
    except Exception as error:
        _abort(ctx, "history.list", error)


def undo_command(ctx: typer.Context) -> None:
    """Restore the immediately preceding project revision."""

    try:
        document = manager_for(_state(ctx)).undo()
        _emit(ctx, "undo", {"revision": document.revision}, f"[green]Restored revision {document.revision}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "undo", error)


def redo_command(ctx: typer.Context) -> None:
    """Reapply the most recently undone revision."""

    try:
        document = manager_for(_state(ctx)).redo()
        _emit(ctx, "redo", {"revision": document.revision}, f"[green]Restored revision {document.revision}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "redo", error)


def run_command(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="JSON command file or '-' for stdin.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run an atomic JSON command list."""

    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
        manager = manager_for(_state(ctx))
        result = CommandEngine(manager).run_batch(payload, dry_run=dry_run)
        document = manager.require_document()
        _emit(ctx, "run", result["data"], f"[green]Applied {len(payload.get('commands', []))} command(s).[/green]", revision=result["project_revision"])
    except Exception as error:
        _abort(ctx, "run", error)
