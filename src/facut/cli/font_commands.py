"""Font discovery, matching, registration, and project audit commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.fonts import FONT_ROLES, FontCatalog
from facut.responses import success_response


font_app = typer.Typer(help="Discover and audit licensed fonts for adaptive VLOG typography.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, *, revision=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


@font_app.command("scan")
def font_scan(ctx: typer.Context, refresh: Annotated[bool, typer.Option("--refresh")] = False) -> None:
    """Scan system and registered user fonts without copying font files."""

    try:
        _emit(ctx, "font.scan", FontCatalog().scan(refresh=refresh))
    except Exception as error:
        _fail(ctx, "font.scan", error)


@font_app.command("list")
def font_list(ctx: typer.Context) -> None:
    """List discoverable fonts and logical typography roles."""

    try:
        _emit(ctx, "font.list", FontCatalog().scan())
    except Exception as error:
        _fail(ctx, "font.list", error)


@font_app.command("register")
def font_register(
    ctx: typer.Context,
    font_file: Annotated[Path, typer.Argument()],
    license_file: Annotated[Path, typer.Option("--license-file")],
) -> None:
    """Register an external font reference and its license; preserve the original file."""

    try:
        _emit(ctx, "font.register", FontCatalog().register(font_file, license_file=license_file))
    except Exception as error:
        _fail(ctx, "font.register", error)


@font_app.command("match")
def font_match(
    ctx: typer.Context,
    role: Annotated[str, typer.Option("--role")],
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
) -> None:
    """Resolve a logical content role to a real installed font and fallback evidence."""

    try:
        _emit(ctx, "font.match", FontCatalog().match(role, language=language))
    except Exception as error:
        _fail(ctx, "font.match", error)


@font_app.command("roles")
def font_roles(ctx: typer.Context) -> None:
    """List stable logical font roles and their ordered fallback families."""

    _emit(ctx, "font.roles", FONT_ROLES)


@font_app.command("audit")
def font_audit(
    ctx: typer.Context,
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
) -> None:
    """Fail visibly on missing fonts or glyphs before final delivery."""

    try:
        manager = manager_for(_state(ctx))
        data = FontCatalog().audit_project(manager.require_document(), language=language)
        _emit(ctx, "font.audit", data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "font.audit", error)
