from __future__ import annotations

import json
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
    SubtitleCue,
    TextOverlay,
    TextStyle,
)
from facut.core.timeline_engine import TimelineEngine
from facut.render.cache import cache_key
from facut.render.ffmpeg_backend import FFmpegBackend
from facut.render.graph_builder import GraphBuilder
from facut.render.hardware import choose_h264_encoder
from facut.exceptions import NotImplementedFacutError


def _ffmpeg_with_xfade() -> tuple[str, str, str] | None:
    candidates = [
        os.environ.get("FACUT_TEST_FFMPEG"),
        shutil.which("ffmpeg"),
        r"D:\工具\jianyin\JianyingPro\9.3.0.13547\ffmpeg.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        result = subprocess.run(
            [candidate, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if " xfade " not in result.stdout or " subtitles " not in result.stdout:
            continue
        encoders_result = subprocess.run(
            [candidate, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if " libx264 " in encoders_result.stdout:
            encoder = "libx264"
        elif " h264_qsv " in encoders_result.stdout:
            encoder = "h264_qsv"
        else:
            continue
        adjacent = Path(candidate).with_name("ffprobe.exe")
        probe = str(adjacent) if adjacent.is_file() else shutil.which("ffprobe")
        if probe:
            return str(candidate), probe, encoder
    return None


def _document(root: Path) -> ProjectDocument:
    document = ProjectDocument(
        project=ProjectSettings(name="render-test", width=320, height=180, fps=30),
        media=[
            MediaAsset(
                id="red",
                kind=MediaKind.VIDEO,
                path="red.mp4",
                original_name="red.mp4",
                size=(root / "red.mp4").stat().st_size,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(duration=2, audio_codec="aac"),
            ),
            MediaAsset(
                id="blue",
                kind=MediaKind.VIDEO,
                path="blue.mp4",
                original_name="blue.mp4",
                size=(root / "blue.mp4").stat().st_size,
                sha256="2" * 64,
                technical=MediaTechnicalInfo(duration=2, audio_codec="aac"),
            ),
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    red = timeline.add_clip("red", "V1", at=0, source_in=0, source_out=2)
    blue = timeline.add_clip("blue", "V1", at=1.5, source_in=0, source_out=2)
    timeline.add_transition(
        "dissolve", 0.5, from_clip_id=red.id, to_clip_id=blue.id
    )
    return document


def test_cache_key_changes_with_source_state(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"one")
    first = cache_key(
        inputs=[source],
        parameters={"height": 540},
        software_version="0.1",
        ffmpeg_version="7",
    )
    second = cache_key(
        inputs=[source],
        parameters={"height": 720},
        software_version="0.1",
        ffmpeg_version="7",
    )
    assert len(first) == 64
    assert first != second


def test_hardware_unavailable_falls_back() -> None:
    choice = choose_h264_encoder(
        "unused", "nvenc", encoders={"libx264"}
    )
    assert choice.encoder == "libx264"
    assert choice.warnings


def test_graph_contains_real_xfade(tmp_path: Path) -> None:
    for name in ("red.mp4", "blue.mp4"):
        (tmp_path / name).write_bytes(b"placeholder")
    graph = GraphBuilder(_document(tmp_path), tmp_path).build()
    assert "xfade=transition=fade:duration=0.5:offset=1.5" in graph.filter_complex
    assert "acrossfade=d=0.5" in graph.filter_complex
    assert graph.filter_complex.endswith("format=yuv420p[vdelivery]")
    assert graph.video_label == "vdelivery"
    assert graph.duration == pytest.approx(3.5)


def test_keyframed_position_is_evaluated_by_overlay_not_pad(tmp_path: Path) -> None:
    for name in ("red.mp4", "blue.mp4"):
        (tmp_path / name).write_bytes(b"placeholder")
    document = _document(tmp_path)
    clip = document.tracks[0].clips[0]
    TimelineEngine(document).transform_clip(
        clip.id,
        fit="cover",
        keyframes=[
            {"property": "x", "time": 0, "value": -40},
            {"property": "x", "time": 1, "value": 40},
        ],
    )
    graph = GraphBuilder(document, tmp_path).build()
    first_chain = graph.filter_complex.split("[v0]", 1)[0]
    assert "pad=max" not in first_chain
    assert "overlay=x=(W-w)/2+(if(lt(t" in first_chain
    assert "eval=frame" in first_chain


def test_bt709_delivery_rejects_hdr_relabel(tmp_path: Path) -> None:
    (tmp_path / "hdr.mp4").write_bytes(b"placeholder")
    document = ProjectDocument(
        project=ProjectSettings(name="hdr", width=320, height=180, fps=30),
        media=[
            MediaAsset(
                id="hdr",
                kind=MediaKind.VIDEO,
                path="hdr.mp4",
                original_name="hdr.mp4",
                size=11,
                sha256="a" * 64,
                technical=MediaTechnicalInfo(
                    duration=2,
                    color_space="bt2020nc",
                    color_transfer="smpte2084",
                    color_primaries="bt2020",
                    dynamic_range="hdr-pq",
                ),
            )
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    timeline.add_clip("hdr", "V1", at=0, source_in=0, source_out=2)
    backend = FFmpegBackend.__new__(FFmpegBackend)
    backend.ffmpeg = "unused"
    with pytest.raises(NotImplementedFacutError, match="will not silently relabel|requires a real"):
        backend.render(
            document,
            tmp_path,
            tmp_path / "output.mp4",
            color_space="bt709",
        )


def test_render_and_range_preview_are_playable(tmp_path: Path) -> None:
    tools = _ffmpeg_with_xfade()
    if tools is None:
        pytest.skip("No modern FFmpeg with xfade is available")
    ffmpeg, ffprobe, encoder = tools
    for color, frequency in (("red", "440"), ("blue", "660")):
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
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=2",
                "-shortest",
                "-c:v",
                encoder,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(tmp_path / f"{color}.mp4"),
            ],
            check=True,
        )
    document = _document(tmp_path)
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V2")
    overlay = timeline.add_clip(
        "blue", "V2", at=0.2, source_in=0, source_out=0.8
    )
    timeline.transform_clip(
        overlay.id,
        x=100,
        y=50,
        scale=0.25,
        opacity=0.7,
        autorotate=False,
    )
    TimelineEngine(document).add_track("subtitle", "S1")
    document.subtitle_cues.append(
        SubtitleCue(
            track_id="S1",
            start=1.1,
            end=1.4,
            text="CAPTION",
            style=TextStyle(font_size=32, color="#FFFFFF", stroke_width=2),
        )
    )
    document.text_overlays.append(
        TextOverlay(
            text="FACUT",
            at=0.2,
            duration=0.8,
            x="center",
            y="center",
            style=TextStyle(font_size=52, color="#FFFFFF", stroke_width=2),
        )
    )
    backend = FFmpegBackend(ffmpeg)
    progress_events: list[dict[str, object]] = []
    final = backend.render(
        document,
        tmp_path,
        tmp_path / "final.mp4",
        hardware="none" if encoder == "libx264" else "qsv",
        progress=progress_events.append,
        loudness_target=-14,
        true_peak=-1,
        loudness_range=11,
    )
    preview = backend.preview_range(
        document,
        tmp_path,
        tmp_path / "preview.mp4",
        start=1,
        end=2.5,
        height=180,
        hardware="none" if encoder == "libx264" else "qsv",
    )
    assert final.output.is_file()
    assert preview.output.is_file()
    assert progress_events
    assert progress_events[-1]["progress"] == pytest.approx(1.0)
    assert any(event["stage"] == "loudness_scan" for event in progress_events)
    assert "eta_seconds" in progress_events[-1]
    assert list((tmp_path / "logs").glob("loudness-pass1-*.log"))
    assert list((tmp_path / "logs").glob("render-*.log"))
    for path, expected in ((final.output, 3.5), (preview.output, 1.5)):
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        assert duration == pytest.approx(expected, abs=0.06)
    title_frame = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(final.output),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    bright_pixels = sum(
        1
        for index in range(0, len(title_frame), 3)
        if all(channel > 180 for channel in title_frame[index : index + 3])
    )
    assert bright_pixels > 20, "text overlay was not burned into the output"
    subtitle_frame = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1.25",
            "-i",
            str(final.output),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    subtitle_bright_pixels = sum(
        1
        for index in range(0, len(subtitle_frame), 3)
        if all(channel > 180 for channel in subtitle_frame[index : index + 3])
    )
    assert subtitle_bright_pixels > 20, "subtitle cue was not burned into the output"
    midpoint = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1.75",
            "-i",
            str(final.output),
            "-vf",
            "scale=1:1",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert len(midpoint) == 3
    red, _, blue = midpoint
    assert red > 20 and blue > 20, "mid-transition frame must blend both clips"
