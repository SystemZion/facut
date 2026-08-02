"""CLI for separately downloading optional AI model packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import public_error
from facut.downloads import ModelDownloader, get_model_package
from facut.responses import success_response


def download_command(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="voice_model or srt_model")],
    directory: Annotated[
        Path | None,
        typer.Option("--directory", "-d", help="Model storage root."),
    ] = None,
    source: Annotated[
        str,
        typer.Option("--source", help="auto, modelscope, huggingface, or hf-mirror"),
    ] = "auto",
    jsonl: Annotated[
        bool,
        typer.Option("--jsonl", help="Emit machine-readable progress events as JSON Lines."),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Ignore the fast receipt and re-run full SHA-256 verification."),
    ] = False,
) -> None:
    """Download an optional model with mirror speed testing and safe resume."""

    from facut.cli.main import CliState, emit, fail

    state: CliState = ctx.ensure_object(CliState)

    def progress(event: dict[str, Any]) -> None:
        if jsonl:
            typer.echo(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            return
        if state.json_output or state.quiet:
            return
        kind = event.get("event")
        if kind == "source_benchmark":
            status = f'{event.get("mbps", 0):.2f} MB/s' if event.get("available") else event.get("error")
            typer.echo(f'测速 {event.get("source")}: {status}')
        elif kind == "file_complete":
            typer.echo(f'完成 {event.get("file")} [{event.get("source")}]')
        elif kind == "file_cached":
            typer.echo(f'已校验 {event.get("file")}')
        elif kind == "source_failed":
            typer.echo(f'源切换 {event.get("source")}: {event.get("error")}', err=True)

    try:
        package = get_model_package(model)
        root = directory if directory is not None else state.config.models.directory
        result = ModelDownloader(package, root, progress=progress).download(
            source_name=source, force_verify=verify
        )
        response = success_response("download", result)
        if jsonl:
            typer.echo(
                json.dumps(
                    {"event": "result", **response.model_dump(mode="json")},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        else:
            emit(state, response, human=f'模型已就绪：{result["directory"]}')
    except Exception as error:
        fail(state, "download", public_error(error))
