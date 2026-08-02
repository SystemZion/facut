from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

import pytest

from facut.core.models import (
    Clip,
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
    SubtitleCue,
    Track,
    TrackType,
)
from facut.render.ffmpeg_backend import FFmpegBackend
from facut.render.incremental import IncrementalRenderer, incremental_eligibility


def _project() -> ProjectDocument:
    return ProjectDocument(
        project=ProjectSettings(name="incremental", duration=4),
        media=[
            MediaAsset(
                id="a",
                kind=MediaKind.VIDEO,
                path=str(Path("a.mp4")),
                original_name="a.mp4",
                size=1,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(duration=2, video_codec="h264"),
            ),
            MediaAsset(
                id="b",
                kind=MediaKind.VIDEO,
                path=str(Path("b.mp4")),
                original_name="b.mp4",
                size=1,
                sha256="2" * 64,
                technical=MediaTechnicalInfo(duration=2, video_codec="h264"),
            ),
        ],
        tracks=[
            Track(
                id="V1",
                type=TrackType.VIDEO,
                name="V1",
                clips=[
                    Clip(id="c1", media_id="a", track_id="V1", source_out=2),
                    Clip(
                        id="c2",
                        media_id="b",
                        track_id="V1",
                        timeline_start=2,
                        source_out=2,
                    ),
                ],
            )
        ],
    )


def test_incremental_eligibility_accepts_contiguous_simple_timeline() -> None:
    eligible, reason = incremental_eligibility(_project())
    assert eligible is True
    assert reason is None


def test_incremental_eligibility_explains_cross_boundary_content() -> None:
    project = _project()
    project.subtitle_cues.append(
        SubtitleCue(track_id="S1", start=0, end=1, text="caption")
    )
    eligible, reason = incremental_eligibility(project)
    assert eligible is False
    assert reason and "subtitles" in reason


def test_real_incremental_second_render_reuses_all_segments(tmp_path: Path) -> None:
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
    if " libx264 " in encoders:
        encoder, hardware = "libx264", "none"
    elif " h264_qsv " in encoders:
        encoder, hardware = "h264_qsv", "qsv"
    else:
        pytest.skip("No usable H.264 encoder is available")
    project = _project()
    project.project.width = 320
    project.project.height = 180
    project.project.fps = 30
    for asset, color in zip(project.media, ("red", "blue"), strict=True):
        output = tmp_path / asset.path
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
                str(output),
            ],
            check=True,
        )
        asset.size = output.stat().st_size
    renderer = IncrementalRenderer(FFmpegBackend(ffmpeg), tmp_path / "cache")
    first = renderer.render(
        project,
        tmp_path,
        tmp_path / "first.mp4",
        hardware=hardware,
    )
    second = renderer.render(
        project,
        tmp_path,
        tmp_path / "second.mp4",
        hardware=hardware,
    )
    assert first.segments_reused == 0
    assert second.cached is True
    assert second.segments_reused == second.segments_total == 2
    assert second.output.stat().st_size > 1000
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    duration = float(
        subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                second.output,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert duration == pytest.approx(4.0, abs=0.01)
