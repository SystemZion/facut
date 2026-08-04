from __future__ import annotations

import wave

import pytest

from facut.core.command_engine import CommandEngine
from facut.core.models import (
    Clip,
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    Track,
    TrackType,
)
from facut.core.project_manager import ProjectManager
from facut.intelligence.narration import generate_narration_plan
from facut.intelligence.narration_plan import (
    NarrationCandidate,
    NarrationLine,
    NarrationLineStatus,
    NarrationPlan,
    NarrationPlanError,
    NarrationPreview,
    NarrationProviderNotConfigured,
    NarrationTimeRange,
    load_narration_plan,
    save_narration_plan,
)


def _video_asset() -> MediaAsset:
    return MediaAsset(
        id="media_video",
        kind=MediaKind.VIDEO,
        path="video.mp4",
        original_name="video.mp4",
        size=1,
        sha256="1" * 64,
        technical=MediaTechnicalInfo(duration=10, video_codec="h264"),
    )


def _wav(path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * int(16000 * seconds))


def _line(line_id: str, preview, *, status=NarrationLineStatus.APPROVED) -> NarrationLine:
    return NarrationLine(
        id=line_id,
        clip_id="clip_video",
        media_id="media_video",
        timeline_range=NarrationTimeRange(start=1, end=3),
        visual_summary="一家人在海边看日落",
        suggestion="说说当时的感受",
        fact_confidence=0.9,
        evidence={"provider": "test-vision", "items": [{"frame": 30}]},
        candidates=[NarrationCandidate(id=f"{line_id}_c1", text="夕阳落下来的时候，我们都安静了。")],
        selected_candidate_id=f"{line_id}_c1",
        previews=[NarrationPreview(id=f"{line_id}_p1", path=str(preview))],
        selected_preview_id=f"{line_id}_p1",
        status=status,
    )


def test_generate_plan_is_strict_and_never_fakes_provider(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    document = manager.require_document()
    document.media.append(_video_asset())
    document.tracks.append(
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[Clip(id="clip_video", media_id="media_video", track_id="V1", source_out=10)],
        )
    )
    plan = generate_narration_plan(
        document,
        {
            "observations": [
                {
                    "id": "obs_1",
                    "media_id": "media_video",
                    "start": 1,
                    "end": 4,
                    "caption": "一家人在海边看日落",
                    "confidence": 0.91,
                    "provider": "test-vision",
                }
            ]
        },
        style="weekend-vlog",
    )
    assert plan.provider == "deterministic"
    assert plan.style == "weekend-vlog"
    assert plan.lines[0].status == NarrationLineStatus.DRAFT
    assert plan.lines[0].candidates[0].source == "deterministic"
    assert plan.lines[0].draft_text == plan.lines[0].selected_text
    with pytest.raises(NarrationProviderNotConfigured) as error:
        generate_narration_plan(document, {"observations": []}, provider="imaginary-llm")
    assert error.value.code == "PROVIDER_NOT_CONFIGURED"
    assert error.value.details["fallback_used"] is False


def test_plan_round_trip_and_atomic_approved_only_apply(monkeypatch, tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    audio = tmp_path / "approved.wav"
    skipped = tmp_path / "draft.wav"
    _wav(audio)
    _wav(skipped)
    monkeypatch.setattr(
        "facut.intelligence.narration_plan.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(duration=1, audio_codec="pcm_s16le", sample_rate=16000, audio_channels=1),
    )
    plan = NarrationPlan(
        project_id=manager.require_document().project.id,
        project_revision=0,
        lines=[
            _line("approved", audio),
            _line("draft", skipped, status=NarrationLineStatus.DRAFT),
        ],
    )
    path = save_narration_plan(plan, tmp_path / "narration.plan.json")
    assert load_narration_plan(path) == plan

    result = CommandEngine(manager).execute(
        "narration.apply",
        {
            "plan_path": str(path),
            "approved_only": True,
            "duck_music": True,
            "preserve_original": True,
        },
    )
    assert result["project_revision"] == 1
    state = manager.require_document()
    track = state.find_track("A_NARRATION")
    assert track is not None and track.metadata["role"] == "narration"
    assert [clip.metadata["narration_line_id"] for clip in track.clips] == ["approved"]
    assert state.settings["narration_applications"][0]["preserve_original"] is True
    assert "audio_ducking" not in state.settings
    assert "no audio track" in result["data"]["warnings"][0].lower()

    restored = manager.undo()
    assert restored.revision == 0
    assert restored.find_track("A_NARRATION") is None
    assert not restored.media


def test_missing_preview_fails_before_mutation(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    plan = NarrationPlan(
        project_id=manager.require_document().project.id,
        project_revision=0,
        lines=[_line("missing", tmp_path / "missing.wav")],
    )
    path = save_narration_plan(plan, tmp_path / "plan.json")
    with pytest.raises(NarrationPlanError):
        CommandEngine(manager).execute("narration.apply", {"plan_path": str(path)})
    assert manager.require_document().revision == 0
    assert manager.require_document().tracks == []


def test_stale_plan_is_blocked_before_mutation(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    plan = NarrationPlan(
        project_id=manager.require_document().project.id,
        project_revision=0,
        lines=[_line("stale", tmp_path / "never-read.wav")],
    )
    path = save_narration_plan(plan, tmp_path / "stale.json")
    CommandEngine(manager).execute(
        "timeline.track.add", {"type": "video", "name": "V1"}
    )
    with pytest.raises(NarrationPlanError, match="targets revision 0"):
        CommandEngine(manager).execute("narration.apply", {"plan_path": str(path)})
    assert manager.require_document().revision == 1
