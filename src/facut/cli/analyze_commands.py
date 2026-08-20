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


@analyze_app.command("batch")
def analyze_batch(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    engine: Annotated[str, typer.Option("--engine", help="auto, native, or python.")] = "auto",
    mode: Annotated[str, typer.Option("--mode", help="fast or deep native sampling.")] = "fast",
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    jobs: Annotated[int, typer.Option("--jobs", min=1, max=8)] = 3,
    asset_timeout: Annotated[float, typer.Option("--asset-timeout", min=1)] = 180.0,
    full: Annotated[bool, typer.Option("--full", help="Return inline evidence instead of a compact file reference.")] = False,
) -> None:
    """Analyze a media tree with the persistent native sidecar when available."""

    from facut.cli.main import emit

    command = "analyze.batch"
    try:
        if engine not in {"auto", "native", "python"}:
            raise ValueError("--engine must be auto, native, or python.")
        if mode not in {"fast", "deep"}:
            raise ValueError("--mode must be fast or deep.")
        root = folder.expanduser().resolve()
        cache = (output_directory or root / ".facut-native").expanduser().resolve()
        from facut.native import collect_batch_inputs

        inputs, collection = collect_batch_inputs(root, output_directory=cache, limit=limit)
        if not inputs:
            raise ValueError("No supported original video or image files were found.")
        files = [Path(str(item["original_path"])) for item in inputs]
        native_path = None
        native_warning: str | None = None
        if engine != "python":
            from facut.native import NativeClient, compact_batch_result, discover_native

            native_path = discover_native()
            if native_path:
                try:
                    data = NativeClient(native_path).batch_scan(
                        inputs,
                        output_directory=cache,
                        mode=mode,
                        jobs=jobs,
                        asset_timeout_seconds=asset_timeout,
                    )
                    payload = data if full else compact_batch_result(data)
                    payload.update({
                        "engine": "native", "executable": str(native_path),
                        "asset_count": len(files), "collection": collection,
                        "jobs": jobs, "asset_timeout_seconds": asset_timeout,
                    })
                    emit(_state(ctx), success_response(command, payload), human=f"Analyzed {len(files)} assets with FACUT Native.")
                    return
                except Exception as error:
                    if engine == "native":
                        raise
                    native_warning = (
                        "FACUT Native failed twice; the Python/FFmpeg fallback was used: "
                        f"{error}"
                    )
            if engine == "native":
                NativeClient()
        results = []
        state = _state(ctx)
        for path in files:
            try:
                results.append(
                    analyze_quality(
                        path,
                        ffmpeg=state.config.tools.ffmpeg,
                        ffprobe=state.config.tools.ffprobe,
                    )
                )
            except Exception as error:
                results.append({"source": str(path), "status": "error", "error": str(error)})
        emit(
            state,
            success_response(
                command,
                {
                    "engine": "python", "asset_count": len(files),
                    "results": results, "collection": collection,
                },
                warnings=[native_warning or "FACUT Native was unavailable; the existing Python/FFmpeg path was used."]
                if engine == "auto"
                else [],
            ),
            human=f"Analyzed {len(files)} assets with the Python fallback.",
        )
    except Exception as error:
        _abort(ctx, command, error)


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
    model: Annotated[
        Path | None,
        typer.Option("--model", help="Local Whisper model directory; defaults to downloaded srt_model."),
    ] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    save: Annotated[bool, typer.Option("--save")] = False,
) -> None:
    state = _state(ctx)
    resolved_model = model or state.config.models.resolve("srt_model")
    _emit_analysis(
        ctx,
        "analyze.transcript",
        target,
        lambda path: transcribe_local(
            path,
            model_path=resolved_model,
            language=language,
            external_python=state.config.tools.analysis_python,
        ),
        save=save,
        cache_parameters={"model": str(resolved_model), "language": language},
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
