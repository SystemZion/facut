from __future__ import annotations

import pytest

from facut.core.models import Clip, MediaAsset, MediaTechnicalInfo, Track, TrackType
from facut.core.command_engine import CommandEngine
from facut.core.project_manager import ProjectManager
from facut.exceptions import InvalidArgumentError, ReviewRequiredError
from facut.vlog.soundscape import (
    analyze_soundscape,
    apply_soundscape_plan,
    plan_soundscape,
)


def _manager(tmp_path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="sound-trip")
    def seed(document):
        for media_id in ("voice", "music"):
            document.media.append(MediaAsset(
                id=media_id,
                kind="audio",
                path=f"{media_id}.wav",
                original_name=f"{media_id}.wav",
                size=100,
                sha256=("a" if media_id == "voice" else "b") * 64,
                technical=MediaTechnicalInfo(duration=10, audio_codec="pcm_s16le"),
            ))
        document.tracks.extend([
            Track(
                id="A_DIALOGUE",
                type=TrackType.AUDIO,
                name="Dialogue",
                metadata={"role": "dialogue"},
                clips=[
                    Clip(
                        id="dialogue_1",
                        media_id="voice",
                        track_id="A_DIALOGUE",
                        timeline_start=2,
                        source_out=4,
                    )
                ],
            ),
            Track(
                id="A_MUSIC",
                type=TrackType.AUDIO,
                name="Music",
                metadata={"role": "music"},
                clips=[
                    Clip(
                        id="music_1",
                        media_id="music",
                        track_id="A_MUSIC",
                        source_out=10,
                    )
                ],
            ),
        ])

    manager.mutate("test.seed", "Seed soundscape timeline", seed)
    return manager


def test_soundscape_analysis_is_metadata_only_and_role_aware(tmp_path) -> None:
    result = analyze_soundscape(_manager(tmp_path))

    assert result["signal_analysis"] == "not_run"
    assert result["roles"]["dialogue"][0]["clip_id"] == "dialogue_1"
    assert result["roles"]["music"][0]["clip_id"] == "music_1"
    assert result["overlaps"] == [
        {
            "kind": "speech_music",
            "speech_clip_id": "dialogue_1",
            "speech_track_id": "A_DIALOGUE",
            "music_clip_id": "music_1",
            "music_track_id": "A_MUSIC",
            "start": 2.0,
            "end": 6.0,
            "measurement_required": True,
        }
    ]
    assert "not run" in result["warnings"][0].lower()


def test_soundscape_plan_is_draft_and_does_not_mutate(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = plan_soundscape(manager)

    assert plan["execution_status"] == "not_applied"
    assert {item["kind"] for item in plan["operations"]} == {
        "duck_music",
        "master_loudness",
    }
    assert all(item["status"] == "draft" for item in plan["operations"])
    assert manager.require_document().revision == 1
    assert "audio_ducking" not in manager.require_document().settings


def test_approved_soundscape_settings_apply_atomically(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = plan_soundscape(manager)
    for operation in plan["operations"]:
        operation["status"] = "approved"

    result = apply_soundscape_plan(manager, plan)

    assert result["project_revision"] == 2
    assert result["execution_status"] == "settings_applied_render_required"
    assert result["measured_output"] is False
    state = manager.require_document()
    assert state.settings["audio_ducking"][0]["reduction_db"] == -8
    assert state.settings["audio"]["master_loudness"]["target_lufs"] == -14
    assert manager.undo().settings.get("audio_ducking") is None


def test_draft_soundscape_plan_requires_review(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = plan_soundscape(manager)
    with pytest.raises(ReviewRequiredError):
        apply_soundscape_plan(manager, plan)
    assert manager.require_document().revision == 1


def test_stale_soundscape_plan_is_blocked(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = plan_soundscape(manager)
    plan["operations"][0]["status"] = "approved"
    manager.mutate("test.change", "Change project", lambda document: None)

    with pytest.raises(InvalidArgumentError, match="stale"):
        apply_soundscape_plan(manager, plan)
    assert manager.require_document().revision == 2


def test_soundscape_apply_is_available_to_atomic_agent_batches(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = plan_soundscape(manager)
    for operation in plan["operations"]:
        operation["status"] = "approved"
    result = CommandEngine(manager).run_batch(
        {
            "atomic": True,
            "actor": "agent",
            "commands": [{"action": "vlog.soundscape.apply", "plan": plan}],
        }
    )
    assert result["project_revision"] == 2
    assert manager.require_document().settings["audio_ducking"]
