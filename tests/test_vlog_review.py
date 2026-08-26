from __future__ import annotations

import pytest

from facut.core.models import Clip, MediaAsset, MediaTechnicalInfo, Track, TrackType
from facut.core.command_engine import CommandEngine
from facut.core.project_manager import ProjectManager
from facut.exceptions import InvalidArgumentError, ReviewRequiredError
from facut.vlog.review import apply_review, create_review, plan_review


def _manager(tmp_path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="review-trip")
    def seed(document):
        document.media.append(MediaAsset(
            id="media_video",
            kind="video",
            path="source.mp4",
            original_name="source.mp4",
            size=100,
            sha256="a" * 64,
            technical=MediaTechnicalInfo(
                duration=20,
                video_codec="h264",
                audio_codec="aac",
            ),
        ))
        document.tracks.append(Track(
            id="V1",
            type=TrackType.VIDEO,
            name="V1",
            clips=[
                Clip(
                    id="clip_main",
                    media_id="media_video",
                    track_id="V1",
                    source_out=5,
                )
            ],
        ))

    manager.mutate("test.seed", "Seed review timeline", seed)
    evidence = manager.project_dir / "cache" / "vlog" / "observations.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"version":"2.0","observations":[]}', encoding="utf-8")
    manager.load()
    return manager


def _submission(package: dict, *, actions: list[dict], conclusion="changes_requested") -> dict:
    return {
        "review_id": package["id"],
        "project_id": package["project_id"],
        "project_revision": package["project_revision"],
        "project_sha256": package["project_sha256"],
        "evidence_sha256": package["evidence_sha256"],
        "conclusion": conclusion,
        "findings": [
            {
                "category": "pace",
                "message": "Opening needs a tighter first cut.",
                "evidence": [{"clip_id": "clip_main"}],
                "edits": actions,
            }
        ],
    }


def test_review_approved_only_applies_one_atomic_revision(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "story")
    plan = plan_review(
        manager,
        _submission(
            package,
            actions=[
                {
                    "id": "approved",
                    "action": "clip.move",
                    "parameters": {"clip_id": "clip_main", "to": 2},
                    "status": "approved",
                    "reason": "Leave two seconds for a cold-open title.",
                },
                {
                    "id": "draft",
                    "action": "audio.volume",
                    "parameters": {"clip_id": "clip_main", "db": -3},
                    "status": "draft",
                    "reason": "Requires listening before changing gain.",
                },
            ],
        ),
    )

    result = apply_review(manager, plan, approved_only=True)

    assert result["project_revision"] == 2
    assert [item["id"] for item in result["applied"]] == ["approved"]
    clip = manager.require_document().find_clip("clip_main")
    assert clip is not None and clip.timeline_start == 2
    assert clip.audio.gain_db == 0
    assert manager.require_document().history[-1].actor == "agent"
    assert manager.undo().find_clip("clip_main").timeline_start == 0


def test_stale_review_plan_is_blocked(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "continuity")
    plan = plan_review(
        manager,
        _submission(
            package,
            actions=[
                {
                    "action": "clip.move",
                    "parameters": {"clip_id": "clip_main", "to": 1},
                    "status": "approved",
                    "reason": "Continuity adjustment.",
                }
            ],
        ),
    )
    manager.mutate("test.change", "Change project", lambda document: None)

    with pytest.raises(InvalidArgumentError, match="stale"):
        apply_review(manager, plan)
    assert manager.require_document().revision == 2


def test_review_application_rolls_back_all_actions_on_failure(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "story")
    plan = plan_review(
        manager,
        _submission(
            package,
            actions=[
                {
                    "action": "clip.move",
                    "parameters": {"clip_id": "clip_main", "to": 2},
                    "status": "approved",
                    "reason": "Valid first edit.",
                },
                {
                    "action": "clip.move",
                    "parameters": {"clip_id": "missing", "to": 4},
                    "status": "approved",
                    "reason": "Invalid second edit must roll back the first.",
                },
            ],
        ),
    )

    with pytest.raises(Exception, match="missing"):
        apply_review(manager, plan)
    assert manager.require_document().revision == 1
    assert manager.require_document().find_clip("clip_main").timeline_start == 0


def test_review_packages_stop_after_three_rounds(tmp_path) -> None:
    manager = _manager(tmp_path)
    rounds = []
    for _ in range(3):
        package = create_review(manager, "story")
        rounds.append(package["round"])
        plan_review(manager, _submission(package, actions=[], conclusion="pass"))
    assert rounds == [1, 2, 3]
    with pytest.raises(ReviewRequiredError) as error:
        create_review(manager, "story")
    assert error.value.code == "REVIEW_REQUIRED"
    assert error.value.details["max_rounds"] == 3


def test_review_create_retry_reuses_unsubmitted_package(tmp_path) -> None:
    manager = _manager(tmp_path)
    first = create_review(manager, "story")
    second = create_review(manager, "story")
    assert first["id"] == second["id"]
    assert first["round"] == second["round"] == 1
    assert first["reused"] is False
    assert second["reused"] is True


def test_review_edit_requires_known_evidence_reference(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "story")
    submission = _submission(
        package,
        actions=[{
            "action": "clip.move",
            "parameters": {"clip_id": "clip_main", "to": 1},
            "status": "approved",
            "reason": "Tighten the opening.",
        }],
    )
    submission["findings"][0]["evidence"] = [{"clip_id": "missing"}]
    with pytest.raises(InvalidArgumentError, match="unknown evidence"):
        plan_review(manager, submission)


def test_submission_is_evidence_bound(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "sound")
    submission = _submission(package, actions=[])
    submission["evidence_sha256"] = "0" * 64
    with pytest.raises(InvalidArgumentError, match="does not match"):
        plan_review(manager, submission)


def test_review_apply_is_available_to_atomic_agent_batches(tmp_path) -> None:
    manager = _manager(tmp_path)
    package = create_review(manager, "story")
    plan = plan_review(
        manager,
        _submission(
            package,
            actions=[
                {
                    "action": "clip.move",
                    "parameters": {"clip_id": "clip_main", "to": 1.5},
                    "status": "approved",
                    "reason": "Tighten the opening after review.",
                }
            ],
        ),
    )
    result = CommandEngine(manager).run_batch(
        {
            "atomic": True,
            "actor": "agent",
            "commands": [{"action": "vlog.review.apply", "plan": plan}],
        }
    )
    assert result["project_revision"] == 2
    assert manager.require_document().find_clip("clip_main").timeline_start == 1.5
