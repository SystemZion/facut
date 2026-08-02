"""Named sequence management and batch delivery."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.cli.render_commands import RENDER_PRESETS
from facut.core.sequences import (
    checkout_sequence,
    duplicate_sequence,
    list_sequences,
    materialize_sequence,
    remove_sequence,
    snapshot_sequence,
)
from facut.render.ffmpeg_backend import FFmpegBackend
from facut.responses import success_response


sequence_app = typer.Typer(help="Save, switch, and batch-render multiple timelines.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _emit(ctx: typer.Context, command: str, data: object, revision: int) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _mutate(ctx: typer.Context, action: str, summary: str, operation, command) -> None:
    manager = manager_for(_state(ctx))
    result, state = manager.mutate(action, summary, operation, command=command)
    _emit(ctx, action, result, state.revision)


@sequence_app.command("save")
def save(ctx: typer.Context, name: str) -> None:
    """Save the current timeline as a named sequence."""

    try:
        _mutate(
            ctx,
            "sequence.save",
            f"Saved sequence {name}",
            lambda document: snapshot_sequence(document, name),
            {"name": name},
        )
    except Exception as error:
        _abort(ctx, "sequence.save", error)


@sequence_app.command("checkout")
def checkout(ctx: typer.Context, name: str) -> None:
    """Replace the working timeline with a named sequence."""

    try:
        _mutate(
            ctx,
            "sequence.checkout",
            f"Checked out sequence {name}",
            lambda document: checkout_sequence(document, name),
            {"name": name},
        )
    except Exception as error:
        _abort(ctx, "sequence.checkout", error)


@sequence_app.command("duplicate")
def duplicate(ctx: typer.Context, source: str, destination: str) -> None:
    """Duplicate a named sequence without copying media."""

    try:
        _mutate(
            ctx,
            "sequence.duplicate",
            f"Duplicated sequence {source} as {destination}",
            lambda document: duplicate_sequence(document, source, destination),
            {"source": source, "destination": destination},
        )
    except Exception as error:
        _abort(ctx, "sequence.duplicate", error)


@sequence_app.command("remove")
def remove(ctx: typer.Context, name: str) -> None:
    """Remove a saved sequence; media remains untouched."""

    try:
        _mutate(
            ctx,
            "sequence.remove",
            f"Removed sequence {name}",
            lambda document: remove_sequence(document, name),
            {"name": name},
        )
    except Exception as error:
        _abort(ctx, "sequence.remove", error)


@sequence_app.command("list")
def list_command(ctx: typer.Context) -> None:
    """List named timelines and their durations."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        _emit(ctx, "sequence.list", list_sequences(document), document.revision)
    except Exception as error:
        _abort(ctx, "sequence.list", error)


@sequence_app.command("render-all")
def render_all(
    ctx: typer.Context,
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    preset: Annotated[str, typer.Option("--preset")] = "youtube-1080p",
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Render every saved sequence with one delivery preset."""

    command = "sequence.render-all"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        if preset not in RENDER_PRESETS:
            raise ValueError(f'Unknown render preset "{preset}".')
        settings = RENDER_PRESETS[preset]
        output_dir.mkdir(parents=True, exist_ok=True)
        backend = FFmpegBackend(state.config.tools.ffmpeg)
        results = []
        for item in list_sequences(document):
            name = item["name"]
            safe_name = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-")
            destination = output_dir / f"{safe_name or 'sequence'}.mp4"
            result = backend.render(
                materialize_sequence(document, name),
                manager.project_dir,
                destination,
                width=settings["width"],
                height=settings["height"],
                fps=settings["fps"],
                bitrate=settings["bitrate"],
                hardware=hardware,
                overwrite=overwrite,
            )
            results.append({"sequence": name, **result.as_dict()})
        _emit(ctx, command, results, document.revision)
    except Exception as error:
        _abort(ctx, command, error)
