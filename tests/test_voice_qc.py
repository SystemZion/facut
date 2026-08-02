from __future__ import annotations

import math
from pathlib import Path
import struct
import wave

from facut.voice import validate_voice_samples


def _write(
    path: Path,
    *,
    seconds: float = 1.0,
    channels: int = 1,
    rate: int = 48000,
    clipped: bool = False,
) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        frames = bytearray()
        for index in range(round(seconds * rate)):
            value = 32767 if clipped else round(math.sin(2 * math.pi * 220 * index / rate) * 6000)
            for _ in range(channels):
                frames.extend(struct.pack("<h", value))
        stream.writeframes(frames)
    return path


def test_clean_pcm_voice_sample_passes_when_duration_target_is_met(tmp_path: Path) -> None:
    report = validate_voice_samples(
        [_write(tmp_path / "clean.wav")], recommended_total_seconds=1
    )
    assert report["status"] == "pass"
    assert report["files"][0]["metrics"]["sample_rate"] == 48000
    assert report["files"][0]["metrics"]["clipping_ratio"] == 0


def test_clipping_fails_and_stereo_warns(tmp_path: Path) -> None:
    clipped = validate_voice_samples(
        [_write(tmp_path / "clip.wav", clipped=True)], recommended_total_seconds=1
    )
    assert clipped["status"] == "fail"
    assert "CLIPPING" in {item["code"] for item in clipped["files"][0]["issues"]}
    stereo = validate_voice_samples(
        [_write(tmp_path / "stereo.wav", channels=2)], recommended_total_seconds=1
    )
    assert stereo["status"] == "warning"
    assert "NOT_MONO" in {item["code"] for item in stereo["files"][0]["issues"]}


def test_short_profile_is_reported_without_false_success(tmp_path: Path) -> None:
    report = validate_voice_samples(
        [_write(tmp_path / "short.wav", seconds=0.75)], recommended_total_seconds=600
    )
    assert report["status"] == "warning"
    assert report["issues"][0]["code"] == "INSUFFICIENT_DURATION"
