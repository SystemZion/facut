"""Human-readable QC report serialization."""

from __future__ import annotations

from pathlib import Path
import json

from .models import QCReport


def render_markdown(report: QCReport) -> str:
    lines = [
        "# facut QC report",
        "",
        f"- Scope: `{report.scope.get('type', 'unknown')}`",
        f"- Status: **{report.status.value.upper()}**",
        f"- Files: {report.summary.get('total', 0)}",
        f"- Passed: {report.summary.get('passed', 0)}",
        f"- Warnings: {report.summary.get('warnings', 0)}",
        f"- Failed: {report.summary.get('failed', 0)}",
        "",
    ]
    for item in report.files:
        label = item.media_id or Path(item.path).name
        lines.extend([f"## {label}", "", f"Status: **{item.status.value.upper()}**", ""])
        lines.extend(["| Check | Status | Summary |", "|---|---:|---|"])
        for name, check in item.checks.items():
            summary = check.summary.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {name} | {check.status.value} | {summary} |")
        if item.contact_sheet:
            lines.extend(["", f"Contact sheet: `{item.contact_sheet}`"])
        if item.issues:
            lines.extend(["", "Issues:", *[f"- {issue}" for issue in item.issues]])
        lines.append("")
    if report.warnings:
        lines.extend(["## Report warnings", "", *[f"- {item}" for item in report.warnings], ""])
    return "\n".join(lines)


def write_markdown(
    report: QCReport, output: str | Path, *, overwrite: bool = False
) -> Path:
    destination = Path(output).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f'Output "{destination}" already exists. Use --overwrite to replace it.'
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return destination


def write_json(
    report: QCReport, output: str | Path, *, overwrite: bool = False
) -> Path:
    """Write the complete machine-readable QC evidence atomically."""

    destination = Path(output).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f'Output "{destination}" already exists. Use --overwrite to replace it.'
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
