from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

import pytest

from facut.core.models import (
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
)
from facut.core.timeline_engine import TimelineEngine
from facut.render.graph_builder import GraphBuilder
from facut.render.ffmpeg_backend import FFmpegBackend


def _project(root: Path) -> tuple[ProjectDocument, TimelineEngine, str]:
    for name in ("base.mp4", "overlay.mp4"):
        (root / name).write_bytes(b"placeholder")
    document = ProjectDocument(
        project=ProjectSettings(name="effects", width=320, height=180, fps=30),
        media=[
            MediaAsset(
                id="base",
                kind=MediaKind.VIDEO,
                path="base.mp4",
                original_name="base.mp4",
                size=11,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(duration=2),
            ),
            MediaAsset(
                id="overlay",
                kind=MediaKind.VIDEO,
                path="overlay.mp4",
                original_name="overlay.mp4",
                size=11,
                sha256="2" * 64,
                technical=MediaTechnicalInfo(duration=2),
            ),
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    timeline.add_track("video", "V2")
    timeline.add_track("adjustment", "ADJ1")
    timeline.add_clip("base", "V1", at=0, source_in=0, source_out=2)
    overlay = timeline.add_clip(
        "overlay", "V2", at=0.25, source_in=0, source_out=1.25
    )
    return document, timeline, overlay.id


def test_effect_add_remove_and_adjustment_graph(tmp_path: Path) -> None:
    document, timeline, overlay_id = _project(tmp_path)
    effect = timeline.add_effect(overlay_id, "brightness", {"value": 0.1})
    adjustment = timeline.add_adjustment(
        "ADJ1", effect_type="vignette", at="0.5s", duration="1s", parameters={}
    )
    graph = GraphBuilder(document, tmp_path).build()
    assert "eq=brightness=0.1" in graph.filter_complex
    assert "vignette=PI/5:enable='between(t\\,0.5\\,1.5)'" in graph.filter_complex
    assert adjustment["id"].startswith("adjustment_")
    assert timeline.remove_effect(effect.id).id == effect.id


def test_mask_and_blend_mode_graphs(tmp_path: Path) -> None:
    document, timeline, overlay_id = _project(tmp_path)
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"placeholder")
    timeline.configure_composite(overlay_id, mask_path="mask.png")
    graph = GraphBuilder(document, tmp_path).build()
    assert "scale2ref=w=rw:h=rh" in graph.filter_complex
    assert "alphamerge" in graph.filter_complex

    document, timeline, overlay_id = _project(tmp_path)
    timeline.configure_composite(overlay_id, blend_mode="screen")
    graph = GraphBuilder(document, tmp_path).build()
    assert "blend=all_mode=screen" in graph.filter_complex


def test_non_normal_blend_rejects_positioned_overlay(tmp_path: Path) -> None:
    document, timeline, overlay_id = _project(tmp_path)
    timeline.transform_clip(overlay_id, x=10)
    timeline.configure_composite(overlay_id, blend_mode="multiply")
    with pytest.raises(NotImplementedError, match="NOT_IMPLEMENTED"):
        GraphBuilder(document, tmp_path).build()


def test_real_mask_adjustment_and_blend_render(tmp_path: Path) -> None:
    ffmpeg = os.environ.get("FACUT_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    encoder = "libx264" if " libx264 " in encoders else "h264_qsv"
    document, timeline, overlay_id = _project(tmp_path)
    for name, color in (("base", "blue"), ("overlay", "red")):
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x180:r=30:d=2",
                "-c:v",
                encoder,
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(tmp_path / f"{name}.mp4"),
            ],
            check=True,
        )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=80x80:r=30:d=1",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(tmp_path / "mask.mp4"),
        ],
        check=True,
    )
    timeline.configure_composite(overlay_id, mask_path="mask.mp4")
    timeline.add_adjustment(
        "ADJ1", effect_type="brightness", at=0.25, duration=1, parameters={"value": 0.1}
    )
    backend = FFmpegBackend(ffmpeg)
    masked = backend.render(
        document,
        tmp_path,
        tmp_path / "masked.mp4",
        hardware="none" if encoder == "libx264" else "qsv",
    )
    assert masked.output.stat().st_size > 1000

    overlay = document.find_clip(overlay_id)
    assert overlay is not None
    overlay.metadata.pop("mask_path")
    overlay.metadata["blend_mode"] = "screen"
    blended = backend.render(
        document,
        tmp_path,
        tmp_path / "blended.mp4",
        hardware="none" if encoder == "libx264" else "qsv",
    )
    assert blended.output.stat().st_size > 1000
