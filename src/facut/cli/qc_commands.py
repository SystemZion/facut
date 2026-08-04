"""Automated QC command implementation; registered by the root CLI module."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.qc.engine import QCEngine, resolve_qc_scope
from facut.qc.report import write_json, write_markdown
from facut.responses import success_response


def qc_command(
    ctx: typer.Context,
    target: Annotated[
        Path | None,
        typer.Argument(help="Media file or facut project; defaults to --project/current project."),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Write the complete structured JSON QC report."),
    ] = None,
    markdown: Annotated[
        Path | None,
        typer.Option("--markdown", help="Write a human-readable Markdown QC report."),
    ] = None,
    contact_sheet: Annotated[
        Path | None,
        typer.Option(
            "--contact-sheet",
            help="Write one contact sheet, or a directory of sheets for project QC.",
        ),
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    black_duration: Annotated[
        float, typer.Option("--black-duration", min=0.0)
    ] = 0.5,
    black_threshold: Annotated[
        float, typer.Option("--black-threshold", min=0.0, max=1.0)
    ] = 0.10,
    silence_threshold: Annotated[
        float, typer.Option("--silence-threshold", help="Silence threshold in dBFS.")
    ] = -50.0,
    silence_duration: Annotated[
        float, typer.Option("--silence-duration", min=0.0)
    ] = 0.5,
    target_lufs: Annotated[float, typer.Option("--target-lufs")] = -14.0,
    loudness_tolerance: Annotated[
        float, typer.Option("--loudness-tolerance", min=0.0)
    ] = 2.0,
    true_peak: Annotated[float, typer.Option("--true-peak")] = -1.0,
    freeze_duration: Annotated[float, typer.Option("--freeze-duration", min=0.1)] = 2.0,
    expected_duration: Annotated[
        float | None,
        typer.Option("--expected-duration", help="Expected final timeline duration in seconds."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", min=0.1, help="Per-check timeout in seconds."),
    ] = None,
) -> None:
    """Run metadata, full-decode, black, silence and EBU R128 checks."""

    from facut.cli.main import CliState, emit, fail

    state: CliState = ctx.ensure_object(CliState)
    try:
        scope, sources = resolve_qc_scope(target, project=state.project)
        if expected_duration is None and target is not None and state.project is not None:
            from facut.core.project_manager import ProjectManager

            expected_duration = ProjectManager(state.project).load().project.duration
        engine = QCEngine(
            ffmpeg=state.config.tools.ffmpeg,
            ffprobe=state.config.tools.ffprobe,
            black_minimum_duration=black_duration,
            black_pixel_threshold=black_threshold,
            silence_threshold_db=silence_threshold,
            silence_minimum_duration=silence_duration,
            target_lufs=target_lufs,
            loudness_tolerance_lu=loudness_tolerance,
            maximum_true_peak_dbfs=true_peak,
            freeze_minimum_duration=freeze_duration,
            expected_duration=expected_duration,
            timeout=timeout,
        )
        qc_report = engine.run(
            scope,
            sources,
            contact_sheet=contact_sheet,
            overwrite=overwrite,
        )
        json_path: str | None = None
        markdown_path: str | None = None
        if report is not None:
            # Keep 0.6 compatibility for explicit .md report paths.
            if report.suffix.casefold() in {".md", ".markdown"} and markdown is None:
                markdown_path = str(write_markdown(qc_report, report, overwrite=overwrite).resolve())
            else:
                json_path = str(write_json(qc_report, report, overwrite=overwrite).resolve())
        if markdown is not None:
            markdown_path = str(write_markdown(qc_report, markdown, overwrite=overwrite).resolve())
        data = qc_report.model_dump(mode="json")
        data["json_report"] = json_path
        data["markdown_report"] = markdown_path
        emit(
            state,
            success_response("qc", data, warnings=qc_report.warnings),
            human=(
                f"[bold]QC {qc_report.status.value.upper()}[/bold] — "
                f"{qc_report.summary['total']} file(s), "
                f"{qc_report.summary['failed']} failed, "
                f"{qc_report.summary['warnings']} warning(s)"
            ),
        )
    except Exception as error:
        fail(state, "qc", public_error(error))
