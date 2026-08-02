"""One-sequence, multi-platform deterministic delivery."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.core.sequences import materialize_sequence
from facut.render import FFmpegBackend
from facut.render.presets import (
    RENDER_PRESETS,
    list_render_presets,
    resolve_render_preset,
)
from facut.responses import success_response


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def delivery_presets_command(ctx: typer.Context) -> None:
    """List machine-readable delivery presets and their provenance."""

    from facut.cli.main import emit

    data = list_render_presets()
    emit(
        _state(ctx),
        success_response("delivery-presets", data),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def deliver_command(
    ctx: typer.Context,
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    preset: Annotated[str, typer.Option("--preset")] = "youtube-4k-sdr",
    also: Annotated[
        str | None,
        typer.Option("--also", help="Comma-separated additional presets."),
    ] = None,
    basename: Annotated[str | None, typer.Option("--basename")] = None,
    sequence: Annotated[str | None, typer.Option("--sequence")] = None,
    subtitles: Annotated[
        str, typer.Option("--subtitles", help="burn or none.")
    ] = "burn",
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Render a master sequence into several platform derivatives."""

    from facut.cli.main import emit, fail

    command = "deliver"
    state = _state(ctx)
    try:
        if subtitles not in {"burn", "none"}:
            raise ValueError("--subtitles must be burn or none.")
        manager = manager_for(state)
        document = manager.require_document()
        if sequence:
            document = materialize_sequence(document, sequence)
        if subtitles == "none":
            document = document.model_copy(deep=True)
            document.subtitle_cues = []
            document.text_overlays = []
        names = [preset]
        if also:
            names.extend(item.strip() for item in also.split(",") if item.strip())
        names = list(dict.fromkeys(names))
        unknown = [name for name in names if name not in RENDER_PRESETS]
        if unknown:
            raise ValueError(f'Unknown delivery preset(s): {", ".join(unknown)}.')
        safe_base = basename or sequence or document.project.name
        safe_base = re.sub(r"[^\w.-]+", "-", safe_base, flags=re.UNICODE).strip("-.")
        safe_base = safe_base or "facut-delivery"
        output_dir.mkdir(parents=True, exist_ok=True)
        backend = FFmpegBackend(state.config.tools.ffmpeg)
        results = []
        warnings: list[str] = []
        for name in names:
            settings = resolve_render_preset(name, source_fps=document.project.fps)
            destination = output_dir / f"{safe_base}-{name}.mp4"
            result = backend.render(
                document,
                manager.project_dir,
                destination,
                width=settings["width"],
                height=settings["height"],
                fps=settings["fps"],
                bitrate=settings["bitrate"],
                audio_bitrate=settings["audio_bitrate"],
                audio_sample_rate=settings["audio_sample_rate"],
                color_space=settings.get("color_space"),
                hardware=hardware,
                overwrite=overwrite,
            )
            results.append(
                {"preset": name, "settings": settings, **result.as_dict()}
            )
            warnings.extend(result.warnings)
            if settings["width"] < settings["height"]:
                warnings.append(
                    f"{name}: vertical output uses deterministic clip fit; subject-aware auto reframe is not yet applied."
                )
        emit(
            state,
            success_response(
                command,
                {"deliveries": results, "subtitles": subtitles},
                warnings=list(dict.fromkeys(warnings)),
                project_revision=document.revision,
            ),
            human=json.dumps(results, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        fail(state, command, public_error(error))
