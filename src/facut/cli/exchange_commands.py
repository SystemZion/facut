"""Editorial interchange CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.core.sequences import materialize_sequence
from facut.responses import success_response


exchange_app = typer.Typer(help="Exchange timelines with OTIO and FCPXML workflows.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


@exchange_app.command("export")
def exchange_export(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    exchange_format: Annotated[
        str | None, typer.Option("--format", help="otio or fcpxml; inferred from extension.")
    ] = None,
    sequence: Annotated[str | None, typer.Option("--sequence")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Export the working or named sequence with an explicit loss report."""

    from facut.cli.main import emit, fail

    state = _state(ctx)
    command = "exchange.export"
    try:
        from facut.exchange import export_fcpxml, export_otio

        manager = manager_for(state)
        document = manager.require_document()
        if sequence:
            document = materialize_sequence(document, sequence)
        selected = (exchange_format or output.suffix.lstrip(".")).casefold()
        if selected == "otio":
            result = export_otio(document, manager.project_dir, output, overwrite=overwrite)
        elif selected in {"fcpxml", "xml"}:
            result = export_fcpxml(document, manager.project_dir, output, overwrite=overwrite)
        else:
            raise ValueError("Exchange format must be otio or fcpxml.")
        emit(
            state,
            success_response(
                command,
                result.as_dict(),
                warnings=list(result.warnings),
                project_revision=document.revision,
            ),
            human=json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        )
    except Exception as error:
        fail(state, command, public_error(error))


@exchange_app.command("import")
def exchange_import(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument()],
    exchange_format: Annotated[
        str | None, typer.Option("--format", help="otio or fcpxml; inferred from extension.")
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Replace the working timeline after validation.")
    ] = False,
) -> None:
    """Plan an exchange import; make changes only with --apply."""

    from facut.cli.main import emit, fail

    state = _state(ctx)
    command = "exchange.import"
    try:
        from facut.exchange import apply_import_plan, plan_fcpxml_import, plan_otio_import

        selected = (exchange_format or source.suffix.lstrip(".")).casefold()
        if selected == "otio":
            plan = plan_otio_import(source)
        elif selected in {"fcpxml", "xml"}:
            plan = plan_fcpxml_import(source)
        else:
            raise ValueError("Exchange format must be otio or fcpxml.")
        manager = manager_for(state)
        revision = manager.require_document().revision
        if apply:
            document = apply_import_plan(manager, plan)
            revision = document.revision
        data = {"plan": plan.as_dict(), "applied": apply}
        emit(
            state,
            success_response(
                command,
                data,
                warnings=plan.warnings,
                project_revision=revision,
            ),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        fail(state, command, public_error(error))
