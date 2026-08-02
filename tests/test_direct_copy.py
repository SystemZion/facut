from __future__ import annotations

import os
from pathlib import Path
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
    Track,
    TrackType,
)
from facut.render.direct_copy import direct_copy_plan, render_direct_copy


def _project(root: Path) -> ProjectDocument:
    technical = MediaTechnicalInfo(
        duration=1.0,
        video_codec="h264",
        audio_codec="aac",
        width=320,
        height=180,
        frame_rate=30,
        pixel_format="yuv420p",
        audio_channels=1,
        sample_rate=48000,
    )
    return ProjectDocument(
        project=ProjectSettings(
            name="direct-copy", width=320, height=180, fps=30, duration=2
        ),
        media=[
            MediaAsset(
                id="a",
                kind=MediaKind.VIDEO,
                path="a.mp4",
                original_name="a.mp4",
                size=(root / "a.mp4").stat().st_size,
                sha256="a" * 64,
                technical=technical,
            ),
            MediaAsset(
                id="b",
                kind=MediaKind.VIDEO,
                path="b.mp4",
                original_name="b.mp4",
                size=(root / "b.mp4").stat().st_size,
                sha256="b" * 64,
                technical=technical.model_copy(deep=True),
            ),
        ],
        tracks=[
            Track(
                id="V1",
                type=TrackType.VIDEO,
                name="V1",
                clips=[
                    Clip(id="c1", media_id="a", track_id="V1", source_out=1),
                    Clip(
                        id="c2",
                        media_id="b",
                        track_id="V1",
                        timeline_start=1.001,
                        source_out=1,
                    ),
                ],
            )
        ],
    )


def test_direct_copy_accepts_full_identical_files_and_snaps_subframe(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mp4").write_bytes(b"b")
    plan = direct_copy_plan(
        _project(tmp_path),
        tmp_path,
        codec="h264",
        width=None,
        height=None,
        fps=None,
        audio_sample_rate=None,
        color_space=None,
    )
    assert plan.eligible is True
    assert plan.duration == pytest.approx(2.0)
    assert any("Sub-frame" in warning for warning in plan.warnings)


def test_direct_copy_rejects_trimmed_clip_unless_forced(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mp4").write_bytes(b"b")
    project = _project(tmp_path)
    project.tracks[0].clips[0].source_in = 0.1
    plan = direct_copy_plan(
        project,
        tmp_path,
        codec="h264",
        width=None,
        height=None,
        fps=None,
        audio_sample_rate=None,
        color_space=None,
    )
    assert plan.eligible is False
    assert "trimmed" in str(plan.reason)


def test_direct_copy_real_concat_is_playable(tmp_path: Path) -> None:
    ffmpeg = os.environ.get("FACUT_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    if " libx264 " not in encoders:
        pytest.skip("libx264 is unavailable")
    for name, color, frequency in (("a.mp4", "red", "440"), ("b.mp4", "blue", "660")):
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x180:r=30:d=1",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=1",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(tmp_path / name),
            ],
            check=True,
        )
    project = _project(tmp_path)
    plan = direct_copy_plan(
        project,
        tmp_path,
        codec="h264",
        width=None,
        height=None,
        fps=None,
        audio_sample_rate=None,
        color_space=None,
    )
    result = render_direct_copy(
        ffmpeg, plan, tmp_path / "out.mp4", overwrite=False
    )
    assert result.encoder == "copy"
    assert result.output.stat().st_size > 1000
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe")
    duration = float(
        subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(result.output),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert duration == pytest.approx(2.0, abs=0.05)
