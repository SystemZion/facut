"""Subtitle and free-text command groups."""

from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.core.models import TextStyle
from facut.core.timeline_engine import parse_time
from facut.responses import success_response
from facut.subtitles.compiler import SubtitleCompiler
from facut.subtitles.editor import (
    add_text_overlay,
    import_cues,
    remove_cue,
    remove_text_overlay,
    require_subtitle_track,
    shift_track,
)
from facut.subtitles.formats import export_srt, export_vtt, parse_subtitle_file


subtitle_app = typer.Typer(help="Import, shift, inspect, export, and compile subtitles.")
text_app = typer.Typer(help="Add and manage timed free-text overlays.")

TEXT_TEMPLATES: dict[str, dict[str, Any]] = {
    "documentary-lower-third": {
        "x": "8%",
        "y": "82%",
        "font_size": 52.0,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 1.5,
        "background": "#00000099",
        "shadow": 2.0,
        "alignment": "left",
        "safe_area": True,
    },
    "chapter": {
        "x": "center",
        "y": "center",
        "font_size": 84.0,
        "color": "#FFFFFF",
        "stroke_width": 2.0,
        "shadow": 3.0,
        "alignment": "center",
        "safe_area": True,
    },
    "song-title": {
        "x": "8%",
        "y": "12%",
        "font_size": 46.0,
        "color": "#FFFFFF",
        "stroke_width": 1.0,
        "background": "#101820B3",
        "shadow": 2.0,
        "alignment": "left",
        "safe_area": True,
    },
    "caption-box": {
        "x": "center",
        "y": "88%",
        "font_size": 48.0,
        "color": "#FFFFFF",
        "stroke_width": 0.0,
        "background": "#000000B3",
        "shadow": 0.0,
        "alignment": "center",
        "safe_area": True,
    },
}


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _serialized(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialized(item) for item in value]
    return value


def _emit_mutation(
    ctx: typer.Context,
    command: str,
    result: Any,
    revision: int,
    *,
    dry_run: bool,
) -> None:
    from facut.cli.main import emit

    data = {"result": _serialized(result), "dry_run": dry_run}
    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=f"[green]{command} completed.[/green]\n"
        f"{json.dumps(data['result'], ensure_ascii=False, indent=2)}",
    )


