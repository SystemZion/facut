from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo, ProjectDocument, ProjectSettings
from facut.core.timeline_engine import TimelineEngine
from facut.render.ffmpeg_backend import FFmpegBackend


def _tools() -> tuple[str, str]:
    ffmpeg = os.environ.get("FACUT_TEST_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FACUT_TEST_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Modern FFmpeg/FFprobe are unavailable")
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
    ).stdout
    if " libx264 " not in encoders:
        pytest.skip("libx264 is unavailable for deterministic software render test")
    return ffmpeg, ffprobe


def _source(tmp_path: Path, ffmpeg: str) -> Path:
    output = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(output),
        ],
        check=True,
    )
    return output


def _project(source: Path) -> tuple[ProjectDocument, TimelineEngine]:
    document = ProjectDocument(
        project=ProjectSettings(name="speed-render", width=320, height=180, fps=30),
        media=[
            MediaAsset(
                id="source",
                kind=MediaKind.VIDEO,
                path=source.name,
                original_name=source.name,
                size=source.stat().st_size,
                sha256="a" * 64,
                technical=MediaTechnicalInfo(
                    duration=2,
                    width=320,
                    height=180,
                    frame_rate=30,
                    video_codec="h264",
                    audio_codec="aac",
                    sample_rate=48000,
                ),
            )
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    return document, timeline


def _duration(path: Path, ffprobe: str) -> float:
    payload = json.loads(
        subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    return float(payload["format"]["duration"])


def test_real_reverse_and_speed_curve_render_video_and_audio(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    source = _source(tmp_path, ffmpeg)

    reverse_document, reverse_timeline = _project(source)
    reverse_clip = reverse_timeline.add_clip("source", "V1", source_out=2)
    reverse_timeline.set_speed(reverse_clip.id, rate=-2)
    reverse_output = tmp_path / "reverse.mp4"
    FFmpegBackend(ffmpeg).render(reverse_document, tmp_path, reverse_output, hardware="none")
    assert _duration(reverse_output, ffprobe) == pytest.approx(1.0, abs=1 / 30)

    curve_document, curve_timeline = _project(source)
    curve_clip = curve_timeline.add_clip("source", "V1", source_out=2)
    segments = curve_timeline.apply_speed_curve(
        curve_clip.id,
        curve={
            "mode": "step",
            "points": [
                {"at": 0.0, "rate": 1.0},
                {"at": 1.0, "rate": 2.0},
                {"at": 2.0, "rate": 2.0},
            ],
        },
    )
    assert [item.speed for item in segments] == [1.0, 2.0]
    curve_output = tmp_path / "curve.mp4"
    FFmpegBackend(ffmpeg).render(curve_document, tmp_path, curve_output, hardware="none")
    assert _duration(curve_output, ffprobe) == pytest.approx(1.5, abs=1 / 30)
