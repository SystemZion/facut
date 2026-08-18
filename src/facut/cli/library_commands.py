"""Licensed music/SFX library and VLOG style-pack commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.library import MediaLibrary
from facut.responses import success_response
from facut.styles import describe_style, list_styles, validate_style_usage


library_app = typer.Typer(help="Manage local licensed music and sound effects.")
music_app = typer.Typer(help="Register local music with mood and license evidence.")
sfx_app = typer.Typer(help="Register local sound effects with tags and license evidence.")
style_app = typer.Typer(help="Inspect and validate VLOG directing style packs.")
library_app.add_typer(music_app, name="music")
library_app.add_typer(sfx_app, name="sfx")


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


def _add_audio(
    ctx: typer.Context,
    source: Path,
    kind: str,
    tags: list[str] | None,
    moods: list[str] | None,
    platforms: list[str] | None,
    license_file: Path | None,
    analyze: bool,
) -> None:
    state = _state(ctx)
    data = MediaLibrary().add(
        source,
        kind=kind,
        tags=tags,
        moods=moods,
        platforms=platforms,
        license_file=license_file,
        ffmpeg=state.config.tools.ffmpeg,
        ffprobe=state.config.tools.ffprobe,
        analyze=analyze,
    )
    _emit(ctx, f"library.{kind}.add", data)


@music_app.command("add")
def music_add(
    ctx: typer.Context,
    source: Path,
    mood: Annotated[list[str] | None, typer.Option("--mood")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    platform: Annotated[list[str] | None, typer.Option("--platform")] = None,
    license_file: Annotated[Path | None, typer.Option("--license-file")] = None,
    analyze: Annotated[bool, typer.Option("--analyze/--no-analyze")] = True,
) -> None:
    """Reference music in place and save beat, mood, platform, and license evidence."""

    try:
        _add_audio(ctx, source, "music", tag, mood, platform, license_file, analyze)
    except Exception as error:
        _fail(ctx, "library.music.add", error)


@sfx_app.command("add")
def sfx_add(
    ctx: typer.Context,
    source: Path,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    platform: Annotated[list[str] | None, typer.Option("--platform")] = None,
    license_file: Annotated[Path | None, typer.Option("--license-file")] = None,
) -> None:
    """Reference a sound effect in place; unverified licenses fail final audit."""

    try:
        _add_audio(ctx, source, "sfx", tag, None, platform, license_file, False)
    except Exception as error:
        _fail(ctx, "library.sfx.add", error)


@library_app.command("search")
def library_search(
    ctx: typer.Context,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    mood: Annotated[str | None, typer.Option("--mood")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    platform: Annotated[str | None, typer.Option("--platform")] = None,
) -> None:
    """Search eligible local assets without assuming copyrighted songs are available."""

    try:
        _emit(ctx, "library.search", MediaLibrary().search(kind=kind, mood=mood, tag=tag, platform=platform))
    except Exception as error:
        _fail(ctx, "library.search", error)


@library_app.command("audit")
def library_audit(ctx: typer.Context, platform: Annotated[str, typer.Option("--platform")]) -> None:
    """Fail visibly on offline, unlicensed, or platform-ineligible assets."""

    try:
        _emit(ctx, "library.audit", MediaLibrary().audit(platform))
    except Exception as error:
        _fail(ctx, "library.audit", error)


@style_app.command("list")
def style_list(ctx: typer.Context) -> None:
    _emit(ctx, "style.list", list_styles())


@style_app.command("describe")
def style_describe(ctx: typer.Context, name: str) -> None:
    try:
        _emit(ctx, "style.describe", describe_style(name))
    except Exception as error:
        _fail(ctx, "style.describe", error)


@style_app.command("validate")
def style_validate(ctx: typer.Context, name: str) -> None:
    try:
        manager = manager_for(_state(ctx))
        data = validate_style_usage(manager.require_document(), name)
        _emit(ctx, "style.validate", data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "style.validate", error)
