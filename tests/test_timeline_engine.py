from __future__ import annotations

from pathlib import Path

import pytest

from facut.core.models import (
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
)
from facut.core.command_engine import CommandEngine, CommandEngineError
from facut.core.project_manager import ProjectManager
from facut.core.timeline_engine import (
    TimelineConflictError,
    TimelineEngine,
    TimelineError,
    parse_time,
)


def project() -> ProjectDocument:
    return ProjectDocument(
        project=ProjectSettings(name="timeline-test", fps=30),
        media=[
            MediaAsset(
                id="media_01",
                kind=MediaKind.VIDEO,
                path="input.mp4",
                original_name="input.mp4",
                size=1,
                sha256="a" * 64,
                technical=MediaTechnicalInfo(duration=30, audio_codec="aac"),
            )
        ],
    )


@pytest.mark.parametrize(
    ("value", "expected_seconds", "expected_frame"),
    [
        (12.5, 12.5, 375),
        ("12.5s", 12.5, 375),
        ("1500ms", 1.5, 45),
        ("00:00:12.500", 12.5, 375),
        ("450f", 15.0, 450),
    ],
)
def test_time_formats(value: object, expected_seconds: float, expected_frame: int) -> None:
    parsed = parse_time(value, 30)
    assert float(parsed.seconds) == expected_seconds
    assert parsed.frame == expected_frame
    assert parsed.as_dict()["timecode"].count(":") == 2


def test_split_trim_move_and_ripple_delete() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    clip = engine.add_clip("media_01", "V1", at=0, source_in=2, source_out=12)
    left, right = engine.split_clip(clip.id, "4s")
    assert left.source_in == 2
    assert left.source_out == 6
    assert right.timeline_start == 4
    assert right.source_in == 6
    engine.move_clip(right.id, to=8)
    engine.trim_clip(right.id, start_delta="1s", end_delta="-1s")
    assert right.timeline_start == 9
    assert right.source_in == 7
    assert right.source_out == 11
    third = engine.duplicate_clip(right.id, to=15)
    engine.delete_clip(right.id, ripple=True)
    assert third.timeline_start == pytest.approx(11)


def test_overlap_requires_matching_transition() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    first = engine.add_clip("media_01", "V1", at=0, source_in=0, source_out=6)
    second = engine.add_clip("media_01", "V1", at=5.5, source_in=8, source_out=14)
    with pytest.raises(TimelineConflictError):
        engine.validate()
    transition = engine.add_transition(
        "dissolve",
        "0.5s",
        from_clip_id=first.id,
        to_clip_id=second.id,
    )
    engine.validate()
    assert transition.track_id == "V1"
    assert document.project.duration == pytest.approx(11.5)


def test_frame_accurate_split() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    clip = engine.add_clip("media_01", "V1", source_in=0, source_out=20)
    left, right = engine.split_clip(clip.id, "375f")
    assert left.duration == pytest.approx(12.5)
    assert right.timeline_start == pytest.approx(12.5)


def test_append_clamp_and_subframe_snap() -> None:
    document = project()
    document.media[0].technical.duration = 4.038
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    first = engine.add_clip("media_01", "V1", source_out=4.040)
    second = engine.add_clip(
        "media_01", "V1", source_out=1, append=True
    )
    assert first.source_out == pytest.approx(4.038)
    assert second.timeline_start == pytest.approx(first.end)
    assert first.metadata["timeline_warnings"]

    second.timeline_start = first.end - 0.001
    changes = engine.snap_subframe_boundaries("V1")
    assert changes
    assert second.timeline_start == pytest.approx(first.end)
    engine.validate()


def test_transform_keyframes_and_ripple_freeze() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    first = engine.add_clip("media_01", "V1", source_in=0, source_out=5)
    following = engine.add_clip("media_01", "V1", at=5, source_in=5, source_out=10)
    transformed = engine.transform_clip(
        first.id,
        x=120,
        scale=0.5,
        opacity=0.8,
        autorotate=False,
        stabilize=True,
        keyframes=[
            {"property": "x", "time": 0, "value": 0},
            {"property": "x", "time": 5, "value": 120},
        ],
    )
    assert transformed.transform.scale_x == 0.5
    assert transformed.transform.autorotate is False
    assert transformed.keyframes[-1].value == 120
    hold = engine.freeze_clip(first.id, at="4.5s", duration="2s")
    assert hold.freeze_frame == pytest.approx(4.5)
    assert hold.duration == 2
    assert hold.audio.muted is True
    assert following.timeline_start == 7


