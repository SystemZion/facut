"""CLI commands for installing, updating, and relocating optional models."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Annotated, Any

import typer

from facut.cli.common import public_error
from facut.config import AppConfig, ModelConfig, load_config, save_config
from facut.installation import install_facut, update_facut
from facut.responses import success_response


models_app = typer.Typer(help="Inspect, link, or relocate separately stored AI models.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data: dict[str, Any], *, warnings=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, warnings=warnings or []),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def install_command(
    ctx: typer.Context,
    directory: Annotated[Path | None, typer.Option("--directory", help="Small FACUT executable directory.")] = None,
    model_directory: Annotated[
        Path | None, typer.Option("--model-directory", help="Independent large-model storage root.")
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="model, voice_model, or srt_model; repeat as needed."),
    ] = None,
    executable: Annotated[
        Path | None, typer.Option("--executable", hidden=True, help="Explicit built FACUT executable.")
    ] = None,
    add_path: Annotated[bool, typer.Option("--path/--no-path", help="Add the install directory to user PATH.")] = True,
    native: Annotated[
        bool,
        typer.Option("--native/--no-native", help="Install the adjacent C++ accelerator bundle when present."),
    ] = True,
) -> None:
    """Install FACUT, replacing an older EXE and optionally downloading models."""

    state = _state(ctx)

    def progress(event: dict[str, Any]) -> None:
        if state.json_output or state.quiet:
            return
        if event.get("event") == "file_complete":
            typer.echo(f'模型文件完成：{event.get("file")}')
        elif event.get("event") == "source_benchmark":
            typer.echo(f'模型源测速：{event.get("source")} {event.get("mbps", 0):.2f} MB/s')

    try:
        result = install_facut(
            executable=executable,
            directory=directory,
            model_directory=model_directory,
            exclude=exclude or [],
            add_path=add_path,
            native=native,
            progress=progress,
        )
        warnings = []
        if result["path"].get("changed"):
            warnings.append("PATH was updated; already-open terminals may need to be reopened.")
        if native and not result["native"].get("installed"):
            warnings.append(result["native"].get("reason", "FACUT Native was not installed."))
        _emit(ctx, "install", result, warnings=warnings)
    except Exception as error:
        _fail(ctx, "install", error)


def update_command(
    ctx: typer.Context,
    check: Annotated[bool, typer.Option("--check", help="Only check the latest GitHub Release.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Install even when the version is not newer.")] = False,
    repository: Annotated[str, typer.Option("--repository")] = "SystemZion/facut",
    from_file: Annotated[
        Path | None, typer.Option("--from-file", hidden=True, help="Test or offline update source.")
    ] = None,
    directory: Annotated[
        Path | None, typer.Option("--directory", hidden=True, help="Explicit install directory.")
    ] = None,
) -> None:
    """Update FACUT from the latest GitHub Release with resumable download."""

    try:
        result = update_facut(
            repository=repository,
            check_only=check,
            force=force,
            from_file=from_file,
            directory=directory,
        )
        warnings = []
        if result.get("mode") == "scheduled-after-exit":
            warnings.append("The EXE will be replaced immediately after this FACUT process exits.")
        _emit(ctx, "update", result, warnings=warnings)
    except Exception as error:
        _fail(ctx, "update", error)


@models_app.command("path")
def models_path(
    ctx: typer.Context,
    directory: Annotated[Path | None, typer.Argument()] = None,
    copy_existing: Annotated[
        bool, typer.Option("--copy-existing", help="Copy recognized model folders; preserve originals.")
    ] = False,
) -> None:
    """Show or persist a model root that is independent from the EXE directory."""

    try:
        config = load_config()
        current = Path(config.models.directory).expanduser().resolve()
        if directory is None:
            _emit(
                ctx,
                "models.path",
                {"directory": str(current), "exists": current.is_dir(), "configured": True},
            )
            return
        destination = directory.expanduser().resolve()
        copied = []
        if copy_existing and current.is_dir() and current != destination:
            destination.mkdir(parents=True, exist_ok=True)
            for source in sorted(current.iterdir()):
                if source.is_dir():
                    target = destination / source.name
                    shutil.copytree(source, target, dirs_exist_ok=True)
                    copied.append(source.name)
        values = config.model_dump()
        values["models"]["directory"] = destination
        save_config(AppConfig.model_validate(values))
        _state(ctx).config = load_config()
        _emit(
            ctx,
            "models.path",
            {
                "previous": str(current),
                "directory": str(destination),
                "copied": copied,
                "originals_preserved": True,
            },
        )
    except Exception as error:
        _fail(ctx, "models.path", error)


@models_app.command("link")
def models_link(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="voice_model or srt_model")],
    directory: Annotated[Path, typer.Argument(help="Existing model directory")],
) -> None:
    """Link an existing model in place without copying its large files."""

    try:
        normalized = model.strip().casefold().replace("-", "_")
        if normalized not in {"voice_model", "srt_model"}:
            raise ValueError("Model name must be voice_model or srt_model.")
        target = directory.expanduser().resolve()
        if not target.is_dir():
            raise FileNotFoundError(f'Model directory "{target}" was not found.')
        config = load_config()
        values = config.model_dump()
        values["models"][normalized] = target
        save_config(AppConfig.model_validate(values))
        _state(ctx).config = load_config()
        _emit(
            ctx,
            "models.link",
            {"model": normalized, "directory": str(target), "copied": False},
        )
    except Exception as error:
        _fail(ctx, "models.link", error)


@models_app.command("status")
def models_status(ctx: typer.Context) -> None:
    """Report resolved model locations and whether each model is ready."""

    try:
        config = load_config()
        data = {
            name: {
                "directory": str(config.models.resolve(name)),
                "available": config.models.resolve(name).is_dir(),
                "explicit": str(getattr(config.models, name))
                if getattr(config.models, name) is not None
                else None,
            }
            for name in ("voice_model", "srt_model")
        }
        _emit(ctx, "models.status", data)
    except Exception as error:
        _fail(ctx, "models.status", error)
