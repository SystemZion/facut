"""Short, deterministic entry points for common FACUT workflows."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.config import WorkflowDefaultsConfig, save_config
from facut.core.timeline_engine import parse_time
from facut.exceptions import ReviewRequiredError
from facut.responses import success_response
from facut.vlog.director import director_status


defaults_app = typer.Typer(help="Set, inspect, or reset shortcut workflow defaults.")
check_app = typer.Typer(help="Check the environment, a project, or a rendered video.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: Any, *, human: str | None = None) -> None:
    from facut.cli.main import emit

    emit(_state(ctx), success_response(command, data), human=human)


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _alias(ctx: typer.Context, requested: str, canonical: str) -> None:
    state = _state(ctx)
    state.requested_command = requested
    state.canonical_action = canonical


def _project_defaults(state: Any) -> dict[str, Any]:
    try:
        document = manager_for(state).require_document()
    except Exception:
        return {}
    value = document.settings.get("workflow_defaults", {})
    return dict(value) if isinstance(value, dict) else {}


def _effective_defaults(state: Any) -> dict[str, Any]:
    result = state.config.workflow.model_dump(mode="json")
    result.update({key: value for key, value in _project_defaults(state).items() if value is not None})
    return result


def _normalize_style(value: str) -> str:
    aliases = {
        "natural": "natural-vlog",
        "comedy": "comedy-vlog",
        "cinematic": "cinematic-travel",
        "humanities": "humanities-documentary",
        "family": "family-trip",
        "food": "food-walk",
        "daily": "relaxed-daily",
    }
    return aliases.get(value.casefold(), value)


def _duration_seconds(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    normalized = value.strip().casefold()
    if normalized.endswith("m") and normalized[:-1].strip():
        return float(normalized[:-1]) * 60.0
    return float(parse_time(value).seconds)


def _run_state_path(project_dir: Path) -> Path:
    return project_dir / "cache" / "vlog" / "shortcut-run.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def scan_command(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
    deep: Annotated[bool, typer.Option("--deep/--fast")] = False,
    batch_size: Annotated[int, typer.Option("--batch-size", "-b", min=1, max=100)] = 12,
    proxy: Annotated[str, typer.Option("--proxy")] = "auto",
) -> None:
    """Prepare and index a travel-media folder without inventing visual observations."""

    _alias(ctx, "scan", "vlog.prepare")
    from facut.cli.vlog_commands import vlog_prepare

    vlog_prepare(
        ctx,
        source=source,
        project=project,
        trip=source.name,
        proxy=proxy,
        batch_size=batch_size,
        frames=True,
        engine="auto",
        native_mode="deep" if deep else "fast",
    )


def cut_command(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")],
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
    style: Annotated[str | None, typer.Option("--style", "-s")] = None,
    length: Annotated[str | None, typer.Option("--length", "-l")] = None,
    preset: Annotated[str | None, typer.Option("--preset")] = None,
    draft: Annotated[bool, typer.Option("--draft")] = False,
    final: Annotated[bool, typer.Option("--final")] = False,
    four_k: Annotated[bool, typer.Option("--4k")] = False,
    hardware: Annotated[str | None, typer.Option("--hardware")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Run the quality-first VLOG workflow with compact, memorable options."""

    command = "cut"
    _alias(ctx, command, "vlog.run")
    try:
        if draft and final:
            raise ValueError("Use either --draft or --final, not both.")
        if four_k and (draft or not final):
            raise ValueError("--4k requires --final; draft review output is always 1080p.")
        state = _state(ctx)
        effective = _effective_defaults(state)
        selected_style = _normalize_style(style or str(effective["style"]))
        target_duration = _duration_seconds(length, float(effective["target_duration"]))
        selected_preset = preset or (
            str(effective["preset"]) if final else "youtube-1080p"
        )
        if four_k and preset is None:
            selected_preset = "youtube-4k"
        selected_hardware = hardware or str(effective["hardware"])
        project_dir = (
            project.expanduser().resolve()
            if project is not None
            else output.expanduser().resolve().parent / f"{source.name}-facut-project"
        )
        run_path = _run_state_path(project_dir)
        previous = (
            json.loads(run_path.read_text(encoding="utf-8"))
            if run_path.is_file()
            else {}
        )
        if final and not previous.get("draft_completed"):
            raise ReviewRequiredError(
                "Final 4K delivery requires a completed 1080p shortcut review first.",
                suggestion="Run the same `facut cut` command with --draft, review it, then retry with --final --4k.",
            )
        invocation = {
            "version": "1.0",
            "source": str(source.expanduser().resolve()),
            "output": str(output.expanduser().resolve()),
            "project": str(project_dir),
            "style": selected_style,
            "target_duration": target_duration,
            "preset": selected_preset,
            "mode": "final" if final else "draft",
            "hardware": selected_hardware,
            "overwrite": overwrite,
            "draft_completed": bool(previous.get("draft_completed", False)),
            "draft_output": previous.get("draft_output"),
        }
        invocation["job_id"] = "vlogjob_" + hashlib.sha256(
            json.dumps(
                {
                    "source": invocation["source"],
                    "project": invocation["project"],
                    "output": invocation["output"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        _write_json_atomic(run_path, invocation)
    except Exception as error:
        _fail(ctx, command, error)
        return
    _alias(ctx, _state(ctx).requested_command or command, "vlog.run")
    from facut.cli.vlog_commands import vlog_run

    vlog_run(
        ctx,
        source=source,
        output=output,
        project=project,
        style=selected_style,
        target_duration=target_duration,
        preset=selected_preset,
        subtitle="auto",
        typography="auto",
        preserve_original_audio=True,
        automatic=True,
        hardware=selected_hardware,
        overwrite=overwrite,
    )
    if not final:
        invocation["draft_completed"] = True
        invocation["draft_output"] = str(output.expanduser().resolve())
        _write_json_atomic(run_path, invocation)


def resume_command(
    ctx: typer.Context,
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
) -> None:
    """Resume the last shortcut VLOG run without repeating valid completed work."""

    command = "resume"
    try:
        state = _state(ctx)
        if project is not None:
            state.project = project
        manager = manager_for(state)
        path = _run_state_path(manager.project_dir)
        if not path.is_file():
            raise FileNotFoundError(
                f'No resumable shortcut run exists in "{manager.project_dir}". Run `facut cut` first.'
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        _fail(ctx, command, error)
        return
    _alias(ctx, command, "vlog.run")
    cut_command(
        ctx,
        source=Path(payload["source"]),
        output=Path(payload["output"]),
        project=Path(payload["project"]),
        style=str(payload["style"]),
        length=f'{float(payload["target_duration"])}s',
        preset=str(payload["preset"]),
        draft=payload.get("mode") != "final",
        final=payload.get("mode") == "final",
        four_k=False,
        hardware=str(payload.get("hardware", "auto")),
        overwrite=bool(payload.get("overwrite", False)),
    )


def next_command(
    ctx: typer.Context,
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
) -> None:
    """Show the exact next quality-first step without editing the project."""

    command = "next"
    try:
        state = _state(ctx)
        if project is not None:
            state.project = project
        manager = manager_for(state)
        status = director_status(manager.project_dir, manager.require_document())
        run_path = _run_state_path(manager.project_dir)
        if run_path.is_file():
            run_state = json.loads(run_path.read_text(encoding="utf-8"))
            status["job_id"] = run_state.get("job_id")
        status["canonical_next_command"] = status.get("next_command")
        stage = status.get("stage")
        status["shortcut_next_command"] = {
            "not_prepared": "facut scan <source> -p <project>",
            "atlas_inspection": "facut vlog inspect batch -p <project>",
            "inspection": "facut vlog inspect next -p <project>",
            "ready_to_plan": "facut vlog plan -p <project>",
            "story_review": "facut vlog compare -p <project>",
            "ready_to_build": "facut resume -p <project>",
        }.get(str(stage), status.get("next_command"))
        _alias(ctx, command, "vlog.status")
        _emit(ctx, "vlog.status", status, human=str(status["shortcut_next_command"]))
    except Exception as error:
        _fail(ctx, command, error)


def say_command(
    ctx: typer.Context,
    text: Annotated[str, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    voice: Annotated[str | None, typer.Option("--voice", "-v")] = None,
    style: Annotated[str, typer.Option("--style", "-s")] = "auto",
    takes: Annotated[int, typer.Option("--takes", "-n", min=1, max=10)] = 1,
    speed: Annotated[float, typer.Option("--speed", min=0.5, max=2.0)] = 1.0,
    intensity: Annotated[float, typer.Option("--intensity", min=0.0, max=1.0)] = 0.5,
    instruction: Annotated[str | None, typer.Option("--instruction")] = None,
    verify: Annotated[bool, typer.Option("--verify")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Synthesize an audition using a short alias for ``voice say``."""

    state = _state(ctx)
    selected_voice = voice or _effective_defaults(state).get("voice")
    _alias(ctx, "say", "voice.say")
    from facut.cli.voice_commands import voice_say

    voice_say(
        ctx,
        text=text,
        output=output,
        voice=selected_voice,
        style=style,
        takes=takes,
        speed=speed,
        intensity=intensity,
        instruction=instruction,
        purpose=None,
        device="auto",
        require_cuda=False,
        use_service=True,
        provider=None,
        overwrite=overwrite,
        verify=verify,
        verify_entity=None,
        verify_min_similarity=0.70,
    )


@check_app.command("env")
def check_environment(ctx: typer.Context) -> None:
    """Run environment diagnostics."""

    _alias(ctx, "check", "doctor")
    from facut.cli.doctor import collect_diagnostics

    state = _state(ctx)
    data, warnings = collect_diagnostics(state.config)
    from facut.cli.main import emit

    emit(state, success_response("doctor", data, warnings=warnings), human="Environment check complete.")


@check_app.command("project")
def check_project(
    ctx: typer.Context,
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
) -> None:
    """Validate one FACUT project."""

    state = _state(ctx)
    if project is not None:
        state.project = project
    _alias(ctx, "check", "project.validate")
    from facut.cli.project_commands import project_validate

    project_validate(ctx)


@check_app.command("video")
def check_video(ctx: typer.Context, target: Annotated[Path, typer.Argument(exists=True)]) -> None:
    """Run full media QC on a rendered video."""

    _alias(ctx, "check", "qc")
    from facut.cli.qc_commands import qc_command

    qc_command(ctx, target=target)


@defaults_app.command("show")
def defaults_show(ctx: typer.Context) -> None:
    """Show user, project and effective shortcut defaults."""

    state = _state(ctx)
    _emit(
        ctx,
        "defaults.show",
        {
            "user": state.config.workflow.model_dump(mode="json"),
            "project": _project_defaults(state),
            "effective": _effective_defaults(state),
            "precedence": "explicit > project > user > built-in",
        },
    )


@defaults_app.command("set")
def defaults_set(
    ctx: typer.Context,
    style: Annotated[str | None, typer.Option("--style")] = None,
    preset: Annotated[str | None, typer.Option("--preset")] = None,
    voice: Annotated[str | None, typer.Option("--voice")] = None,
    hardware: Annotated[str | None, typer.Option("--hardware")] = None,
    length: Annotated[str | None, typer.Option("--length")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "user",
) -> None:
    """Persist shortcut defaults globally or in the current project."""

    command = "defaults.set"
    try:
        state = _state(ctx)
        if scope not in {"user", "project"}:
            raise ValueError("--scope must be user or project.")
        updates = {
            key: value
            for key, value in {
                "style": _normalize_style(style) if style else None,
                "preset": preset,
                "voice": voice,
                "hardware": hardware,
                "target_duration": _duration_seconds(length, 480.0) if length else None,
            }.items()
            if value is not None
        }
        if not updates:
            raise ValueError("Specify at least one workflow default to set.")
        if scope == "user":
            values = state.config.workflow.model_dump()
            values.update(updates)
            state.config.workflow = WorkflowDefaultsConfig.model_validate(values)
            path = save_config(state.config)
            data = {"scope": scope, "path": str(path), "defaults": values}
        else:
            manager = manager_for(state)

            def mutate(document):
                values = dict(document.settings.get("workflow_defaults", {}))
                values.update(updates)
                document.settings["workflow_defaults"] = values

            _, document = manager.mutate(
                "defaults.set", "Update project shortcut defaults", mutate
            )
            data = {
                "scope": scope,
                "defaults": document.settings["workflow_defaults"],
                "project_revision": document.revision,
            }
        _emit(ctx, command, data)
    except Exception as error:
        _fail(ctx, command, error)


@defaults_app.command("reset")
def defaults_reset(
    ctx: typer.Context,
    scope: Annotated[str, typer.Option("--scope")] = "user",
) -> None:
    """Reset user or project shortcut defaults."""

    command = "defaults.reset"
    try:
        state = _state(ctx)
        if scope == "user":
            state.config.workflow = WorkflowDefaultsConfig()
            path = save_config(state.config)
            data = {"scope": scope, "path": str(path)}
        elif scope == "project":
            manager = manager_for(state)

            def mutate(document):
                document.settings.pop("workflow_defaults", None)

            _, document = manager.mutate(
                "defaults.reset", "Reset project shortcut defaults", mutate
            )
            data = {"scope": scope, "project_revision": document.revision}
        else:
            raise ValueError("--scope must be user or project.")
        _emit(ctx, command, data)
    except Exception as error:
        _fail(ctx, command, error)


def explain_command(
    ctx: typer.Context,
    command: Annotated[str, typer.Argument()],
    arguments: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Explain a shortcut's canonical action and quality behavior without executing it."""

    aliases = {
        "scan": ("vlog.prepare", "Imports, links proxies and builds external-AI inspection tasks."),
        "cut": ("vlog.run", "Runs the complete quality-first director workflow; draft is 1080p."),
        "resume": ("vlog.run", "Restarts only missing or invalid workflow nodes."),
        "next": ("vlog.status", "Reads status and recommends the next command without editing."),
        "say": ("voice.say", "Creates audition files and never edits the timeline."),
        "check": ("doctor/project.validate/qc", "Selects a deterministic check by explicit subcommand."),
    }
    key = command.casefold()
    if key not in aliases:
        _fail(ctx, "explain", ValueError(f'Unknown shortcut "{command}".'))
        return
    canonical, behavior = aliases[key]
    _emit(
        ctx,
        "explain",
        {
            "requested_command": key,
            "canonical_action": canonical,
            "arguments": arguments or [],
            "behavior": behavior,
            "quality_policy": "Shortcut commands never bypass evidence review or final QC.",
            "executed": False,
            "effective_defaults": _effective_defaults(_state(ctx)),
        },
    )