def test_crop_shorthand_motion_preset_and_easing() -> None:
    document = project()
    document.media[0].technical.width = 3840
    document.media[0].technical.height = 2160
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    clip = engine.add_clip("media_01", "V1", source_out=5)
    engine.transform_clip(clip.id, crop="120:80:3600:2000")
    assert clip.transform.crop_left == 120
    assert clip.transform.crop_right == 120
    assert clip.transform.crop_bottom == 80
    moved = engine.apply_motion_preset(
        clip.id, preset="slow-push", intensity=0.4
    )
    assert moved.metadata["motion_preset"]["name"] == "slow-push"
    assert {item.easing for item in moved.keyframes} == {"ease-in-out"}
    assert max(float(item.value) for item in moved.keyframes) > 1


def test_constant_reverse_and_piecewise_speed_curve_are_real_timeline_edits() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    reverse = engine.add_clip("media_01", "V1", source_out=4)
    engine.set_speed(reverse.id, rate=-2)
    assert reverse.speed == -2
    assert reverse.duration == pytest.approx(2)

    curve_clip = engine.add_clip("media_01", "V1", at=2, source_in=4, source_out=8)
    segments = engine.apply_speed_curve(
        curve_clip.id,
        curve={
            "version": "1.0",
            "mode": "step",
            "points": [{"at": 0, "rate": 1}, {"at": 2, "rate": 2}, {"at": 4, "rate": 2}],
        },
    )
    assert len(segments) == 2
    assert [item.speed for item in segments] == [1, 2]
    assert segments[0].id == curve_clip.id
    assert segments[1].timeline_start == pytest.approx(segments[0].end)
    assert segments[-1].end == pytest.approx(5)
    assert all("speed_curve" in item.metadata for item in segments)
    engine.validate()


def test_speed_curve_rejects_direction_changes() -> None:
    document = project()
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    clip = engine.add_clip("media_01", "V1", source_out=4)
    with pytest.raises(TimelineError, match="--reverse"):
        engine.apply_speed_curve(
            clip.id,
            curve={"points": [{"at": 0, "rate": 1}, {"at": 4, "rate": -1}]},
        )


def test_command_engine_exposes_audio_processing_and_master_loudness(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "audio-agent-project")
    manager.document.media.extend(project().media)
    timeline = TimelineEngine(manager.document)
    timeline.add_track("video", "V1")
    clip = timeline.add_clip("media_01", "V1", source_out=4)
    manager.save()
    engine = CommandEngine(manager)
    processed = engine.execute(
        "audio.process",
        {
            "clip_id": clip.id,
            "highpass_hz": 90,
            "compressor_preset": "vlog",
            "limiter_db": -1,
        },
    )
    assert processed["data"]["audio"]["compressor"]["ratio"] == 4
    configured = engine.execute(
        "audio.loudness",
        {"target": -14, "true_peak": -1, "loudness_range": 11, "two_pass": True},
    )
    assert configured["data"]["target_lufs"] == -14
    assert manager.require_document().settings["audio"]["master_loudness"]["two_pass"] is True


def test_atomic_batch_and_dry_run(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "batch-project")
    manager.document.media.extend(project().media)
    manager.save()
    engine = CommandEngine(manager)
    result = engine.run_batch(
        {
            "atomic": True,
            "commands": [
                {"action": "timeline.track.add", "type": "video", "name": "V1"},
                {
                    "action": "timeline.add",
                    "media_id": "media_01",
                    "track": "V1",
                    "at": 0,
                    "in": 2,
                    "out": 8,
                },
            ],
        },
        dry_run=True,
    )
    assert result["project_revision"] == 1
    assert manager.require_document().revision == 0
    assert manager.require_document().tracks == []
    with pytest.raises(CommandEngineError):
        engine.run_batch(
            {
                "atomic": True,
                "commands": [
                    {"action": "timeline.track.add", "type": "video", "name": "V1"},
                    {"action": "not.a.real.action"},
                ],
            }
        )
    assert manager.require_document().tracks == []
