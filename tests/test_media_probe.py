from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from facut.media.probe import _parse_probe, probe_media


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


def test_probe_parses_camera_gps_timezone_and_hdr_metadata() -> None:
    info = _parse_probe(
        {
            "format": {
                "format_name": "mov,mp4",
                "duration": "2.0",
                "tags": {
                    "creation_time": "2026-07-20T18:30:00+08:00",
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 17 Pro",
                    "com.apple.quicktime.location.ISO6709": "+31.2304+121.4737+004.2/",
                },
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "color_transfer": "smpte2084",
                    "tags": {"timecode": "10:00:00:00"},
                }
            ],
        }
    )
    assert info.camera_make == "Apple"
    assert info.camera_model == "iPhone 17 Pro"
    assert info.latitude == pytest.approx(31.2304)
    assert info.longitude == pytest.approx(121.4737)
    assert info.altitude == pytest.approx(4.2)
    assert info.timezone_offset == "+08:00"
    assert info.dynamic_range == "hdr-pq"
    assert info.timecode == "10:00:00:00"
