"""Independent audio-track editing commands."""

from __future__ import annotations

from typing import Annotated

import typer

from facut.cli.timeline_commands import _execute


audio_app = typer.Typer(
    help="Add and shape independent music or sound-effect tracks."
)


@audio_app.command("add")
def audio_add(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    track: Annotated[str, typer.Option("--track")],
    at: Annotated[str, typer.Option("--at")] = "0",
    source_in: Annotated[str, typer.Option("--in")] = "0",
    source_out: Annotated[str | None, typer.Option("--out")] = None,
    volume_db: Annotated[float, typer.Option("--volume-db")] = 0.0,
    fade_in: Annotated[str, typer.Option("--fade-in")] = "0",
    fade_out: Annotated[str, typer.Option("--fade-out")] = "0",
    loop: Annotated[
        bool,
        typer.Option(
            "--loop",
            help="Repeat the selected source range; defaults to covering the video timeline.",
        ),
    ] = False,
    duration: Annotated[
        str | None,
        typer.Option("--duration", help="Explicit looped timeline duration."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Place music or a sound effect on an audio track."""

    params = {
        "media_id": media_id,
        "track": track,
        "at": at,
        "in": source_in,
        "volume_db": volume_db,
        "fade_in": fade_in,
        "fade_out": fade_out,
        "loop": loop,
    }
    if source_out is not None:
        params["out"] = source_out
    if duration is not None:
        params["duration"] = duration
    _execute(ctx, "audio.add", params, dry_run)


@audio_app.command("volume")
def audio_volume(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    db: Annotated[float, typer.Option("--db")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Set gain for an independent audio clip in decibels."""

    _execute(ctx, "audio.volume", {"clip_id": clip_id, "db": db}, dry_run)


@audio_app.command("fade")
def audio_fade(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    fade_in: Annotated[str | None, typer.Option("--in")] = None,
    fade_out: Annotated[str | None, typer.Option("--out")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Apply a fade-in, fade-out, or both."""

    params = {"clip_id": clip_id}
    if fade_in is not None:
        params["fade_in"] = fade_in
    if fade_out is not None:
        params["fade_out"] = fade_out
    _execute(ctx, "audio.fade", params, dry_run)
