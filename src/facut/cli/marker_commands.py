"""Semantic timeline markers and portable exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.core.models import Marker, ProjectDocument
from facut.core.timeline_engine import parse_time
from facut.responses import success_response


marker_app = typer.Typer(help="Manage rated, labeled, and recommended timeline ranges.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _emit(ctx: typer.Context, command: str, data: object, revision: int) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _marker_row(marker: Marker) -> dict[str, Any]:
    metadata = marker.metadata
    end = metadata.get("end")
    return {
        "id": marker.id,
        "at": marker.at,
        "end": end,
        "duration": max(0.0, float(end) - marker.at) if end is not None else None,
        "label": marker.label,
        "category": metadata.get("category"),
        "rating": metadata.get("rating"),
        "recommended": bool(metadata.get("recommended", False)),
        "tags": metadata.get("tags", []),
        "note": metadata.get("note"),
        "color": marker.color,
    }


@marker_app.command("add")
def marker_add(
    ctx: typer.Context,
    at: Annotated[str, typer.Option("--at")],
    label: Annotated[str, typer.Option("--label")],
    end: Annotated[str | None, typer.Option("--end")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
    rating: Annotated[int | None, typer.Option("--rating", min=1, max=5)] = None,
    recommended: Annotated[bool, typer.Option("--recommended")] = False,
    tags: Annotated[str | None, typer.Option("--tags")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    color: Annotated[str, typer.Option("--color")] = "#FFD54F",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Add a point or recommended range such as speech, chorus, or reaction."""

    command = "marker.add"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        at_seconds = float(parse_time(at, document.project.fps).seconds)
        end_seconds = (
            float(parse_time(end, document.project.fps).seconds)
            if end is not None
            else None
        )
        if end_seconds is not None and end_seconds <= at_seconds:
            raise ValueError("Marker range end must be after its start.")
        metadata = {
            "category": category,
            "rating": rating,
            "recommended": recommended,
            "tags": [item.strip() for item in (tags or "").split(",") if item.strip()],
            "note": note,
            "end": end_seconds,
        }

        def operation(candidate: ProjectDocument) -> Marker:
            marker = Marker(at=at_seconds, label=label, color=color, metadata=metadata)
            candidate.markers.append(marker)
            return marker

        marker, state = manager.mutate(
            command,
            f"Added marker {label} at {at}",
            operation,
            command={"at": at, "end": end, "label": label, **metadata},
            dry_run=dry_run,
        )
        _emit(
            ctx,
            command,
            {"marker": _marker_row(marker), "dry_run": dry_run},
            state.revision,
        )
    except Exception as error:
        _abort(ctx, command, error)


@marker_app.command("list")
def marker_list(
    ctx: typer.Context,
    category: Annotated[str | None, typer.Option("--category")] = None,
    minimum_rating: Annotated[int | None, typer.Option("--min-rating")] = None,
    recommended_only: Annotated[bool, typer.Option("--recommended-only")] = False,
) -> None:
    """List and filter marker points and ranges."""

    command = "marker.list"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        rows = [_marker_row(item) for item in sorted(document.markers, key=lambda m: m.at)]
        rows = [
            row
            for row in rows
            if (category is None or row["category"] == category)
            and (
                minimum_rating is None
                or int(row["rating"] or 0) >= minimum_rating
            )
            and (not recommended_only or row["recommended"])
        ]
        _emit(ctx, command, rows, document.revision)
    except Exception as error:
        _abort(ctx, command, error)


@marker_app.command("remove")
def marker_remove(
    ctx: typer.Context,
    marker_id: Annotated[str, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Remove one marker by stable ID."""

    command = "marker.remove"
    try:
        manager = manager_for(_state(ctx))

        def operation(document: ProjectDocument) -> Marker:
            marker = next((item for item in document.markers if item.id == marker_id), None)
            if marker is None:
                raise ValueError(f'Marker "{marker_id}" was not found.')
            document.markers.remove(marker)
            return marker

        marker, state = manager.mutate(
            command,
            f"Removed marker {marker_id}",
            operation,
            command={"marker_id": marker_id},
            dry_run=dry_run,
        )
        _emit(
            ctx,
            command,
            {"marker": _marker_row(marker), "dry_run": dry_run},
            state.revision,
        )
    except Exception as error:
        _abort(ctx, command, error)


@marker_app.command("export")
def marker_export(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    export_format: Annotated[str | None, typer.Option("--format")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Export marker data to CSV or XLSX for review and handoff."""

    command = "marker.export"
    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        if output.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{output}" exists; use --overwrite to replace it.'
            )
        selected = (export_format or output.suffix.lstrip(".")).lower()
        if selected not in {"csv", "xlsx"}:
            raise ValueError("Marker export format must be csv or xlsx.")
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = [_marker_row(item) for item in sorted(document.markers, key=lambda m: m.at)]
        columns = [
            "id",
            "at",
            "end",
            "duration",
            "label",
            "category",
            "rating",
            "recommended",
            "tags",
            "note",
            "color",
        ]
        export_rows = [
            {**row, "tags": ",".join(row["tags"])}
            for row in rows
        ]
        if selected == "csv":
            with output.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(export_rows)
        else:
            from openpyxl import Workbook

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "facut markers"
            sheet.append(columns)
            for row in export_rows:
                sheet.append([row[column] for column in columns])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            workbook.save(output)
        _emit(
            ctx,
            command,
            {"output": str(output.resolve()), "format": selected, "count": len(rows)},
            document.revision,
        )
    except Exception as error:
        _abort(ctx, command, error)
