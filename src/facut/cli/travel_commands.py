"""GPX route animation and subject-aware reframe commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.responses import success_response


map_app = typer.Typer(help="Parse GPX and render offline route animations.")
reframe_app = typer.Typer(help="Compile subject trajectories into rendered transform keyframes.")


def _state(ctx):
    from facut.cli.main import CliState
    return ctx.ensure_object(CliState)


def _emit(ctx, command, data, warnings=None, revision=None):
    from facut.cli.main import emit
    emit(_state(ctx), success_response(command, data, warnings=warnings or [], project_revision=revision), human=json.dumps(data, ensure_ascii=False, indent=2))


def _fail(ctx, command, error):
    from facut.cli.main import fail
    fail(_state(ctx), command, public_error(error))


@map_app.command("inspect")
def map_inspect(ctx: typer.Context, source: Path) -> None:
    try:
        from facut.travel import parse_gpx

        _emit(ctx, "map.inspect", parse_gpx(source))
    except Exception as error:
        _fail(ctx, "map.inspect", error)


@map_app.command("animate")
def map_animate(
    ctx: typer.Context,
    source: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    duration: Annotated[float, typer.Option("--duration", min=1)] = 8,
    width: Annotated[int, typer.Option("--width", min=320)] = 1920,
    height: Annotated[int, typer.Option("--height", min=240)] = 1080,
    fps: Annotated[int, typer.Option("--fps", min=1, max=120)] = 30,
    encoder: Annotated[str, typer.Option("--encoder")] = "libx264",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    try:
        from facut.travel import parse_gpx, render_route_video

        route = parse_gpx(source)
        if dry_run:
            data = {"route": {key: value for key, value in route.items() if key != "points"}, "output": str(output.resolve()), "duration": duration, "width": width, "height": height, "fps": fps, "encoder": encoder, "dry_run": True}
        else:
            state = _state(ctx)
            data = render_route_video(route, output, ffmpeg=state.config.tools.ffmpeg, width=width, height=height, fps=fps, duration=duration, encoder=encoder, overwrite=overwrite)
            data["dry_run"] = False
        _emit(ctx, "map.animate", data, warnings=data.get("warnings"))
    except Exception as error:
        _fail(ctx, "map.animate", error)


@reframe_app.command("plan")
def reframe_plan(
    ctx: typer.Context,
    clip_id: str,
    trajectory: Path,
    width: Annotated[int, typer.Option("--width", min=320)] = 1080,
    height: Annotated[int, typer.Option("--height", min=320)] = 1920,
    confidence: Annotated[float, typer.Option("--confidence", min=0, max=1)] = 0.4,
    smoothing: Annotated[int, typer.Option("--smoothing", min=1)] = 5,
    apply: Annotated[bool, typer.Option("--apply")] = False,
) -> None:
    try:
        from facut.travel import apply_reframe_plan, build_reframe_plan

        manager = manager_for(_state(ctx))
        plan = build_reframe_plan(manager, clip_id, trajectory, width=width, height=height, confidence_threshold=confidence, smoothing=smoothing)
        document = apply_reframe_plan(manager, plan) if apply else manager.require_document()
        data = {"plan": plan, "applied": apply}
        _emit(ctx, "reframe.plan", data, warnings=plan["limitations"], revision=document.revision)
    except Exception as error:
        _fail(ctx, "reframe.plan", error)
