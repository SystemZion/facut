"""Compile subject trajectories into deterministic transform keyframes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from facut.core.project_manager import ProjectManager
from facut.core.timeline_engine import TimelineEngine


def build_reframe_plan(
    manager: ProjectManager,
    clip_id: str,
    trajectory_file: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
    confidence_threshold: float = 0.4,
    smoothing: int = 5,
) -> dict[str, Any]:
    document = manager.require_document()
    clip = document.find_clip(clip_id)
    if clip is None:
        raise ValueError(f'Clip "{clip_id}" was not found.')
    asset = document.find_media(clip.media_id)
    assert asset is not None
    source_width, source_height = asset.technical.width, asset.technical.height
    if not source_width or not source_height:
        raise ValueError("Auto reframe requires known source width and height.")
    raw = Path(trajectory_file).expanduser().resolve().read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    points = payload.get("observations", payload.get("points"))
    if not isinstance(points, list) or not points:
        raise ValueError('Subject trajectory requires a non-empty "observations" list.')
    scale = max(width / source_width, height / source_height)
    scaled_width, scaled_height = source_width * scale, source_height * scale
    accepted: list[dict[str, float]] = []
    for point in points:
        confidence = float(point.get("confidence", 1.0))
        if confidence < confidence_threshold:
            continue
        time = float(point["time"])
        cx, cy = float(point["cx"]), float(point["cy"])
        if not 0 <= time <= clip.duration or not 0 <= cx <= 1 or not 0 <= cy <= 1:
            raise ValueError("Trajectory time or normalized subject center is out of range.")
        accepted.append({"time": time, "cx": cx, "cy": cy, "confidence": confidence})
    if not accepted:
        raise ValueError("No subject observations passed the confidence threshold.")
    accepted.sort(key=lambda item: item["time"])
    smoothed: list[dict[str, float]] = []
    radius = max(0, smoothing // 2)
    for index, point in enumerate(accepted):
        window = accepted[max(0, index - radius) : index + radius + 1]
        total = sum(item["confidence"] for item in window)
        smoothed.append(
            {
                **point,
                "cx": sum(item["cx"] * item["confidence"] for item in window) / total,
                "cy": sum(item["cy"] * item["confidence"] for item in window) / total,
            }
        )
    max_x = max(0.0, (scaled_width - width) / 2)
    max_y = max(0.0, (scaled_height - height) / 2)
    keyframes: list[dict[str, Any]] = []
    for point in smoothed:
        x = max(-max_x, min(max_x, scaled_width / 2 - point["cx"] * scaled_width))
        y = max(-max_y, min(max_y, scaled_height / 2 - point["cy"] * scaled_height))
        keyframes.extend(
            [
                {"property": "x", "time": point["time"], "value": round(x, 3), "easing": "linear"},
                {"property": "y", "time": point["time"], "value": round(y, 3), "easing": "linear"},
            ]
        )
    return {
        "version": "1.0",
        "clip_id": clip_id,
        "target": {"width": width, "height": height, "aspect": round(width / height, 6)},
        "source": {"width": source_width, "height": source_height},
        "fit": "cover",
        "keyframes": keyframes,
        "accepted_observations": len(accepted),
        "rejected_observations": len(points) - len(accepted),
        "provider": payload.get("provider", "external-subject-tracker"),
        "limitations": ["FACUT compiles supplied subject centers; object detection/tracker inference is provider-specific."],
    }


def apply_reframe_plan(manager: ProjectManager, plan: dict[str, Any]):
    def operation(document):
        clip = TimelineEngine(document).transform_clip(
            plan["clip_id"], fit="cover", keyframes=plan["keyframes"]
        )
        clip.metadata["reframe"] = {
            "target": plan["target"],
            "provider": plan["provider"],
            "accepted_observations": plan["accepted_observations"],
        }
        return clip

    _, state = manager.mutate(
        "reframe.apply",
        f"Applied subject-aware reframe to {plan['clip_id']}",
        operation,
        command={"clip_id": plan["clip_id"], "target": plan["target"]},
    )
    return state
