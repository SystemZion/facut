"""Director Studio music and explicit preference commands."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.director.music import MusicCatalog, TAG_DIMENSIONS
from facut.director.taste import TasteStore
from facut.responses import success_response


music_app = typer.Typer(help="Search, audition, plan, and audit the licensed Music Library v2.")
music_source_app = typer.Typer(help="Use safe public-source discovery sessions.")
taste_app = typer.Typer(help="Manage explicitly remembered director preferences.")
music_app.add_typer(music_source_app, name="source")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, warnings: list[str] | None = None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx), success_response(command, data, warnings=warnings or []),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))

def _set_project(ctx: typer.Context, project: Path | None) -> None:
    if project is not None:
        _state(ctx).project = project


def _tags(values: list[str] | None) -> dict[str, list[str]]:
    output = {dimension: [] for dimension in TAG_DIMENSIONS}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError("Music tags use --tag dimension=value.")
        dimension, value = raw.split("=", 1)
        if dimension not in output or not value.strip():
            raise ValueError(f'Invalid music tag "{raw}".')
        output[dimension].append(value.strip())
    return output

def _platforms(values: list[str] | None) -> list[str]:
    return sorted({item.strip() for raw in values or [] for item in raw.split(",") if item.strip()})


@music_app.command("ingest")
def music_ingest(
    ctx: typer.Context,
    source: Path,
    recursive: Annotated[bool, typer.Option("--recursive", "-r")] = False,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    platform: Annotated[list[str] | None, typer.Option("--platform")] = None,
    license_file: Annotated[Path | None, typer.Option("--license-file")] = None,
    license_type: Annotated[str | None, typer.Option("--license-type")] = None,
    author: Annotated[str | None, typer.Option("--author")] = None,
    analyze: Annotated[bool, typer.Option("--analyze/--no-analyze")] = True,
    jsonl_progress: Annotated[bool, typer.Option("--jsonl-progress")] = False,
) -> None:
    """Index one track or a directory; source audio stays read-only and in place."""

    command = "library.ingest"
    try:
        state = _state(ctx)
        catalog = MusicCatalog()
        options = {
            "ffmpeg": state.config.tools.ffmpeg, "ffprobe": state.config.tools.ffprobe,
            "tags": _tags(tag), "platforms": _platforms(platform), "license_file": license_file,
            "license_type": license_type, "author": author, "analyze": analyze,
        }
        data = (
            catalog.ingest_directory(
                source, recursive=recursive,
                progress=(lambda event: typer.echo(json.dumps(event, ensure_ascii=True, separators=(",", ":")))) if jsonl_progress else None,
                **options,
            )
            if source.is_dir() else catalog.ingest_file(source, **options)
        )
        _emit(ctx, command, data)
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("find")
def music_find(
    ctx: typer.Context,
    query: str,
    style: Annotated[str | None, typer.Option("--style")] = None,
    scene: Annotated[str | None, typer.Option("--scene")] = None,
    duration: Annotated[float | None, typer.Option("--duration")] = None,
    platform: Annotated[list[str] | None, typer.Option("--platform")] = None,
    top: Annotated[int, typer.Option("--top")] = 3,
    offset: Annotated[int, typer.Option("--offset")] = 0,
) -> None:
    """Hard-filter license, platform, hash, and duration before creative ranking."""

    command = "library.search.v2"
    try:
        _emit(ctx, command, MusicCatalog().find(query, style=style, scene=scene, duration=duration, platforms=_platforms(platform), top=top, offset=offset))
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("audition")
def music_audition(ctx: typer.Context, track_id: str, project: Annotated[Path | None, typer.Option("--project", "-p")] = None) -> None:
    """Return an auditable local track for Review Room playback; never auto-apply it."""

    command = "library.audition"
    try:
        _set_project(ctx, project)
        item = MusicCatalog().get(track_id)
        _emit(ctx, command, {"asset": item, "status": "review_required", "autoplay": False})
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("plan")
def music_plan(
    ctx: typer.Context, candidate_id: str,
    output: Annotated[Path, typer.Option("--output", "-o")],
    start: Annotated[float, typer.Option("--start")] = 0.0,
    duration: Annotated[float | None, typer.Option("--duration")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    command = "library.music.plan"
    try:
        data = MusicCatalog().plan(candidate_id, start=start, duration=duration)
        destination = output.expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(f'Output "{destination}" already exists.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _emit(ctx, command, {**data, "output": str(destination)})
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("apply")
def music_apply(
    ctx: typer.Context, plan: Path,
    approved_only: Annotated[bool, typer.Option("--approved-only/--allow-draft")] = True,
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
) -> None:
    """Validate approval/license, then ask the existing timeline engine to add the cue."""

    command = "library.music.apply"
    try:
        from facut.cli.common import manager_for
        from facut.director.music import apply_music_plan

        _set_project(ctx, project)
        manager = manager_for(_state(ctx))
        _emit(ctx, command, apply_music_plan(manager, plan, approved_only=approved_only))
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("audit")
def music_audit(ctx: typer.Context, platform: Annotated[list[str], typer.Option("--platform")]) -> None:
    command = "library.audit"
    try:
        _emit(ctx, command, MusicCatalog().audit(_platforms(platform)))
    except Exception as error:
        _fail(ctx, command, error)


@music_app.command("export")
def music_export(ctx: typer.Context, output: Annotated[Path, typer.Option("--output", "-o")], overwrite: Annotated[bool, typer.Option("--overwrite")] = False) -> None:
    try:
        path = MusicCatalog().export_json(output, overwrite=overwrite)
        _emit(ctx, "library.export", {"output": str(path)})
    except Exception as error:
        _fail(ctx, "library.export", error)


@music_source_app.command("list")
def music_source_list(ctx: typer.Context) -> None:
    _emit(ctx, "library.source.list", {"sources": [
        {"id": "pixabay", "mode": "visible-browser", "official_audio_api": False},
        {"id": "youtube-audio-library", "mode": "visible-browser", "login_may_be_required": True},
    ]})


@music_source_app.command("search")
def music_source_search(
    ctx: typer.Context, source: str, query: str,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    command = "library.source.search"
    try:
        data = MusicCatalog().create_source_session(source, query)
        data["browser_opened"] = bool(open_browser and webbrowser.open(data["url"]))
        _emit(ctx, command, data, ["SOURCE_INTERACTION_REQUIRED: use the visible browser; no hidden endpoint will be called."])
    except Exception as error:
        _fail(ctx, command, error)


@music_source_app.command("import")
def music_source_import(
    ctx: typer.Context, downloaded_file: Path,
    session_id: Annotated[str, typer.Option("--from-session")],
    platform: Annotated[list[str] | None, typer.Option("--platform")] = None,
    license_file: Annotated[Path | None, typer.Option("--license-file")] = None,
    license_text: Annotated[str | None, typer.Option("--license-text")] = None,
    license_type: Annotated[str | None, typer.Option("--license-type")] = None,
) -> None:
    command = "library.source.import"
    try:
        state = _state(ctx)
        data = MusicCatalog().import_source_download(
            downloaded_file, session_id, platforms=_platforms(platform), license_file=license_file,
            license_text=license_text, license_type=license_type,
            ffmpeg=state.config.tools.ffmpeg, ffprobe=state.config.tools.ffprobe,
        )
        _emit(ctx, command, data)
    except Exception as error:
        _fail(ctx, command, error)


@taste_app.command("show")
def taste_show(ctx: typer.Context) -> None:
    _emit(ctx, "taste.show", TasteStore().show())


@taste_app.command("remember")
def taste_remember(
    ctx: typer.Context, feedback_id: str,
    category: Annotated[str, typer.Option("--category")],
    value: Annotated[str, typer.Option("--value")],
    feedback: Annotated[str, typer.Option("--feedback")],
    applies_to: Annotated[list[str] | None, typer.Option("--applies-to")] = None,
) -> None:
    try:
        _emit(ctx, "taste.remember", TasteStore().remember(feedback_id, category=category, value=value, original_feedback=feedback, applies_to=applies_to))
    except Exception as error:
        _fail(ctx, "taste.remember", error)


@taste_app.command("forget")
def taste_forget(ctx: typer.Context, preference_id: str) -> None:
    try:
        _emit(ctx, "taste.forget", TasteStore().forget(preference_id))
    except Exception as error:
        _fail(ctx, "taste.forget", error)


@taste_app.command("export")
def taste_export(ctx: typer.Context, output: Annotated[Path, typer.Option("--output", "-o")], overwrite: Annotated[bool, typer.Option("--overwrite")] = False) -> None:
    try:
        path = TasteStore().export(output, overwrite=overwrite)
        _emit(ctx, "taste.export", {"output": str(path)})
    except Exception as error:
        _fail(ctx, "taste.export", error)