@subtitle_app.command("import")
def subtitle_import(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="SRT or WebVTT file.")],
    track: Annotated[str, typer.Option("--track", help="Destination subtitle track.")],
    subtitle_format: Annotated[
        str | None, typer.Option("--format", help="Override srt/vtt format detection.")
    ] = None,
    offset: Annotated[
        str, typer.Option("--offset", help="Signed timeline offset, for example +500ms.")
    ] = "0",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import all cues from an SRT or VTT file in one project revision."""

    command = "subtitle.import"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        offset_seconds = float(parse_time(offset, document.project.fps).seconds)
        parsed = parse_subtitle_file(source, subtitle_format)

        def operation(candidate):
            return import_cues(
                candidate,
                track,
                parsed,
                offset=offset_seconds,
                source=source.name,
            )

        result, state = manager.mutate(
            command,
            f"Imported {len(parsed)} subtitle cues into {track}",
            operation,
            command={
                "source": source.name,
                "track": track,
                "format": subtitle_format,
                "offset": offset,
            },
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("shift")
def subtitle_shift(
    ctx: typer.Context,
    track: Annotated[str, typer.Argument(help="Subtitle track ID.")],
    offset: Annotated[str, typer.Option("--offset", help="Signed offset.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Shift every cue on a subtitle track."""

    command = "subtitle.shift"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        offset_seconds = float(parse_time(offset, document.project.fps).seconds)
        result, state = manager.mutate(
            command,
            f"Shifted subtitle track {track} by {offset}",
            lambda candidate: shift_track(candidate, track, offset_seconds),
            command={"track": track, "offset": offset},
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("list")
def subtitle_list(
    ctx: typer.Context,
    track: Annotated[str | None, typer.Option("--track")] = None,
) -> None:
    """List subtitle cues in timeline order."""

    from facut.cli.main import emit

    command = "subtitle.list"
    try:
        document = manager_for(_state(ctx)).require_document()
        if track is not None:
            require_subtitle_track(document, track)
        cues = [
            cue
            for cue in document.subtitle_cues
            if track is None or cue.track_id == track
        ]
        data = [cue.model_dump(mode="json") for cue in cues]
        human = "\n".join(
            f"{cue.id}  {cue.start:8.3f} → {cue.end:8.3f}  {cue.text}"
            for cue in cues
        )
        emit(
            _state(ctx),
            success_response(command, data, project_revision=document.revision),
            human=human or "No subtitle cues.",
        )
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("remove")
def subtitle_remove(
    ctx: typer.Context,
    cue_id: Annotated[str, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Remove one subtitle cue."""

    command = "subtitle.remove"
    try:
        manager = manager_for(_state(ctx))
        result, state = manager.mutate(
            command,
            f"Removed subtitle cue {cue_id}",
            lambda candidate: remove_cue(candidate, cue_id),
            command={"cue_id": cue_id},
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("export")
def subtitle_export(
    ctx: typer.Context,
    track: Annotated[str, typer.Argument(help="Subtitle track ID.")],
    output: Annotated[Path, typer.Option("--output", "-o")],
    subtitle_format: Annotated[
        str | None, typer.Option("--format", help="srt or vtt; defaults to extension.")
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Export one subtitle track without modifying the project."""

    from facut.cli.main import emit

    command = "subtitle.export"
    try:
        document = manager_for(_state(ctx)).require_document()
        require_subtitle_track(document, track)
        selected = (subtitle_format or output.suffix.lstrip(".")).lower()
        cues = [cue for cue in document.subtitle_cues if cue.track_id == track]
        if selected == "srt":
            content = export_srt(cues)
        elif selected in {"vtt", "webvtt"}:
            content = export_vtt(cues)
            selected = "vtt"
        else:
            raise ValueError("Subtitle export format must be srt or vtt.")
        if output.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{output}" exists; use --overwrite to replace it.'
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")
        data = {"output": str(output.resolve()), "format": selected, "cue_count": len(cues)}
        emit(
            _state(ctx),
            success_response(command, data, project_revision=document.revision),
            human=f"[green]Exported {len(cues)} cues to {output}.[/green]",
        )
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("compile-ass")
def subtitle_compile_ass(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Compile all project subtitles and text overlays to an ASS render sidecar."""

    from facut.cli.main import emit

    command = "subtitle.compile-ass"
    try:
        document = manager_for(_state(ctx)).require_document()
        plan = SubtitleCompiler().compile(document)
        written = plan.write(output, overwrite=overwrite)
        data = {
            "output": str(written.resolve()),
            "format": plan.format,
            "cue_count": plan.cue_count,
            "text_overlay_count": plan.text_overlay_count,
        }
        emit(
            _state(ctx),
            success_response(command, data, project_revision=document.revision),
            human=f"[green]Compiled ASS sidecar to {written}.[/green]",
        )
    except Exception as error:
        _abort(ctx, command, error)


@text_app.command("add")
def text_add(
    ctx: typer.Context,
    text: Annotated[str, typer.Option("--text")],
    at: Annotated[str, typer.Option("--at")],
    duration: Annotated[str, typer.Option("--duration")],
    x: Annotated[str | None, typer.Option("--x")] = None,
    y: Annotated[str | None, typer.Option("--y")] = None,
    track: Annotated[str | None, typer.Option("--track")] = None,
    template: Annotated[str | None, typer.Option("--template")] = None,
    font: Annotated[str | None, typer.Option("--font")] = None,
    font_size: Annotated[float | None, typer.Option("--font-size")] = None,
    color: Annotated[str | None, typer.Option("--color")] = None,
    stroke_color: Annotated[str | None, typer.Option("--stroke-color")] = None,
    stroke_width: Annotated[float | None, typer.Option("--stroke-width")] = None,
    background: Annotated[str | None, typer.Option("--background")] = None,
    shadow: Annotated[float | None, typer.Option("--shadow")] = None,
    alignment: Annotated[str | None, typer.Option("--alignment")] = None,
    letter_spacing: Annotated[
        float | None, typer.Option("--letter-spacing")
    ] = None,
    safe_area: Annotated[
        bool | None, typer.Option("--safe-area/--no-safe-area")
    ] = None,
    entrance: Annotated[str | None, typer.Option("--in-animation")] = None,
    exit_animation: Annotated[str | None, typer.Option("--out-animation")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Add a timed, styled text overlay."""

    command = "text.add"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        at_seconds = float(parse_time(at, document.project.fps).seconds)
        duration_seconds = float(parse_time(duration, document.project.fps).seconds)
        if template is not None and template not in TEXT_TEMPLATES:
            raise ValueError(
                f'Unknown text template "{template}". Available: '
                f'{", ".join(TEXT_TEMPLATES)}.'
            )
        preset = dict(TEXT_TEMPLATES.get(template or "", {}))
        resolved_x = x or str(preset.pop("x", "center"))
        resolved_y = y or str(preset.pop("y", "80%"))
        style_values = {
            **preset,
            "font_family": font,
            "font_size": font_size,
            "color": color,
            "stroke_color": stroke_color,
            "stroke_width": stroke_width,
            "background": background,
            "shadow": shadow,
            "alignment": alignment,
            "letter_spacing": letter_spacing,
            "safe_area": safe_area,
        }
        style = TextStyle.model_validate(
            {name: value for name, value in style_values.items() if value is not None}
        )

        def operation(candidate):
            return add_text_overlay(
                candidate,
                text=text,
                at=at_seconds,
                duration=duration_seconds,
                x=resolved_x,
                y=resolved_y,
                track_id=track,
                style=style,
                entrance=entrance,
                exit=exit_animation,
            )

        result, state = manager.mutate(
            command,
            f"Added text overlay at {at}",
            operation,
            command={
                "text": text,
                "at": at,
                "duration": duration,
                "x": resolved_x,
                "y": resolved_y,
                "track": track,
                "template": template,
            },
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)


@text_app.command("presets")
def text_presets(ctx: typer.Context) -> None:
    """List built-in professional title templates."""

    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response("text.presets", TEXT_TEMPLATES),
        human=json.dumps(TEXT_TEMPLATES, ensure_ascii=False, indent=2),
    )


@text_app.command("import-csv")
def text_import_csv(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument()],
    default_template: Annotated[
        str, typer.Option("--template")
    ] = "song-title",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Batch-add titles from text,at,duration[,template] CSV columns."""

    command = "text.import-csv"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        prepared = []
        for row in rows:
            template_name = row.get("template") or default_template
            if template_name not in TEXT_TEMPLATES:
                raise ValueError(f'Unknown text template "{template_name}".')
            preset = dict(TEXT_TEMPLATES[template_name])
            prepared.append(
                {
                    "text": row["text"],
                    "at": float(parse_time(row["at"], document.project.fps).seconds),
                    "duration": float(
                        parse_time(row["duration"], document.project.fps).seconds
                    ),
                    "x": preset.pop("x", "center"),
                    "y": preset.pop("y", "80%"),
                    "style": TextStyle.model_validate(preset),
                    "template": template_name,
                }
            )

        def operation(candidate):
            return [
                add_text_overlay(
                    candidate,
                    text=item["text"],
                    at=item["at"],
                    duration=item["duration"],
                    x=item["x"],
                    y=item["y"],
                    style=item["style"],
                )
                for item in prepared
            ]

        result, state = manager.mutate(
            command,
            f"Imported {len(prepared)} text overlays from {source.name}",
            operation,
            command={"source": source.name, "template": default_template},
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)


@text_app.command("list")
def text_list(ctx: typer.Context) -> None:
    """List free-text overlays."""

    from facut.cli.main import emit

    command = "text.list"
    try:
        document = manager_for(_state(ctx)).require_document()
        data = [item.model_dump(mode="json") for item in document.text_overlays]
        human = "\n".join(
            f"{item.id}  {item.at:8.3f} → {item.end:8.3f}  {item.text}"
            for item in document.text_overlays
        )
        emit(
            _state(ctx),
            success_response(command, data, project_revision=document.revision),
            human=human or "No text overlays.",
        )
    except Exception as error:
        _abort(ctx, command, error)


@text_app.command("remove")
def text_remove(
    ctx: typer.Context,
    overlay_id: Annotated[str, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Remove one free-text overlay."""

    command = "text.remove"
    try:
        manager = manager_for(_state(ctx))
        result, state = manager.mutate(
            command,
            f"Removed text overlay {overlay_id}",
            lambda candidate: remove_text_overlay(candidate, overlay_id),
            command={"overlay_id": overlay_id},
            dry_run=dry_run,
        )
        _emit_mutation(ctx, command, result, state.revision, dry_run=dry_run)
    except Exception as error:
        _abort(ctx, command, error)
