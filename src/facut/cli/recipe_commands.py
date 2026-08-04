"""Declarative recipe planning and atomic edit/render orchestration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.qc.engine import QCEngine, resolve_qc_scope
from facut.recipe import RecipeEngine, load_recipe, recipe_json_schema
from facut.render.ffmpeg_backend import FFmpegBackend
from facut.render.presets import resolve_render_preset
from facut.responses import success_response


recipe_app = typer.Typer(help="Validate, plan and build declarative FACUT recipes.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: Any, *, warnings=None, revision=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, warnings=warnings or [], project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _write_json_atomic(path: Path, payload: dict[str, Any], *, overwrite: bool = False) -> Path:
    destination = path.expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


@recipe_app.command("validate")
def recipe_validate(ctx: typer.Context, source: Annotated[Path, typer.Argument()]) -> None:
    """Validate schema, references and the complete dry-run edit plan."""

    try:
        manager = manager_for(_state(ctx))
        recipe = load_recipe(source)
        data = RecipeEngine(manager).validate(recipe, recipe_path=source)
        data["schema"] = "facut://schemas/recipe"
        _emit(
            ctx,
            "recipe.validate",
            data,
            warnings=data.get("warnings"),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, "recipe.validate", error)


@recipe_app.command("plan")
def recipe_plan(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Compile a reviewable build plan without changing the project."""

    try:
        manager = manager_for(_state(ctx))
        recipe = load_recipe(source)
        plan = RecipeEngine(manager).plan(recipe, recipe_path=source)
        payload = plan.as_dict()
        saved = _write_json_atomic(output, payload, overwrite=overwrite)
        payload["output"] = str(saved)
        _emit(
            ctx,
            "recipe.plan",
            payload,
            warnings=plan.warnings,
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, "recipe.plan", error)


def _render_recipe(ctx: typer.Context, request: dict[str, Any], destination: Path) -> dict[str, Any]:
    state = _state(ctx)
    manager = manager_for(state)
    document = manager.require_document()
    parameters = dict(request.get("parameters") or {})
    preset_name = request.get("preset")
    if preset_name:
        preset = resolve_render_preset(
            str(preset_name),
            source_fps=document.project.fps,
            requested_fps=parameters.get("fps"),
        )
        for key in ("width", "height", "fps", "bitrate", "audio_bitrate", "audio_sample_rate", "color_space"):
            parameters.setdefault(key, preset.get(key))
    backend = FFmpegBackend(state.config.tools.ffmpeg)
    result = backend.render(
        document,
        manager.project_dir,
        destination,
        width=parameters.get("width"),
        height=parameters.get("height"),
        fps=parameters.get("fps"),
        codec=str(parameters.get("codec", "h264")),
        audio_codec=str(parameters.get("audio_codec", "aac")),
        audio_bitrate=str(parameters.get("audio_bitrate", "320k")),
        bitrate=parameters.get("bitrate"),
        hardware=str(parameters.get("hardware", "auto")),
        color_space=parameters.get("color_space"),
        audio_sample_rate=parameters.get("audio_sample_rate"),
        overwrite=True,
        burn_subtitle=parameters.get("burn_subtitle"),
        loudness_target=parameters.get("loudness"),
        true_peak=float(parameters.get("true_peak", -1.0)),
        loudness_range=float(parameters.get("lra", 11.0)),
    )
    return result.as_dict()


def _qc_recipe(ctx: typer.Context, target: Path, request: dict[str, Any]) -> dict[str, Any]:
    state = _state(ctx)
    scope, sources = resolve_qc_scope(target)
    engine = QCEngine(
        ffmpeg=state.config.tools.ffmpeg,
        ffprobe=state.config.tools.ffprobe,
        black_minimum_duration=float(request.get("black_duration", 0.5)),
        black_pixel_threshold=float(request.get("black_threshold", 0.1)),
        silence_threshold_db=float(request.get("silence_threshold", -50.0)),
        silence_minimum_duration=float(request.get("silence_duration", 0.5)),
        target_lufs=float(request.get("target_lufs", -14.0)),
        loudness_tolerance_lu=float(request.get("loudness_tolerance", 2.0)),
        maximum_true_peak_dbfs=float(request.get("true_peak", -1.0)),
        timeout=request.get("timeout"),
    )
    return engine.run(scope, sources, overwrite=True).model_dump(mode="json")


@recipe_app.command("build")
def recipe_build(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument()],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Apply edits atomically, render to a temporary file, then publish the output."""

    manager = None
    committed = False
    temporary: Path | None = None
    try:
        state = _state(ctx)
        manager = manager_for(state)
        recipe = load_recipe(source)
        engine = RecipeEngine(manager)
        plan = engine.plan(recipe, recipe_path=source, output=output)
        render_request = plan.render_request
        final_output = None
        if render_request and render_request.get("output"):
            final_output = Path(str(render_request["output"])).expanduser().resolve()
            if final_output.exists() and not overwrite:
                raise FileExistsError(f'Output "{final_output}" already exists; use --overwrite.')
        result = engine.build(recipe, recipe_path=source, output=output, dry_run=dry_run)
        committed = not dry_run
        if dry_run or render_request is None:
            _emit(
                ctx,
                "recipe.build",
                result,
                warnings=result.get("warnings"),
                revision=result["edit"]["project_revision"],
            )
            return
        if final_output is None:
            result["warnings"].append("Recipe edits were applied, but no render output was supplied.")
            _emit(
                ctx,
                "recipe.build",
                result,
                warnings=result["warnings"],
                revision=result["edit"]["project_revision"],
            )
            return
        final_output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=final_output.parent, prefix=".facut-recipe-", suffix=final_output.suffix, delete=False
        ) as stream:
            temporary = Path(stream.name)
        temporary.unlink(missing_ok=True)
        result["render"] = _render_recipe(ctx, render_request, temporary)
        result["render_executed"] = True
        if plan.qc_request is not None:
            result["qc"] = _qc_recipe(ctx, temporary, plan.qc_request)
            result["qc"]["scope"]["path"] = str(final_output)
            result["qc_executed"] = True
        os.replace(temporary, final_output)
        result["render"]["output"] = str(final_output)
        result["output"] = str(final_output)
        result["warnings"] = [
            item
            for item in result.get("warnings", [])
            if "must be executed by the CLI backend" not in item
        ]
        _emit(
            ctx,
            "recipe.build",
            result,
            warnings=result.get("warnings"),
            revision=result["edit"]["project_revision"],
        )
    except Exception as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if committed and manager is not None:
            try:
                manager.undo()
                manager.history.clear_redo()
            except Exception:
                pass
        _fail(ctx, "recipe.build", error)


@recipe_app.command("schema")
def recipe_schema(ctx: typer.Context) -> None:
    """Return the machine-readable recipe document schema."""

    _emit(ctx, "recipe.schema", recipe_json_schema())


__all__ = ["recipe_app"]
