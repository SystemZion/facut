from __future__ import annotations

import json

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.recipe import RecipeDocument, RecipeEngine
from facut.vlog import (
    build_story_candidates,
    compare_story_candidates,
    director_status,
    ingest_observations,
    next_inspection_task,
    prepare_evidence_manifest,
    refine_story_candidate,
)


def _manager(tmp_path, count: int = 3) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="trip", width=3840, height=2160)
    for index in range(count):
        source = tmp_path / f"clip-{index}.mp4"
        source.write_bytes(f"video-{index}".encode())
        manager.document.media.append(
            MediaAsset(
                id=f"media_{index}",
                kind=MediaKind.VIDEO,
                path=manager.store_path(source),
                original_name=source.name,
                size=source.stat().st_size,
                sha256=str(index + 1) * 64,
                technical=MediaTechnicalInfo(duration=12, width=3840, height=2160, frame_rate=30),
            )
        )
    manager.save(create_snapshot=False)
    return manager


def test_prepare_creates_complete_resumable_inspection_contract(tmp_path) -> None:
    manager = _manager(tmp_path)
    result = prepare_evidence_manifest(manager, generate_frames=False, batch_size=2)
    assert result["asset_count"] == 3
    assert result["pending_tasks"] == 2
    task = next_inspection_task(manager.project_dir)
    assert task["task_id"] == "inspect_0001"
    assert task["minimum_observations"] == 2
    assert task["required_output_schema"]["additionalProperties"] is False
    assert director_status(manager.project_dir)["stage"] == "inspection"


def test_observations_are_strict_idempotent_and_complete_tasks(tmp_path) -> None:
    manager = _manager(tmp_path, count=2)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=2)
    payload = {
        "observations": [
            {
                "observation_id": f"obs_{index}",
                "media_id": f"media_{index}",
                "range": {"start": 1, "end": 8},
                "summary": summary,
                "tags": tags,
                "quality": 0.9 - index * 0.1,
                "confidence": 0.95,
                "original_audio_value": "high" if index == 0 else "medium",
                "evidence_frames": [30, 120, 240],
            }
            for index, (summary, tags) in enumerate(
                [("抵达城市后的开心反应", ["arrival", "reaction"]), ("日落时的一家人", ["sunset", "family"])]
            )
        ]
    }
    first = ingest_observations(
        manager.require_document(), manager.project_dir, payload, task_id="inspect_0001"
    )
    assert first["remaining_tasks"] == 0
    second = ingest_observations(manager.require_document(), manager.project_dir, payload)
    assert second["unchanged"] == 2
    assert second["observation_count"] == 2


def test_storygraph_builds_three_reviewable_candidates_and_comparison(tmp_path) -> None:
    manager = _manager(tmp_path, count=3)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=3)
    payload = {
        "observations": [
            {
                "observation_id": "opening",
                "media_id": "media_0",
                "range": {"start": 0, "end": 6},
                "summary": "抵达城市时全家人的惊喜反应",
                "tags": ["arrival", "reaction"],
                "quality": 0.8,
                "confidence": 0.95,
                "original_audio_value": "high",
            },
            {
                "observation_id": "food",
                "media_id": "media_1",
                "range": {"start": 2, "end": 10},
                "summary": "街边美食细节和现场声音",
                "tags": ["food", "detail"],
                "quality": 0.92,
                "confidence": 0.9,
                "original_audio_value": "high",
            },
            {
                "observation_id": "ending",
                "media_id": "media_2",
                "range": {"start": 3, "end": 11},
                "summary": "日落后的全家福和告别",
                "tags": ["sunset", "family", "farewell"],
                "quality": 0.95,
                "confidence": 0.96,
                "original_audio_value": "medium",
            },
        ]
    }
    ingest_observations(manager.require_document(), manager.project_dir, payload, task_id="inspect_0001")
    plan = build_story_candidates(
        manager.require_document(), manager.project_dir, style="family-trip", target_duration=30
    )
    assert [item.strategy for item in plan.candidates] == ["narrative", "immersive", "visual"]
    assert all(item.segments for item in plan.candidates)
    comparison = compare_story_candidates(manager.project_dir)
    assert len(comparison["differences"]) == 3
    assert all("transition_intents" in item.polish_plan for item in plan.candidates)
    assert all(item.polish_plan["music_query"]["license_required"] for item in plan.candidates)
    refined = refine_story_candidate(manager.project_dir, "candidate-narrative")
    assert refined.selected_candidate_id == "candidate-narrative"
    assert refined.status == "ready"


def test_story_planning_refuses_incomplete_visual_coverage(tmp_path) -> None:
    manager = _manager(tmp_path, count=2)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=2)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": "only-one",
                    "media_id": "media_0",
                    "range": {"start": 0, "end": 4},
                    "summary": "只检查了一条素材",
                }
            ]
        },
    )
    try:
        build_story_candidates(manager.require_document(), manager.project_dir)
    except ValueError as error:
        assert "coverage is incomplete" in str(error)
    else:
        raise AssertionError("Quality-first planning must not skip unobserved media.")


def test_story_planning_rejects_unknown_style_pack(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=1)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": "one",
                    "media_id": "media_0",
                    "range": {"start": 0, "end": 4},
                    "summary": "抵达",
                }
            ]
        },
    )
    try:
        build_story_candidates(
            manager.require_document(), manager.project_dir, style="imaginary-style"
        )
    except ValueError as error:
        assert "Unknown VLOG style" in str(error)
    else:
        raise AssertionError("Unknown style packs must not be silently accepted.")


def test_ready_candidate_can_be_applied_atomically_from_recipe(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    manager.mutate("test.import", "Commit imported test media", lambda document: None)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=1)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": "arrival",
                    "media_id": "media_0",
                    "range": {"start": 1, "end": 7},
                    "summary": "抵达后的反应",
                    "confidence": 0.95,
                }
            ]
        },
    )
    build_story_candidates(manager.require_document(), manager.project_dir)
    refine_story_candidate(manager.project_dir, "candidate-narrative")
    recipe = RecipeDocument.model_validate(
        {"version": "1.0", "vlog": {"candidate_id": "candidate-narrative"}}
    )
    result = RecipeEngine(manager).build(recipe)
    assert result["edit"]["project_revision"] == 2
    assert manager.require_document().settings["vlog_director"]["candidate_id"] == "candidate-narrative"
    assert manager.require_document().tracks[0].clips
