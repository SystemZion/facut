"""Preview and final rendering commands."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.core.timeline_engine import parse_time
from facut.render import FFmpegBackend
from facut.render.incremental import IncrementalRenderer, incremental_eligibility
from facut.render.presets import RENDER_PRESETS, resolve_render_preset
from facut.responses import success_response


preview_app = typer.Typer(help="Render fast low-resolution timeline previews.")

def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _preview_key(document: Any, parameters: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "project": document.model_dump(mode="json"),
            "parameters": parameters,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _progress_callback(
    state: Any,
    document: Any,
    command: str,
    *,
    jsonl: bool,
):
    """Create a human or JSON Lines progress sink without corrupting JSON mode."""

    if state.json_output and not jsonl:
        return None
    if state.quiet and not jsonl:
        return None
    video_clips = sorted(
        (
            clip
            for track in document.tracks
            if track.enabled and track.type.value in {"video", "image"}
            for clip in track.clips
            if clip.enabled
        ),
        key=lambda clip: clip.timeline_start,
    )
    last_percent = -1

    def callback(event: dict[str, Any]) -> None:
        nonlocal last_percent
        elapsed = float(event.get("out_time_seconds", 0.0) or 0.0)
        current = next(
            (
                clip
                for clip in video_clips
                if clip.timeline_start <= elapsed < clip.end
            ),
            video_clips[-1] if video_clips else None,
        )
        payload = {
            **event,
            "command": command,
            "current_clip_id": current.id if current is not None else None,
        }
        percent = round(float(payload.get("progress", 0.0)) * 100)
        if jsonl:
            typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return
        if percent == last_percent and event.get("progress") != 1:
            return
        last_percent = percent
        fps = float(payload.get("fps", 0.0) or 0.0)
        eta = payload.get("eta_seconds")
        eta_text = "--" if eta is None else f"{float(eta):.1f}s"
        typer.echo(
            f"{command}: {percent:3d}%  {fps:6.1f} fps  ETA {eta_text}"
            f"  clip {payload['current_clip_id'] or '-'}",
            err=True,
        )

    return callback


@preview_app.command("range")
def preview_range(
    ctx: typer.Context,
    from_time: Annotated[str, typer.Option("--from")],
    to_time: Annotated[str, typer.Option("--to")],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    height: Annotated[int, typer.Option("--height")] = 540,
    fps: Annotated[float, typer.Option("--fps")] = 24.0,
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    jsonl_progress: Annotated[
        bool,
        typer.Option(
            "--jsonl-progress",
            help="Emit newline-delimited JSON progress events.",
        ),
    ] = False,
) -> None:
    """Render a bounded, cached low-resolution preview."""

    from facut.cli.main import emit

    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        start = float(parse_time(from_time, document.project.fps).seconds)
        end = float(parse_time(to_time, document.project.fps).seconds)
        destination = output or manager.project_dir / "previews" / "preview-range.mp4"
        parameters = {
            "start": start,
            "end": end,
            "height": height,
            "fps": fps,
            "hardware": hardware,
        }
        key = _preview_key(document, parameters)
        cache_path = manager.project_dir / "cache" / "previews" / f"{key}.mp4"
        cached = cache_path.is_file()
        if cached:
            if destination.exists() and not overwrite and destination.resolve() != cache_path.resolve():
                raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.resolve() != cache_path.resolve():
                shutil.copy2(cache_path, destination)
            result_data = {
                "status": "success",
                "output": str(destination.resolve()),
                "duration": end - start,
                "cached": True,
                "encoder": None,
                "hardware": None,
            }
            warnings: list[str] = []
        else:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            result = FFmpegBackend(state.config.tools.ffmpeg).preview_range(
                document,
                manager.project_dir,
                cache_path,
                start=start,
                end=end,
                height=height,
                fps=fps,
                hardware=hardware,
                overwrite=False,
                progress=_progress_callback(
                    state,
                    document,
                    "preview.range",
                    jsonl=jsonl_progress,
                ),
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.resolve() != cache_path.resolve():
                if destination.exists():
                    if not overwrite:
                        raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
                    destination.unlink()
                shutil.copy2(cache_path, destination)
            result_data = result.as_dict()
            result_data["output"] = str(destination.resolve())
            result_data["cached"] = False
            warnings = result.warnings
        emit(
            state,
            success_response(
                "preview.range",
                result_data,
                warnings=warnings,
                project_revision=document.revision,
            ),
            human=f"[green]Preview rendered:[/green] {destination.resolve()}",
        )
    except Exception as error:
        _abort(ctx, "preview.range", error)


@preview_app.command("timeline")
def preview_timeline(
    ctx: typer.Context,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    height: Annotated[int, typer.Option("--height")] = 540,
    fps: Annotated[float, typer.Option("--fps")] = 24.0,
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    jsonl_progress: Annotated[
        bool,
        typer.Option("--jsonl-progress", help="Emit newline-delimited JSON progress events."),
    ] = False,
) -> None:
    """Render and cache a low-resolution preview of the complete timeline."""

    from facut.cli.main import emit

    command = "preview.timeline"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        if document.project.duration <= 0:
            raise ValueError("The timeline is empty.")
        destination = output or manager.project_dir / "previews" / "preview-timeline.mp4"
        parameters = {
            "start": 0.0,
            "end": document.project.duration,
            "height": height,
            "fps": fps,
            "hardware": hardware,
        }
        key = _preview_key(document, parameters)
        cache_path = manager.project_dir / "cache" / "previews" / f"{key}.mp4"
        cached = cache_path.is_file()
        warnings: list[str] = []
        if not cached:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            result = FFmpegBackend(state.config.tools.ffmpeg).preview_range(
                document,
                manager.project_dir,
                cache_path,
                start=0.0,
                end=document.project.duration,
                height=height,
                fps=fps,
                hardware=hardware,
                overwrite=False,
                progress=_progress_callback(
                    state, document, command, jsonl=jsonl_progress
                ),
            )
            warnings = result.warnings
        if destination.exists() and destination.resolve() != cache_path.resolve():
            if not overwrite:
                raise FileExistsError(
                    f'Output "{destination}" already exists; use --overwrite.'
                )
            destination.unlink()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != cache_path.resolve():
            shutil.copy2(cache_path, destination)
        data = {
            "status": "success",
            "output": str(destination.resolve()),
            "duration": document.project.duration,
            "cached": cached,
        }
        emit(
            state,
            success_response(
                command,
                data,
                warnings=warnings,
                project_revision=document.revision,
            ),
            human=f"[green]Timeline preview rendered:[/green] {destination.resolve()}",
        )
    except Exception as error:
        _abort(ctx, command, error)


def render_command(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    preset: Annotated[str | None, typer.Option("--preset")] = None,
    codec: Annotated[str, typer.Option("--codec")] = "h264",
    width: Annotated[int | None, typer.Option("--width")] = None,
    height: Annotated[int | None, typer.Option("--height")] = None,
    fps: Annotated[float | None, typer.Option("--fps")] = None,
    bitrate: Annotated[str | None, typer.Option("--bitrate")] = None,
    audio_codec: Annotated[str, typer.Option("--audio-codec")] = "aac",
    audio_bitrate: Annotated[str | None, typer.Option("--audio-bitrate")] = None,
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    jsonl_progress: Annotated[
        bool,
        typer.Option(
            "--jsonl-progress",
            help="Emit newline-delimited JSON progress events.",
        ),
    ] = False,
    incremental: Annotated[
        bool,
        typer.Option(
            "--incremental/--no-incremental",
            help="Reuse unchanged clip renders when the timeline is eligible.",
        ),
    ] = True,
    sequence: Annotated[
        str | None,
        typer.Option("--sequence", help="Render a saved named sequence."),
    ] = None,
) -> None:
    """Render the full timeline to a playable video."""

    from facut.cli.main import emit

    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        if sequence is not None:
            from facut.core.sequences import materialize_sequence

            document = materialize_sequence(document, sequence)
        if preset:
            if preset not in RENDER_PRESETS:
                raise ValueError(
                    f'Unknown render preset "{preset}". Available: {", ".join(RENDER_PRESETS)}.'
                )
            settings = resolve_render_preset(
                preset, source_fps=document.project.fps, requested_fps=fps
            )
            width = width or settings["width"]
            height = height or settings["height"]
            fps = fps or settings["fps"]
            bitrate = bitrate or settings["bitrate"]
            audio_bitrate = audio_bitrate or settings["audio_bitrate"]
            audio_sample_rate = settings["audio_sample_rate"]
            color_space = settings.get("color_space")
        else:
            color_space = None
            audio_sample_rate = None
        audio_bitrate = audio_bitrate or "320k"
        backend = FFmpegBackend(state.config.tools.ffmpeg)
        progress = _progress_callback(
            state,
            document,
            "render",
            jsonl=jsonl_progress,
        )
        eligible, fallback_reason = incremental_eligibility(document)
        if incremental and eligible:
            result = IncrementalRenderer(
                backend, manager.project_dir / "cache" / "render"
            ).render(
                document,
                manager.project_dir,
                output,
                width=width,
                height=height,
                fps=fps,
                codec=codec,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                bitrate=bitrate,
                hardware=hardware,
                color_space=color_space,
                audio_sample_rate=audio_sample_rate,
                overwrite=overwrite,
                progress=progress,
            )
        else:
            result = backend.render(
                document,
                manager.project_dir,
                output,
                width=width,
                height=height,
                fps=fps,
                codec=codec,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                bitrate=bitrate,
                hardware=hardware,
                color_space=color_space,
                audio_sample_rate=audio_sample_rate,
                overwrite=overwrite,
                progress=progress,
            )
            if incremental and fallback_reason:
                result.warnings.append(
                    f"Incremental cache fallback: {fallback_reason}."
                )
        emit(
            state,
            success_response(
                "render",
                result.as_dict(),
                warnings=result.warnings,
                project_revision=document.revision,
            ),
            human=f"[green]Rendered:[/green] {result.output}",
        )
    except Exception as error:
        _abort(ctx, "render", error)
