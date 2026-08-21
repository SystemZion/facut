"""CLI controls for background warm-service memory."""

from __future__ import annotations

from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.responses import success_response
from facut.runtime_control import (
    SUPPORTED_SERVICES,
    autoload_status,
    clean_services,
    configure_autoload,
    service_status,
    warm_services,
)


autoload_app = typer.Typer(help="Configure default background service warmup.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: dict) -> None:
    from facut.cli.main import emit

    emit(_state(ctx), success_response(command, data), human=str(data))


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _service_option() -> typer.Option:
    return typer.Option(
        "--service",
        help=f"Service to control; repeat it, or use all. Available: {', '.join(SUPPORTED_SERVICES)}.",
    )


def cleanram_command(
    ctx: typer.Context,
    services: Annotated[list[str], _service_option()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Release RAM by stopping only explicitly selected warm services."""

    try:
        _emit(ctx, "runtime.cleanram", clean_services(services, dry_run=dry_run))
    except Exception as error:
        _fail(ctx, "runtime.cleanram", error)


def warmup_command(
    ctx: typer.Context,
    services: Annotated[list[str], _service_option()],
) -> None:
    """Load selected heavy services now instead of waiting for first use."""

    try:
        _emit(ctx, "runtime.warmup", warm_services(services))
    except Exception as error:
        _fail(ctx, "runtime.warmup", error)


@autoload_app.command("status")
def autoload_status_command(ctx: typer.Context) -> None:
    """Show default background warmup policy and live service state."""

    try:
        _emit(ctx, "runtime.autoload.status", autoload_status())
    except Exception as error:
        _fail(ctx, "runtime.autoload.status", error)


@autoload_app.command("enable")
def autoload_enable(
    ctx: typer.Context,
    service: Annotated[str, typer.Argument(help="Service name or all.")],
) -> None:
    """Enable default background warmup for a service."""

    try:
        _emit(ctx, "runtime.autoload.enable", configure_autoload(service, enabled=True))
    except Exception as error:
        _fail(ctx, "runtime.autoload.enable", error)


@autoload_app.command("disable")
def autoload_disable(
    ctx: typer.Context,
    service: Annotated[str, typer.Argument(help="Service name or all.")],
    stop_now: Annotated[bool, typer.Option("--stop-now")] = False,
) -> None:
    """Disable future warmup; optionally stop that service immediately."""

    try:
        data = configure_autoload(service, enabled=False)
        if stop_now:
            data["stop"] = clean_services([service])
        else:
            data["live_services"] = service_status([service])
        _emit(ctx, "runtime.autoload.disable", data)
    except Exception as error:
        _fail(ctx, "runtime.autoload.disable", error)
