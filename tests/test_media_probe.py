from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from facut.media.probe import probe_media


@pytest.fixture()
def sample_video(tmp_path):
    ffmpeg = os.environ.get("FACUT_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    capability = subprocess.run(
        [ffmpeg, "-hide_banner", "-version"], capture_output=True, check=False
    )
    if capability.returncode:
        pytest.skip("Installed FFmpeg is too old for the integration test")
    output = tmp_path / "sample.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=25:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
    )
    return output


def test_probe_extracts_video_and_audio_metadata(sample_video) -> None:
    info = probe_media(sample_video)
    assert info.video_codec == "mpeg4"
    assert info.audio_codec == "aac"
    assert (info.width, info.height) == (160, 90)
    assert info.frame_rate == pytest.approx(25)
    assert info.sample_rate == 48000
    assert info.duration == pytest.approx(1, abs=0.1)


def test_probe_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        probe_media("definitely-does-not-exist.mp4")
