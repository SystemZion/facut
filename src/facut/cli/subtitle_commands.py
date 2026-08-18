"""Subtitle and free-text command groups."""

from __future__ import annotations

import json
import csv
import os
import subprocess
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
from facut.subtitles.templates import (
    TEXT_TEMPLATES,
    list_text_templates,
    resolve_text_template,
)
from facut.analysis.engine import transcribe_local
from facut.exceptions import DependencyMissingError
from facut.render import FFmpegBackend
from facut.subtitles.director import (
    add_glossary_entry,
    apply_transcript_plan,
    build_transcript_plan,
    load_glossary,
    load_transcript_plan,
    save_transcript_plan,
)


subtitle_app = typer.Typer(help="Import, shift, inspect, export, and compile subtitles.")
text_app = typer.Typer(help="Add and manage timed free-text overlays.")
glossary_app = typer.Typer(help="Manage project-specific names, places, and terminology.")
subtitle_app.add_typer(glossary_app, name="glossary")

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


def _diarize(transcripts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    executable = os.environ.get("FACUT_DIARIZATION_PROVIDER")
    if not executable:
        raise DependencyMissingError(
            "Speaker diarization was requested, but no diarization provider is configured.",
            suggestion=(
                "Set FACUT_DIARIZATION_PROVIDER to a local executable that accepts transcript JSON "
                "on stdin and returns the same structure with segment speaker fields."
            ),
        )
    completed = subprocess.run(
        [executable],
        input=json.dumps({"transcripts": transcripts}, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise DependencyMissingError(
            "The configured speaker diarization provider failed.",
            details={"stderr": completed.stderr[-4000:]},
        )
    payload = json.loads(completed.stdout)
    if not isinstance(payload.get("transcripts"), dict):
        raise ValueError('Diarization provider output requires a "transcripts" object.')
    return payload["transcripts"]


@subtitle_app.command("transcribe")
def subtitle_transcribe(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    media: Annotated[list[str] | None, typer.Option("--media")] = None,
    model: Annotated[Path | None, typer.Option("--model")] = None,
    language: Annotated[str, typer.Option("--language")] = "zh",
    speaker_diarization: Annotated[bool, typer.Option("--speaker-diarization")] = False,
    word_timestamps: Annotated[bool, typer.Option("--word-timestamps/--no-word-timestamps")] = True,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Transcribe active timeline sources into a review-first caption plan."""

    command = "subtitle.transcribe"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        destination = output.expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
        model_path = model or state.config.models.resolve("srt_model")
        selected_ids = set(media or [])
        if not selected_ids:
            selected_ids = {
                clip.media_id
                for track in document.tracks
                if track.type.value in {"video", "audio"}
                for clip in track.clips
                if clip.enabled
            }
        if not selected_ids:
            raise ValueError("No enabled video or audio clips are present on the timeline.")
        transcripts: dict[str, dict[str, Any]] = {}
        for media_id in sorted(selected_ids):
            asset = document.find_media(media_id)
            if asset is None:
                raise ValueError(f'Unknown media "{media_id}".')
            transcripts[media_id] = transcribe_local(
                manager.resolve_path(asset.path),
                model_path=model_path,
                language=language,
                external_python=state.config.tools.analysis_python,
                word_timestamps=word_timestamps,
            )
        diarization_status = "disabled"
        if speaker_diarization:
            transcripts = _diarize(transcripts)
            diarization_status = "provider"
        plan = build_transcript_plan(
            document,
            manager.project_dir,
            transcripts,
            language=language,
            model=Path(model_path).name,
            word_timestamps=word_timestamps,
            speaker_diarization=diarization_status,
        )
        save_transcript_plan(plan, destination)
        data = plan.model_dump(mode="json")
        data["output"] = str(destination)
        from facut.cli.main import emit

        emit(
            state,
            success_response(command, data, warnings=plan.warnings, project_revision=document.revision),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("review")
def subtitle_review(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    cues: Annotated[list[str] | None, typer.Option("--cue")] = None,
    status: Annotated[str, typer.Option("--status")] = "approved",
    all_cues: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Inspect or explicitly approve/reject transcript cues without changing their raw text."""

    command = "subtitle.review"
    try:
        if status not in {"draft", "approved", "rejected"}:
            raise ValueError("--status must be draft, approved, or rejected.")
        plan = load_transcript_plan(plan_file)
        selected = {item.id for item in plan.cues} if all_cues else set(cues or [])
        unknown = selected - {item.id for item in plan.cues}
        if unknown:
            raise ValueError("Unknown subtitle cue(s): " + ", ".join(sorted(unknown)))
        for item in plan.cues:
            if item.id in selected:
                item.status = status
        plan.status = "ready" if plan.cues and all(item.status != "draft" for item in plan.cues) else "review_required"
        save_transcript_plan(plan, plan_file)
        from facut.cli.main import emit

        emit(
            _state(ctx),
            success_response(command, plan.model_dump(mode="json")),
            human=json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("apply")
def subtitle_apply(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    approved_only: Annotated[bool, typer.Option("--approved-only/--include-drafts")] = True,
    track: Annotated[str, typer.Option("--track")] = "S_DIALOGUE",
) -> None:
    """Apply reviewed captions in one undoable revision with a real installed font."""

    command = "subtitle.apply"
    try:
        manager = manager_for(_state(ctx))
        data = apply_transcript_plan(
            manager,
            load_transcript_plan(plan_file),
            approved_only=approved_only,
            track_id=track,
        )
        from facut.cli.main import emit

        emit(
            _state(ctx),
            success_response(command, data, project_revision=data["project_revision"]),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@subtitle_app.command("proof")
def subtitle_proof(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    height: Annotated[int, typer.Option("--height")] = 540,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Render a real low-resolution subtitle proof from the active timeline."""

    command = "subtitle.proof"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        document = manager.require_document()
        if not document.subtitle_cues and not document.text_overlays:
            raise ValueError("The project has no subtitles or text overlays to proof.")
        result = FFmpegBackend(state.config.tools.ffmpeg).preview_range(
            document,
            manager.project_dir,
            output,
            start=0,
            end=document.project.duration,
            height=height,
            fps=min(24, document.project.fps),
            overwrite=overwrite,
        )
        from facut.cli.main import emit

        data = {"output": str(result.output), "duration": result.duration, "font_audit_required": True}
        emit(
            state,
            success_response(command, data, warnings=result.warnings, project_revision=document.revision),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@glossary_app.command("add")
def subtitle_glossary_add(
    ctx: typer.Context,
    term: Annotated[str, typer.Argument()],
    kind: Annotated[str, typer.Option("--type")] = "term",
) -> None:
    """Add a project name, place, attraction, or domain term for ASR review."""

    command = "subtitle.glossary.add"
    try:
        manager = manager_for(_state(ctx))
        data = add_glossary_entry(manager.project_dir, term, kind)
        from facut.cli.main import emit

        emit(
            _state(ctx),
            success_response(command, data, project_revision=manager.require_document().revision),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@glossary_app.command("list")
def subtitle_glossary_list(ctx: typer.Context) -> None:
    """List the current project glossary."""

    command = "subtitle.glossary.list"
    try:
        manager = manager_for(_state(ctx))
        data = load_glossary(manager.project_dir)
        from facut.cli.main import emit

        emit(
            _state(ctx),
            success_response(command, data, project_revision=manager.require_document().revision),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        _abort(ctx, command, error)


@text_app.command("add")
def text_add(
    ctx: typer.Context,
    text: Annotated[str, typer.Option("--text")],
    at: Annotated[str, typer.Option("--at")],
    duration: Annotated[str, typer.Option("--duration")],
    subtitle: Annotated[
        str | None, typer.Option("--subtitle", help="Optional secondary line.")
    ] = None,
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
    accent_color: Annotated[
        str | None, typer.Option("--accent-color", help="Template accent override.")
    ] = None,
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
        definition = resolve_text_template(template) if template else {}
        preset = dict(definition.get("style", {}))
        resolved_x = x or str(definition.get("x", "center"))
        resolved_y = y or str(definition.get("y", "80%"))
        template_parameters = dict(definition.get("parameters", {}))
        if accent_color is not None:
            template_parameters["accent_color"] = accent_color
        explicit_style = {
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
        # TextStyle supplies the system defaults.  Feed it the resolved template
        # first, then only values the caller actually supplied.  This preserves
        # the public precedence contract: explicit option > template > default.
        style = TextStyle.model_validate(
            {
                **preset,
                **{name: value for name, value in explicit_style.items() if value is not None},
            }
        )

        def operation(candidate):
            return add_text_overlay(
                candidate,
                text=text,
                subtitle=subtitle,
                at=at_seconds,
                duration=duration_seconds,
                x=resolved_x,
                y=resolved_y,
                track_id=track,
                style=style,
                entrance=entrance,
                exit=exit_animation,
                template=template,
                template_parameters=template_parameters,
            )

        result, state = manager.mutate(
            command,
            f"Added text overlay at {at}",
            operation,
            command={
                "text": text,
                "subtitle": subtitle,
                "at": at,
                "duration": duration,
                "x": resolved_x,
                "y": resolved_y,
                "track": track,
                "template": template,
                "accent_color": accent_color,
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
        success_response("text.presets", list_text_templates()),
        human=json.dumps(list_text_templates(), ensure_ascii=False, indent=2),
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
            definition = resolve_text_template(template_name)
            prepared.append(
                {
                    "text": row["text"],
                    "at": float(parse_time(row["at"], document.project.fps).seconds),
                    "duration": float(
                        parse_time(row["duration"], document.project.fps).seconds
                    ),
                    "x": definition.get("x", "center"),
                    "y": definition.get("y", "80%"),
                    "style": TextStyle.model_validate(definition.get("style", {})),
                    "template": template_name,
                    "template_parameters": definition.get("parameters", {}),
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
                    template=item["template"],
                    template_parameters=item["template_parameters"],
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
