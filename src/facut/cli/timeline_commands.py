"""Timeline, clip, and transition command groups."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.core.command_engine import CommandEngine
from facut.core.timeline_engine import parse_time
from facut.responses import success_response
from facut.transitions.registry import registry


timeline_app = typer.Typer(help="Create tracks and edit the non-destructive timeline.")
track_app = typer.Typer(help="Manage timeline tracks.")
clip_app = typer.Typer(help="Move, split, trim, duplicate, and delete clips.")
transition_app = typer.Typer(help="List and apply registered transition plugins.")
timeline_app.add_typer(track_app, name="track")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _execute(ctx: typer.Context, action: str, params: dict, dry_run: bool):
    from facut.cli.main import emit

    try:
        result = CommandEngine(manager_for(_state(ctx))).execute(action, params, dry_run=dry_run)
        emit(
            _state(ctx),
            success_response(
                action,
                {"result": result["data"], "dry_run": dry_run},
                project_revision=result["project_revision"],
            ),
            human=f"[green]{action} completed.[/green]\n{json.dumps(result['data'], ensure_ascii=False, indent=2)}",
        )
    except Exception as error:
        _abort(ctx, action, error)


@track_app.command("add")
def track_add(
    ctx: typer.Context,
    track_type: Annotated[str, typer.Option("--type")],
    name: Annotated[str, typer.Option("--name")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Add a video, audio, image, subtitle, adjustment, or mask track."""

    _execute(ctx, "timeline.track.add", {"type": track_type, "name": name}, dry_run)


@timeline_app.command("add")
def timeline_add(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    track: Annotated[str, typer.Option("--track")],
    at: Annotated[str, typer.Option("--at")] = "0",
    source_in: Annotated[str, typer.Option("--in")] = "0",
    source_out: Annotated[str | None, typer.Option("--out")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Place a source range on a timeline track."""

    params = {"media_id": media_id, "track": track, "at": at, "in": source_in}
    if source_out is not None:
        params["out"] = source_out
    _execute(ctx, "timeline.add", params, dry_run)


@timeline_app.command("show")
def timeline_show(
    ctx: typer.Context,
    from_time: Annotated[str | None, typer.Option("--from")] = None,
    to_time: Annotated[str | None, typer.Option("--to")] = None,
) -> None:
    """Show clips, transitions, and exact frame-aware positions."""

    from facut.cli.main import emit

    try:
        document = manager_for(_state(ctx)).require_document()
        lower = float(parse_time(from_time, document.project.fps).seconds) if from_time else 0.0
        upper = float(parse_time(to_time, document.project.fps).seconds) if to_time else float("inf")
        tracks = []
        lines = []
        for track in sorted(document.tracks, key=lambda item: item.order):
            clips = []
            for clip in track.clips:
                if clip.end < lower or clip.timeline_start > upper:
                    continue
                item = clip.model_dump(mode="json")
                item["start_time"] = parse_time(clip.timeline_start, document.project.fps).as_dict()
                item["end_time"] = parse_time(clip.end, document.project.fps).as_dict()
                clips.append(item)
                lines.append(
                    f"{track.id:<5} [{clip.id}] {item['start_time']['timecode']} → {item['end_time']['timecode']}  {clip.media_id}"
                )
            tracks.append({"id": track.id, "name": track.name, "type": track.type.value, "clips": clips})
        data = {
            "duration": parse_time(document.project.duration, document.project.fps).as_dict(),
            "tracks": tracks,
            "transitions": [item.model_dump(mode="json") for item in document.transitions],
        }
        emit(
            _state(ctx),
            success_response("timeline.show", data, project_revision=document.revision),
            human="\n".join(lines) or "Timeline is empty.",
        )
    except Exception as error:
        _abort(ctx, "timeline.show", error)


@clip_app.command("move")
def clip_move(
    ctx: typer.Context,
    clip_id: str,
    to: Annotated[str | None, typer.Option("--to")] = None,
    delta: Annotated[str | None, typer.Option("--delta")] = None,
    track: Annotated[str | None, typer.Option("--track")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    params = {"clip_id": clip_id, "to": to, "delta": delta, "track_id": track}
    _execute(ctx, "clip.move", {key: value for key, value in params.items() if value is not None}, dry_run)


@clip_app.command("split")
def clip_split(
    ctx: typer.Context,
    clip_id: str,
    at: Annotated[str, typer.Option("--at")],
    timeline_position: Annotated[bool, typer.Option("--timeline-position")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    _execute(ctx, "clip.split", {"clip_id": clip_id, "at": at, "timeline_position": timeline_position}, dry_run)


@clip_app.command("trim")
def clip_trim(
    ctx: typer.Context,
    clip_id: str,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    source_in: Annotated[str | None, typer.Option("--in")] = None,
    source_out: Annotated[str | None, typer.Option("--out")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    params = {
        "clip_id": clip_id,
        "start_delta": start,
        "end_delta": end,
        "source_in": source_in,
        "source_out": source_out,
    }
    _execute(ctx, "clip.trim", {key: value for key, value in params.items() if value is not None}, dry_run)


@clip_app.command("delete")
def clip_delete(
    ctx: typer.Context,
    clip_id: str,
    ripple: Annotated[bool, typer.Option("--ripple")] = False,
    leave_gap: Annotated[bool, typer.Option("--leave-gap")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    if ripple and leave_gap:
        _abort(ctx, "clip.delete", ValueError("Choose either --ripple or --leave-gap."))
        return
    _execute(ctx, "clip.delete", {"clip_id": clip_id, "ripple": ripple}, dry_run)


@transition_app.command("list")
def transition_list(ctx: typer.Context) -> None:
    from facut.cli.main import emit

    definitions = [item.as_dict() for item in registry.list()]
    emit(
        _state(ctx),
        success_response("transition.list", definitions),
        human="\n".join(f"{item['name']:<18} {item['category']}" for item in definitions),
    )


@transition_app.command("describe")
def transition_describe(ctx: typer.Context, name: str) -> None:
    from facut.cli.main import emit

    try:
        data = registry.get(name).as_dict()
        emit(_state(ctx), success_response("transition.describe", data), human=json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as error:
        _abort(ctx, "transition.describe", error)


@transition_app.command("add")
def transition_add(
    ctx: typer.Context,
    transition_type: Annotated[str, typer.Option("--type")],
    duration: Annotated[str | None, typer.Option("--duration")] = None,
    from_clip: Annotated[str | None, typer.Option("--from")] = None,
    to_clip: Annotated[str | None, typer.Option("--to")] = None,
    track: Annotated[str | None, typer.Option("--track")] = None,
    at: Annotated[str | None, typer.Option("--at")] = None,
    direction: Annotated[str | None, typer.Option("--direction")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    params = {
        "type": transition_type,
        "duration": duration,
        "from": from_clip,
        "to": to_clip,
        "track_id": track,
        "at": at,
        "parameters": {"direction": direction} if direction else {},
    }
    _execute(ctx, "transition.add", {key: value for key, value in params.items() if value is not None}, dry_run)


@transition_app.command("remove")
def transition_remove(
    ctx: typer.Context,
    transition_id: str,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    _execute(ctx, "transition.remove", {"transition_id": transition_id}, dry_run)
