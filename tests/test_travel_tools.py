from __future__ import annotations

import json
import os
import shutil

import pytest

from facut.core.models import Clip, MediaAsset, MediaKind, MediaTechnicalInfo, Track, TrackType
from facut.core.project_manager import ProjectManager
from facut.travel import apply_reframe_plan, build_reframe_plan, parse_gpx, render_route_video


def _gpx(tmp_path):
    source = tmp_path / "route.gpx"
    source.write_text(
        """<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="31.2000" lon="121.5000"><ele>5</ele><time>2026-07-01T08:00:00Z</time></trkpt>
<trkpt lat="31.2050" lon="121.5100"><ele>15</ele><time>2026-07-01T08:05:00Z</time></trkpt>
<trkpt lat="31.2150" lon="121.5200"><ele>12</ele><time>2026-07-01T08:10:00Z</time></trkpt>
</trkseg></trk></gpx>""",
        encoding="utf-8",
    )
    return source


def test_gpx_parse_and_actual_small_video_render(tmp_path) -> None:
    route = parse_gpx(_gpx(tmp_path))
    assert route["point_count"] == 3
    assert route["distance_meters"] > 1000
    assert route["elapsed_seconds"] == 600
    output = tmp_path / "route.mp4"
    ffmpeg = os.environ.get("FACUT_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    result = render_route_video(
        route, output, ffmpeg=ffmpeg, width=320, height=240, fps=2, duration=1
    )
    assert output.stat().st_size > 0
    assert result["duration"] == 1


def test_reframe_plan_smooths_subject_points_and_applies_keyframes(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project", name="reframe")
    document = manager.require_document()
    document.media.append(
        MediaAsset(
            id="media_1",
            kind=MediaKind.VIDEO,
            path="camera.mp4",
            original_name="camera.mp4",
            size=1,
            sha256="1" * 64,
            technical=MediaTechnicalInfo(duration=10, width=3840, height=2160),
        )
    )
    document.tracks.append(
        Track(id="V1", name="V1", type=TrackType.VIDEO, clips=[Clip(id="clip_1", media_id="media_1", track_id="V1", source_out=10)])
    )
    manager.save(create_snapshot=False)
    trajectory = tmp_path / "subject.json"
    trajectory.write_text(
        json.dumps({"provider": "test-tracker", "observations": [{"time": 0, "cx": 0.2, "cy": 0.5, "confidence": 0.9}, {"time": 5, "cx": 0.8, "cy": 0.5, "confidence": 0.9}, {"time": 9, "cx": 0.5, "cy": 0.5, "confidence": 0.1}]}),
        encoding="utf-8",
    )
    plan = build_reframe_plan(manager, "clip_1", trajectory, smoothing=1)
    assert plan["accepted_observations"] == 2
    assert plan["rejected_observations"] == 1
    assert plan["keyframes"][0]["property"] == "x"
    state = apply_reframe_plan(manager, plan)
    clip = state.find_clip("clip_1")
    assert clip.transform.fit == "cover"
    assert len(clip.keyframes) == 4
    assert clip.metadata["reframe"]["target"]["width"] == 1080
