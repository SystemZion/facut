"""Top-level Typer application."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from facut import __version__
from facut.cli.doctor import collect_diagnostics, run_sample_render
from facut.config import AppConfig, load_config
from facut.exceptions import ExitCode, FacutError
from facut.logging_config import configure_logging
from facut.responses import Response, error_response, success_response

app = typer.Typer(
    name="facut",
    help="Fast AI Cut - deterministic command-line video editing for agents and humans.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@dataclass(slots=True)
class CliState:
    json_output: bool
    quiet: bool
    verbose: bool
    project: Path | None
    config: AppConfig
    logger: logging.Logger


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"facut {__version__}")
        raise typer.Exit(ExitCode.SUCCESS)


@app.callback()
def root(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit only a machine-readable JSON response."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress non-essential human output."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable detailed diagnostics and logs."),
    ] = False,
    project: Annotated[
        Path | None,
        typer.Option("--project", "-p", help="Project file or project directory."),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = None,
) -> None:
    """Set process-wide output and project options."""

    del version
    try:
        config = load_config()
        if config.voice.cpu_overlay is not None:
            os.environ.setdefault(
                "FACUT_CPU_TORCH_OVERLAY",
                str(config.voice.cpu_overlay.expanduser().resolve()),
            )
    except FacutError as exc:
        response = error_response("startup", exc)
        if json_output:
            typer.echo(response.as_json())
        else:
            typer.echo(f"Error: {exc.message}", err=True)
            if exc.suggestion:
                typer.echo(f"Suggestion: {exc.suggestion}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    logger = configure_logging(verbose=verbose, quiet=quiet)
    ctx.obj = CliState(json_output, quiet, verbose, project, config, logger)


def emit(
    state: CliState,
    response: Response[object],
    *,
    human: str | None = None,
) -> None:
    """Respect global output mode for all command implementations."""

    if state.json_output:
        typer.echo(response.as_json())
    elif not state.quiet and human:
        Console().print(human)


def fail(state: CliState, command: str, error: FacutError) -> None:
    """Emit a domain failure and terminate with its stable exit code."""

    state.logger.error("%s: %s", command, error.message)
    response = error_response(command, error)
    if state.json_output:
        typer.echo(response.as_json())
    else:
        typer.echo(f"Error: {error.message}", err=True)
        if error.suggestion:
            typer.echo(f"Suggestion: {error.suggestion}", err=True)
        if stderr := error.details.get("stderr"):
            lines = str(stderr).splitlines()[-20:]
            typer.echo("FFmpeg details (last 20 lines):", err=True)
            typer.echo("\n".join(lines), err=True)
        if log_path := error.details.get("log_path"):
            typer.echo(f"Render log: {log_path}", err=True)
    raise typer.Exit(error.exit_code)


@app.command("help")
def help_command(ctx: typer.Context) -> None:
    """Show the root command help."""

    if ctx.parent is not None:
        typer.echo(ctx.parent.get_help())


@app.command("doctor")
def doctor(
    ctx: typer.Context,
    json_output: Annotated[
        bool | None,
        typer.Option("--json", help="Emit only a machine-readable JSON response."),
    ] = None,
    sample_render: Annotated[
        bool,
        typer.Option("--sample-render", help="Run a one-second generated render smoke test."),
    ] = False,
) -> None:
    """Check FFmpeg, codecs, hardware backends, directories, fonts, and platform."""

    state: CliState = ctx.ensure_object(CliState)
    if json_output is not None:
        state.json_output = json_output
    data, warnings = collect_diagnostics(state.config)
    if sample_render:
        data["sample_render"] = run_sample_render(state.config)
        if data["sample_render"]["status"] == "failed":
            warnings.append("The FFmpeg sample render failed.")
            data["healthy"] = False
    response = success_response("doctor", data, warnings=warnings)
    if state.json_output:
        typer.echo(response.as_json())
        return
    if state.quiet:
        return

    table = Table(title=f"facut doctor {__version__}", show_header=True)
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("Platform", f'{data["platform"]["system"]} {data["platform"]["release"]}')
    table.add_row("Python", data["python"]["version"])
    table.add_row("FFmpeg", data["ffmpeg"]["version"] or "[red]not found[/red]")
    table.add_row("FFprobe", data["ffprobe"]["version"] or "[red]not found[/red]")
    hardware = data["encoders"]["hardware"]
    usable_hardware = [name for name, diagnostic in hardware.items() if diagnostic["usable"]]
    detected_only = [
        name
        for name, diagnostic in hardware.items()
        if diagnostic["detected"] and not diagnostic["usable"]
    ]
    hardware_summary = ", ".join(usable_hardware) if usable_hardware else "none usable"
    if detected_only:
        hardware_summary += f" (detected but unusable: {', '.join(detected_only)})"
    table.add_row("Hardware encoders", hardware_summary)
    table.add_row("Cache writable", "yes" if data["directories"]["cache_writable"] else "[red]no[/red]")
    table.add_row("Temporary writable", "yes" if data["directories"]["temporary_writable"] else "[red]no[/red]")
    table.add_row("Fonts discoverable", "yes" if data["fonts"]["available"] else "[yellow]no[/yellow]")
    table.add_row("Sample render", data["sample_render"]["status"])
    table.add_row("Overall", "[green]healthy[/green]" if data["healthy"] else "[yellow]attention needed[/yellow]")
    console = Console()
    console.print(table)
    for warning in warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


def main() -> None:
    """Installed console-script entry point."""

    if len(sys.argv) > 1 and sys.argv[1] == "__voice_service_daemon__":
        from facut.voice.service import _daemon_entry

        _daemon_entry(sys.argv[2:])
        return
    app()


# The console-script imports this module before calling ``main``.  Avoid loading
# every editing subsystem for the eager version query; this keeps automation
# health checks fast without changing normal Typer registration or tests.
if len(sys.argv) == 2 and sys.argv[1] == "--version":
    typer.echo(f"facut {__version__}")
    raise SystemExit(ExitCode.SUCCESS)


from facut.cli.project_commands import (  # noqa: E402
    branch_app,
    history_app,
    import_command,
    init_command,
    inspect_command,
    project_app,
    redo_command,
    run_command,
    undo_command,
)
from facut.cli.timeline_commands import (  # noqa: E402
    clip_app,
    timeline_app,
    transition_app,
)
from facut.cli.render_commands import preview_app, render_command  # noqa: E402
from facut.cli.audio_commands import audio_app  # noqa: E402
from facut.cli.subtitle_commands import subtitle_app, text_app  # noqa: E402
from facut.cli.proxy_commands import proxy_app  # noqa: E402
from facut.cli.marker_commands import marker_app  # noqa: E402
from facut.cli.qc_commands import qc_command  # noqa: E402
from facut.cli.analyze_commands import analyze_app  # noqa: E402
from facut.cli.sequence_commands import sequence_app  # noqa: E402
from facut.cli.serve_commands import serve_command  # noqa: E402
from facut.cli.effect_commands import effect_app  # noqa: E402
from facut.cli.ingest_commands import ingest_command  # noqa: E402
from facut.cli.delivery_commands import (  # noqa: E402
    deliver_command,
    delivery_presets_command,
)
from facut.cli.exchange_commands import exchange_app  # noqa: E402
from facut.cli.schema_commands import schema_app  # noqa: E402
from facut.cli.intelligence_commands import (  # noqa: E402
    broll_app,
    narration_app,
    semantic_app,
    story_app,
)
from facut.cli.travel_commands import map_app, reframe_app  # noqa: E402
from facut.cli.voice_commands import voice_app  # noqa: E402
from facut.cli.recipe_commands import recipe_app  # noqa: E402
from facut.cli.vlog_commands import vlog_app  # noqa: E402
from facut.cli.native_commands import native_app  # noqa: E402
from facut.cli.font_commands import font_app  # noqa: E402
from facut.cli.typography_commands import typography_app  # noqa: E402
from facut.cli.library_commands import library_app, style_app  # noqa: E402
from facut.cli.download_commands import download_command  # noqa: E402
from facut.cli.install_commands import (  # noqa: E402
    install_command,
    models_app,
    update_command,
)

app.command("init")(init_command)
app.command("import")(import_command)
app.command("ingest")(ingest_command)
app.command("inspect")(inspect_command)
app.command("undo")(undo_command)
app.command("redo")(redo_command)
app.command("run")(run_command)
app.add_typer(project_app, name="project")
app.add_typer(history_app, name="history")
app.add_typer(branch_app, name="branch")
app.add_typer(timeline_app, name="timeline")
app.add_typer(clip_app, name="clip")
app.add_typer(transition_app, name="transition")
app.add_typer(preview_app, name="preview")
app.add_typer(audio_app, name="audio")
app.add_typer(subtitle_app, name="subtitle")
app.add_typer(text_app, name="text")
app.add_typer(proxy_app, name="proxy")
app.add_typer(marker_app, name="marker")
app.command("render")(render_command)
app.command("qc")(qc_command)
app.add_typer(analyze_app, name="analyze")
app.add_typer(sequence_app, name="sequence")
app.command("serve")(serve_command)
app.command("deliver")(deliver_command)
app.command("delivery-presets")(delivery_presets_command)
app.add_typer(exchange_app, name="exchange")
app.add_typer(schema_app, name="schema")
app.add_typer(semantic_app, name="semantic")
app.add_typer(story_app, name="story")
app.add_typer(broll_app, name="broll")
app.add_typer(narration_app, name="narration")
app.add_typer(map_app, name="map")
app.add_typer(reframe_app, name="reframe")
app.add_typer(voice_app, name="voice")
app.add_typer(recipe_app, name="recipe")
app.add_typer(effect_app, name="effect")
app.add_typer(vlog_app, name="vlog")
app.add_typer(native_app, name="native")
app.add_typer(font_app, name="font")
app.add_typer(typography_app, name="typography")
app.add_typer(library_app, name="library")
app.add_typer(style_app, name="style")
app.command("download")(download_command)
app.command("install")(install_command)
app.command("update")(update_command)
app.add_typer(models_app, name="models")


if __name__ == "__main__":
    main()
