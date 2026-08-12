from __future__ import annotations

from array import array
import math
import os
from pathlib import Path
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
from facut.render.ffmpeg_backend import FFmpegBackend
from facut.render.graph_builder import GraphBuilder


def _modern_ffmpeg() -> tuple[str, str] | None:
    candidates = [
        os.environ.get("FACUT_TEST_FFMPEG"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        filters = subprocess.run(
            [candidate, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        encoders = subprocess.run(
            [candidate, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if " amix " not in filters.stdout or " aloop " not in filters.stdout:
            continue
        if " libx264 " in encoders.stdout:
            return str(candidate), "none"
        if " h264_qsv " in encoders.stdout:
            return str(candidate), "qsv"
    return None


def _project(root: Path) -> tuple[ProjectDocument, str]:
    document = ProjectDocument(
        project=ProjectSettings(name="audio-test", width=320, height=180, fps=30),
        media=[
            MediaAsset(
                id="camera",
                kind=MediaKind.VIDEO,
                path="camera.mp4",
                original_name="camera.mp4",
                size=(root / "camera.mp4").stat().st_size,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(duration=3, audio_codec="aac"),
            ),
            MediaAsset(
                id="music",
                kind=MediaKind.AUDIO,
                path="music.wav",
                original_name="music.wav",
                size=(root / "music.wav").stat().st_size,
                sha256="2" * 64,
                technical=MediaTechnicalInfo(
                    duration=1, audio_codec="pcm_s16le", sample_rate=48000
                ),
            ),
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    timeline.add_clip("camera", "V1", source_out=3)
    timeline.add_track("audio", "A1")
    music = timeline.add_audio_clip(
        "music",
        "A1",
        volume_db=-12,
        fade_in=0.2,
        fade_out=0.2,
        loop=True,
    )
    return document, music.id


def _tone_strength(samples: array, frequency: float, rate: int = 48000) -> float:
    real = 0.0
    imaginary = 0.0
    for index, sample in enumerate(samples):
        angle = 2 * math.pi * frequency * index / rate
        real += sample * math.cos(angle)
        imaginary += sample * math.sin(angle)
    return math.hypot(real, imaginary) / len(samples)


def test_audio_track_builds_loop_fades_gain_and_mix(tmp_path: Path) -> None:
    (tmp_path / "camera.mp4").write_bytes(b"video")
    (tmp_path / "music.wav").write_bytes(b"audio")
    document, music_id = _project(tmp_path)
    clip = document.find_clip(music_id)
    assert clip is not None
    assert clip.duration == pytest.approx(3)
    graph = GraphBuilder(document, tmp_path).build()
    assert "aloop=loop=-1" in graph.filter_complex
    assert "volume=-12dB" in graph.filter_complex
    assert "afade=t=in:st=0:d=0.2" in graph.filter_complex
    assert "afade=t=out:st=2.8:d=0.2" in graph.filter_complex
    assert "amix=inputs=2:duration=first" in graph.filter_complex


def test_native_and_independent_audio_share_processing_model(tmp_path: Path) -> None:
    (tmp_path / "camera.mp4").write_bytes(b"video")
    (tmp_path / "music.wav").write_bytes(b"audio")
    document, music_id = _project(tmp_path)
    timeline = TimelineEngine(document)
    camera_id = document.find_track("V1").clips[0].id  # type: ignore[union-attr]
    timeline.set_audio_volume(camera_id, -3)
    timeline.configure_audio(
        camera_id,
        highpass_hz=100,
        denoise_strength=0.5,
        compressor=True,
        compressor_threshold_db=-20,
        compressor_ratio=3,
        limiter_db=-1,
        loudnorm_lufs=-14,
        channel_mode="stereo",
        pan=-0.25,
    )
    timeline.configure_audio(music_id, channel_mode="mono")
    graph = GraphBuilder(document, tmp_path).build().filter_complex
    assert "highpass=f=100" in graph
    assert "afftdn=nr=16.5:nf=-50" in graph
    assert "acompressor=" in graph
    assert "volume=-3dB" in graph
    assert "loudnorm=I=-14:TP=-1:LRA=11" in graph
    assert "alimiter=limit=" in graph
    assert "pan=stereo|c0=1*c0|c1=0.75*c1" in graph
    assert "channel_layouts=mono,pan=stereo|c0=c0|c1=c0" in graph
    timeline.set_audio_mute(camera_id)
    muted_graph = GraphBuilder(document, tmp_path).build().filter_complex
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=3[a0]" in muted_graph


def test_audio_crossfade_requires_overlap_and_sets_both_fades(tmp_path: Path) -> None:
    (tmp_path / "camera.mp4").write_bytes(b"video")
    (tmp_path / "music.wav").write_bytes(b"audio")
    document, first_id = _project(tmp_path)
    timeline = TimelineEngine(document)
    second = timeline.add_audio_clip(
        "music", "A1", at=2.5, source_out=1, loop=False
    )
    source, target = timeline.crossfade_audio(first_id, second.id, 0.5)
    assert source.audio.fade_out == pytest.approx(0.5)
    assert target.audio.fade_in == pytest.approx(0.5)
    graph = GraphBuilder(document, tmp_path).build().filter_complex
    assert "afade=t=out:st=2.5:d=0.5" in graph
    assert "afade=t=in:st=0:d=0.5" in graph


def test_real_ffmpeg_mix_keeps_original_and_looped_music(tmp_path: Path) -> None:
    tool = _modern_ffmpeg()
    if tool is None:
        pytest.skip("No FFmpeg with aloop/amix and H.264 encoder is available")
    ffmpeg, hardware = tool
    encoder = "libx264" if hardware == "none" else "h264_qsv"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-shortest",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(tmp_path / "camera.mp4"),
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
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(tmp_path / "music.wav"),
        ],
        check=True,
    )
    document, _ = _project(tmp_path)
    camera_id = document.find_track("V1").clips[0].id  # type: ignore[union-attr]
    TimelineEngine(document).configure_audio(
        camera_id, highpass_hz=200, loudnorm_lufs=-18, limiter_db=-3
    )
    output = tmp_path / "mixed.mp4"
    FFmpegBackend(ffmpeg).render(
        document, tmp_path, output, hardware=hardware, overwrite=False
    )
    decoded = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "2.2",
            "-t",
            "0.5",
            "-i",
            str(output),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    samples = array("f")
    samples.frombytes(decoded)
    assert _tone_strength(samples, 440) > 0.01, "camera audio was lost"
    assert _tone_strength(samples, 880) > 0.003, "looped music was not mixed"
    assert max(abs(sample) for sample in samples) < 0.9, "limiter did not cap peaks"
