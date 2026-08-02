"""Local-first material analysis command group."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any, Callable

import typer

from facut.analysis import (
    analyze_beats,
    analyze_quality,
    analyze_scenes,
    analyze_song_metadata,
    analyze_travel_metadata,
    transcribe_local,
)
from facut.cli.common import manager_for, public_error
from facut.responses import success_response
from facut.media.probe import probe_media


analyze_app = typer.Typer(
    help="Analyze quality, scenes, beats, speech, travel metadata, and song metadata."
)


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _resolve(ctx: typer.Context, target: str) -> tuple[Path, str | None, Any | None]:
    try:
        manager = manager_for(_state(ctx))
        asset = manager.resolve_media(target)
        return manager.resolve_path(asset.path), asset.id, manager
    except Exception:
        path = Path(target).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'Analysis target "{target}" was not found.')
        return path, None, None


def _emit_analysis(
    ctx: typer.Context,
    command: str,
    target: str,
    analyzer: Callable[[Path], dict[str, Any]],
    *,
    save: bool,
    cache_parameters: dict[str, Any] | None = None,
) -> None:
    from facut.cli.main import emit

    try:
        path, media_id, manager = _resolve(ctx, target)
        cache_key = None
        cache_path = None
        cache_hit = False
        if media_id is not None and manager is not None:
            asset = manager.require_document().find_media(media_id)
            assert asset is not None
            encoded = json.dumps(
                {
                    "analysis": command,
                    "algorithm_version": 1,
                    "media_sha256": asset.sha256,
                    "parameters": cache_parameters or {},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            cache_key = hashlib.sha256(encoded).hexdigest()
            cache_path = manager.project_dir / "cache" / "analysis" / f"{cache_key}.json"
        if cache_path is not None and cache_path.is_file():
            result = json.loads(cache_path.read_text(encoding="utf-8"))
            cache_hit = True
        else:
            result = analyzer(path)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=cache_path.parent,
                    prefix=f".{cache_key}-",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    temporary = Path(stream.name)
                os.replace(temporary, cache_path)
        result = {
            **result,
            "cache": {"hit": cache_hit, "key": cache_key},
        }
        revision = None
        if save:
            if media_id is None or manager is None:
                raise ValueError("--save requires a project media ID target.")

            def operation(document):
                asset = document.find_media(media_id)
                assert asset is not None
                analysis = asset.metadata.setdefault("analysis", {})
                analysis[command.rsplit(".", 1)[-1]] = result
                return asset

            _, state = manager.mutate(
                command,
                f"Saved {command} for {media_id}",
                operation,
                command={"media_id": media_id},
            )
            revision = state.revision
        emit(
            _state(ctx),
            success_response(command, result, project_revision=revision),
            human=json.dumps(result, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@analyze_app.command("scenes")
def scenes(
    ctx: typer.Context,
    target: str,
    threshold: Annotated[float, typer.Option("--threshold")] = 0.4,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    state = _state(ctx)
    _emit_analysis(
        ctx,
        "analyze.scenes",
        target,
        lambda path: analyze_scenes(path, threshold=threshold, ffmpeg=state.config.tools.ffmpeg),
        save=save,
        cache_parameters={"threshold": threshold},
    )


@analyze_app.command("quality")
def quality(
    ctx: typer.Context,
    target: str,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    state = _state(ctx)
    _emit_analysis(
        ctx,
        "analyze.quality",
        target,
        lambda path: analyze_quality(
            path,
            ffmpeg=state.config.tools.ffmpeg,
            ffprobe=state.config.tools.ffprobe,
        ),
        save=save,
        cache_parameters={},
    )


@analyze_app.command("beats")
def beats(
    ctx: typer.Context,
    target: str,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    state = _state(ctx)
    _emit_analysis(
        ctx,
        "analyze.beats",
        target,
        lambda path: analyze_beats(path, ffmpeg=state.config.tools.ffmpeg),
        save=save,
        cache_parameters={},
    )


@analyze_app.command("transcript")
def transcript(
    ctx: typer.Context,
    target: str,
    model: Annotated[Path, typer.Option("--model", help="Local Whisper model directory.")],
    language: Annotated[str | None, typer.Option("--language")] = None,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    _emit_analysis(
        ctx,
        "analyze.transcript",
        target,
        lambda path: transcribe_local(path, model_path=model, language=language),
        save=save,
        cache_parameters={"model": str(model), "language": language},
    )


@analyze_app.command("song")
def song(
    ctx: typer.Context,
    target: str,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    state = _state(ctx)
    _emit_analysis(
        ctx,
        "analyze.song",
        target,
        lambda path: analyze_song_metadata(path, ffprobe=state.config.tools.ffprobe),
        save=save,
        cache_parameters={},
    )


@analyze_app.command("travel")
def travel(
    ctx: typer.Context,
    target: str,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    """Classify travel metadata conservatively and expose missing vision capability."""

    state = _state(ctx)
    _emit_analysis(
        ctx,
        "analyze.travel",
        target,
        lambda path: analyze_travel_metadata(
            path,
            probe_media(path, ffprobe=state.config.tools.ffprobe),
        ),
        save=save,
        cache_parameters={},
    )
