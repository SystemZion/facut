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
from facut.responses import success_response


preview_app = typer.Typer(help="Render fast low-resolution timeline previews.")

RENDER_PRESETS: dict[str, dict[str, Any]] = {
    "youtube-1080p": {"width": 1920, "height": 1080, "fps": 30.0, "bitrate": "12M"},
    "youtube-4k": {"width": 3840, "height": 2160, "fps": 30.0, "bitrate": "45M"},
    "bilibili-1080p": {"width": 1920, "height": 1080, "fps": 30.0, "bitrate": "12M"},
    "tiktok-1080x1920": {"width": 1080, "height": 1920, "fps": 30.0, "bitrate": "12M"},
}


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
    audio_bitrate: Annotated[str, typer.Option("--audio-bitrate")] = "320k",
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Render the full timeline to a playable video."""

    from facut.cli.main import emit

    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        if preset:
            if preset not in RENDER_PRESETS:
                raise ValueError(
                    f'Unknown render preset "{preset}". Available: {", ".join(RENDER_PRESETS)}.'
                )
            settings = RENDER_PRESETS[preset]
            width = width or settings["width"]
            height = height or settings["height"]
            fps = fps or settings["fps"]
            bitrate = bitrate or settings["bitrate"]
        result = FFmpegBackend(state.config.tools.ffmpeg).render(
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
            overwrite=overwrite,
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
