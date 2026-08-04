"""High-level mixed-device travel ingestion workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.core.models import MediaKind
from facut.core.project_manager import ProjectManager
from facut.media.proxy_manager import ProxyError, ProxyManager
from facut.responses import success_response


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _project_slug(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-.")
    return cleaned or "facut-trip"


def ingest_command(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="Travel media root directory.")],
    trip: Annotated[str, typer.Option("--trip", help="Human-readable trip name.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", help="New project directory when --project is absent."),
    ] = None,
    verify: Annotated[str, typer.Option("--verify")] = "sha256",
    proxy: Annotated[
        str,
        typer.Option("--proxy", help="none, link, or auto (link then create)."),
    ] = "none",
    proxy_height: Annotated[int, typer.Option("--proxy-height")] = 540,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import a mixed-device trip, verify content hashes, and manage proxies."""

    from facut.cli.main import emit, fail

    command = "ingest"
    state = _state(ctx)
    try:
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError(f'Ingest root "{source}" was not found.')
        if verify.casefold() != "sha256":
            raise ValueError("The first release supports --verify sha256 only.")
        proxy_mode = proxy.casefold()
        if proxy_mode not in {"none", "link", "auto"}:
            raise ValueError("--proxy must be none, link, or auto.")
        if state.project is not None:
            manager = ProjectManager(state.project)
            manager.load()
        else:
            destination = (output or Path.cwd() / _project_slug(trip)).resolve()
            if dry_run:
                raise ValueError(
                    "--dry-run requires an existing --project because project creation is not simulated."
                )
            manager = ProjectManager.create(
                destination,
                name=trip,
                width=3840,
                height=2160,
                fps=30.0,
                sample_rate=48000,
            )
        assets = manager.import_paths(
            [source],
            recursive=True,
            exclude_proxy_candidates=proxy_mode != "none",
            dry_run=dry_run,
        )
        if not dry_run:
            def record_trip(document):
                document.settings["trip"] = {
                    "name": trip,
                    "source_root": manager.store_path(source),
                    "verification": "sha256",
                }
                return document.settings["trip"]

            manager.mutate(
                "ingest.metadata",
                f"Recorded trip metadata for {trip}",
                record_trip,
                command={"trip": trip, "verify": verify},
            )
        proxy_results: list[dict[str, object]] = []
        if proxy_mode != "none" and not dry_run:
            service = ProxyManager(
                manager,
                ffmpeg=state.config.tools.ffmpeg,
                ffprobe=state.config.tools.ffprobe,
            )
            proxy_results, _ = service.scan(
                search_directories=[source],
                link=True,
            )
            if proxy_mode == "auto":
                matched_ids = {
                    str(item["media_id"])
                    for item in proxy_results
                    if item["status"] == "linked"
                }
                for asset in assets:
                    if asset.kind != MediaKind.VIDEO or asset.id in matched_ids:
                        continue
                    _, _, generated = service.create(
                        asset.id, height=proxy_height, codec="h264", overwrite=False
                    )
                    proxy_results.append(
                        {"media_id": asset.id, "status": "created", "path": str(generated)}
                    )
        document = manager.require_document()
        data = {
            "project": str(manager.project_file.resolve()),
            "trip": trip,
            "verification": {
                "algorithm": "sha256",
                "verified": not dry_run,
                "asset_count": len(assets),
            },
            "media": [asset.model_dump(mode="json") for asset in assets],
            "proxies": proxy_results,
            "dry_run": dry_run,
        }
        emit(
            state,
            success_response(command, data, project_revision=document.revision),
            human=json.dumps(data, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        fail(state, command, public_error(error))
