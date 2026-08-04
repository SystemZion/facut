"""CutGraph-aware A/B preview rendering shared by CLI and Agent RPC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from facut.core.project_manager import ProjectManager
from facut.render.ffmpeg_backend import FFmpegBackend


def render_compare_previews(
    manager: ProjectManager,
    backend: FFmpegBackend,
    *,
    before: str,
    after: str,
    output_dir: str | Path | None = None,
    changed_only: bool = True,
    padding: float = 0.5,
    height: int = 360,
    fps: float = 24.0,
    hardware: str = "auto",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render paired previews for merged semantic-diff ranges."""

    current = manager.require_document()
    manager.cutgraph.initialize(current)
    left = manager.cutgraph.document_at(before)
    right = manager.cutgraph.document_at(after)
    diff = manager.diff_history(before, after)
    ranges = list(diff["changed_ranges"])
    maximum_duration = max(left.project.duration, right.project.duration)
    if not changed_only:
        ranges = [{"from": 0.0, "to": maximum_duration, "entity": "timeline"}]
    normalized: list[dict[str, float]] = []
    for item in sorted(ranges, key=lambda value: (value["from"], value["to"])):
        start = max(0.0, float(item["from"]) - padding)
        end = min(maximum_duration, float(item["to"]) + padding)
        if end <= start:
            continue
        if normalized and start <= normalized[-1]["to"] + 1e-6:
            normalized[-1]["to"] = max(normalized[-1]["to"], end)
        else:
            normalized.append({"from": start, "to": end})
    root = (
        Path(output_dir)
        if output_dir is not None
        else manager.project_dir
        / "previews"
        / f"compare-{manager.cutgraph.resolve(before)[:8]}-{manager.cutgraph.resolve(after)[:8]}"
    )
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(normalized, start=1):
        pair: dict[str, Any] = {"index": index, **item}
        for label, document in (("before", left), ("after", right)):
            destination = root / f"{index:03d}-{label}.mp4"
            if item["from"] >= document.project.duration:
                pair[label] = None
                continue
            result = backend.preview_range(
                document,
                manager.project_dir,
                destination,
                start=item["from"],
                end=min(item["to"], document.project.duration),
                height=height,
                fps=fps,
                hardware=hardware,
                overwrite=overwrite,
            )
            pair[label] = str(result.output)
        artifacts.append(pair)
    return {
        "before": manager.cutgraph.resolve(before),
        "after": manager.cutgraph.resolve(after),
        "changed_only": changed_only,
        "ranges": normalized,
        "artifacts": artifacts,
        "output_dir": str(root),
        "diff": diff["summary"],
    }
