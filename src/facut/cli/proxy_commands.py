"""Project-native proxy management commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.media.proxy_manager import ProxyManager
from facut.responses import success_response


proxy_app = typer.Typer(help="Create, link, relink, and inspect media proxies.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _service(ctx: typer.Context) -> ProxyManager:
    state = _state(ctx)
    return ProxyManager(
        manager_for(state),
        ffmpeg=state.config.tools.ffmpeg,
        ffprobe=state.config.tools.ffprobe,
    )


def _emit(ctx: typer.Context, command: str, data: object, revision: int) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


@proxy_app.command("create")
def proxy_create(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    height: Annotated[int, typer.Option("--height")] = 540,
    codec: Annotated[str, typer.Option("--codec")] = "h264",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Create a validated project proxy and link it to its original."""

    try:
        asset, state, output = _service(ctx).create(
            media_id,
            height=height,
            codec=codec,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        _emit(
            ctx,
            "proxy.create",
            {
                "media_id": media_id,
                "output": str(output),
                "linked": asset is not None,
                "dry_run": dry_run,
            },
            state.revision,
        )
    except Exception as error:
        _abort(ctx, "proxy.create", error)


@proxy_app.command("link")
def proxy_link(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    path: Annotated[Path, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Validate and link an existing proxy file."""

    try:
        asset, state = _service(ctx).link(media_id, path, dry_run=dry_run)
        _emit(
            ctx,
            "proxy.link",
            {"media": asset.model_dump(mode="json"), "dry_run": dry_run},
            state.revision,
        )
    except Exception as error:
        _abort(ctx, "proxy.link", error)


@proxy_app.command("relink")
def proxy_relink(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    search: Annotated[Path | None, typer.Option("--search")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Find and link a same-name, LRF, or conventional proxy automatically."""

    try:
        asset, state, path = _service(ctx).relink(
            media_id, search, dry_run=dry_run
        )
        _emit(
            ctx,
            "proxy.relink",
            {
                "media": asset.model_dump(mode="json"),
                "matched": str(path),
                "dry_run": dry_run,
            },
            state.revision,
        )
    except Exception as error:
        _abort(ctx, "proxy.relink", error)


@proxy_app.command("scan")
def proxy_scan(
    ctx: typer.Context,
    media_id: Annotated[str | None, typer.Option("--media-id")] = None,
    search: Annotated[
        list[Path] | None,
        typer.Option("--search", help="Additional directory to scan recursively."),
    ] = None,
    link: Annotated[bool, typer.Option("--link")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Score LRF/proxy candidates and atomically link unambiguous matches."""

    try:
        service = _service(ctx)
        data, state = service.scan(
            media_id,
            search_directories=search,
            link=link,
            dry_run=dry_run,
        )
        _emit(
            ctx,
            "proxy.scan",
            {"results": data, "link": link, "dry_run": dry_run},
            state.revision,
        )
    except Exception as error:
        _abort(ctx, "proxy.scan", error)


@proxy_app.command("status")
def proxy_status(
    ctx: typer.Context,
    media_id: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Report original/proxy availability and the selected render sources."""

    try:
        service = _service(ctx)
        data = service.status(media_id)
        revision = service.manager.require_document().revision
        _emit(ctx, "proxy.status", data, revision)
    except Exception as error:
        _abort(ctx, "proxy.status", error)
