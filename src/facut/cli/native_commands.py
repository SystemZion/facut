"""CLI diagnostics and benchmarks for the optional native accelerator."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.native import NativeClient, collect_batch_inputs, compact_batch_result, discover_native
from facut.responses import success_response


native_app = typer.Typer(help="Inspect and benchmark the optional C++ Native Accelerator.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: dict) -> None:
    from facut.cli.main import emit

    emit(_state(ctx), success_response(command, data), human=str(data))


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


@native_app.command("doctor")
@native_app.command("status", hidden=True)
def native_doctor(ctx: typer.Context) -> None:
    """Verify executable discovery, protocol version, FFmpeg ABI and features."""

    try:
        path = discover_native()
        data = NativeClient(path).doctor()
        data["executable"] = str(path.resolve()) if path else None
        _emit(ctx, "native.doctor", data)
    except Exception as error:
        _fail(ctx, "native.doctor", error)


@native_app.command("benchmark")
def native_benchmark(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    mode: Annotated[str, typer.Option("--mode")] = "fast",
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    jobs: Annotated[int, typer.Option("--jobs", min=1, max=8)] = 3,
) -> None:
    """Scan a bounded folder and report native throughput."""

    try:
        root = folder.expanduser().resolve()
        cache = output_directory or root / ".facut-native-benchmark"
        inputs, collection = collect_batch_inputs(
            root, output_directory=cache, limit=limit
        )
        if not inputs:
            raise ValueError("No supported media files were found for the benchmark.")
        result = NativeClient().batch_scan(
            inputs,
            output_directory=cache,
            mode=mode,
            jobs=jobs,
        )
        data = compact_batch_result(result)
        elapsed = float(data.get("elapsed_seconds") or 0.0)
        data["assets_per_second"] = len(inputs) / elapsed if elapsed > 0 else None
        data["collection"] = collection
        data["jobs"] = jobs
        _emit(ctx, "native.benchmark", data)
    except Exception as error:
        _fail(ctx, "native.benchmark", error)
