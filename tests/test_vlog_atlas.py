from __future__ import annotations

import json

import pytest

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.vlog.atlas import (
    _annotate_near_duplicates,
    build_scene_atlas,
    ingest_atlas_observations,
    next_atlas_inspection_batch,
    scene_atlas_status,
)


def _manager(tmp_path, count: int = 13) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="atlas")
    for index in range(count):
        source = tmp_path / f"clip-{index:03d}.mp4"
        source.write_bytes(f"video-{index}".encode())
        manager.document.media.append(
            MediaAsset(
                id=f"media_{index:03d}",
                kind=MediaKind.VIDEO,
                path=manager.store_path(source),
                original_name=source.name,
                size=source.stat().st_size,
                sha256=hashlib_sha256(source.read_bytes()),
                technical=MediaTechnicalInfo(duration=12, width=3840, height=2160),
            )
        )
    manager.save(create_snapshot=False)
    return manager


def hashlib_sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _observations(task: dict, *, confidence: float = 0.9, actions=None):
    return [
        {
            "observation_id": f"obs_{media_id}",
            "media_id": media_id,
            "range": {"start": 0.5, "end": 5.0},
            "summary": "旅行素材基础观察",
            "confidence": confidence,
            "actions": actions or [],
        }
        for media_id in task["media_ids"]
    ]


def test_atlas_defaults_to_twelve_and_task_ids_are_stable(tmp_path) -> None:
    manager = _manager(tmp_path)
    built = build_scene_atlas(manager)
    assert built["asset_count"] == 13
    assert built["baseline_pending"] == 2
    first = next_atlas_inspection_batch(manager.project_dir)
    assert len(first["assets"]) == 12
    assert first["task_id"].startswith("atlas_baseline_")
    assert first["visual_provider"] == "external-agent"
    assert first["assets"][0]["source_ref"].endswith("clip-000.mp4")
    assert first["assets"][0]["baseline"]["sample_times"] == [0.5, 6.0, 11.5]
    original_id = first["task_id"]
    build_scene_atlas(manager)
    assert next_atlas_inspection_batch(manager.project_dir)["task_id"] == original_id


def test_resume_uses_content_hash_and_idempotent_observations(tmp_path) -> None:
    manager = _manager(tmp_path, count=2)
    build_scene_atlas(manager, batch_size=2)
    task = next_atlas_inspection_batch(manager.project_dir)
    payload = {
        "task_id": task["task_id"],
        "asset_hashes": task["asset_hashes"],
        "observations": _observations(task),
    }
    first = ingest_atlas_observations(manager.require_document(), manager.project_dir, payload)
    assert first["inserted"] == 2
    assert first["task_status"] == "complete"
    retry = ingest_atlas_observations(manager.require_document(), manager.project_dir, payload)
    assert retry["unchanged"] == 2
    # Rebuilding keeps the source-addressed completion instead of scheduling it again.
    resumed = build_scene_atlas(manager, batch_size=2)
    assert resumed["baseline_pending"] == 0
    stored = json.loads(
        (manager.project_dir / "cache" / "vlog" / "atlas" / "observations.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(record["content_sha256"] for record in stored["observations"])
    canonical = json.loads(
        (manager.project_dir / "cache" / "vlog" / "observations.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["media_id"] for item in canonical["observations"]} == {
        "media_000",
        "media_001",
    }


def test_low_confidence_or_important_action_upgrades_to_deep_review(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    build_scene_atlas(manager)
    task = next_atlas_inspection_batch(manager.project_dir)
    result = ingest_atlas_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "task_id": task["task_id"],
            "observations": _observations(task, confidence=0.6, actions=["摔倒"]),
        },
    )
    assert result["deep_pending"] == 1
    deep = next_atlas_inspection_batch(manager.project_dir)
    assert deep["kind"] == "deep"
    assert deep["assets"][0]["deep_review"]["sample_count"] == 12
    assert "low-confidence-meaning" in deep["assets"][0]["deep_review"]["reasons"]
    assert "important-action-or-reaction" in deep["assets"][0]["deep_review"]["reasons"]


def test_partial_batch_keeps_task_id_and_resumes_missing_assets(tmp_path) -> None:
    manager = _manager(tmp_path, count=3)
    build_scene_atlas(manager, batch_size=3)
    task = next_atlas_inspection_batch(manager.project_dir)
    partial = _observations(task)[:1]
    result = ingest_atlas_observations(
        manager.require_document(),
        manager.project_dir,
        {"task_id": task["task_id"], "observations": partial},
    )
    assert result["task_status"] == "pending"
    resumed = next_atlas_inspection_batch(manager.project_dir)
    assert resumed["task_id"] == task["task_id"]
    assert resumed["status"] == "pending"
    completed = ingest_atlas_observations(
        manager.require_document(),
        manager.project_dir,
        {"task_id": task["task_id"], "observations": _observations(task)[1:]},
    )
    assert completed["task_status"] == "complete"
    assert scene_atlas_status(manager.project_dir)["baseline_pending"] == 0


def test_new_asset_does_not_renumber_existing_batch(tmp_path) -> None:
    manager = _manager(tmp_path, count=2)
    build_scene_atlas(manager, batch_size=2)
    original_id = next_atlas_inspection_batch(manager.project_dir)["task_id"]
    source = tmp_path / "added.mp4"
    source.write_bytes(b"added-video")
    manager.document.media.append(
        MediaAsset(
            id="media_added",
            kind=MediaKind.VIDEO,
            path=manager.store_path(source),
            original_name=source.name,
            size=source.stat().st_size,
            sha256=hashlib_sha256(source.read_bytes()),
            technical=MediaTechnicalInfo(duration=6),
        )
    )
    manager.save(create_snapshot=False)
    build_scene_atlas(manager, batch_size=2)
    atlas = json.loads(
        (manager.project_dir / "cache" / "vlog" / "atlas" / "atlas.json").read_text(
            encoding="utf-8"
        )
    )
    baseline_ids = [task["task_id"] for task in atlas["tasks"] if task["kind"] == "baseline"]
    assert original_id in baseline_ids
    assert len(baseline_ids) == 2


def test_valid_low_quality_media_is_not_excluded_and_exact_duplicates_are_explicit(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    original = manager.document.media[0]
    duplicate_path = tmp_path / "duplicate.mp4"
    duplicate_path.write_bytes((tmp_path / "clip-000.mp4").read_bytes())
    manager.document.media.append(
        original.model_copy(
            update={
                "id": "media_duplicate",
                "path": manager.store_path(duplicate_path),
                "original_name": duplicate_path.name,
                "metadata": {"quality_score": 0.01},
            }
        )
    )
    manager.document.media[0].metadata["quality_score"] = 0.01
    manager.save(create_snapshot=False)
    result = build_scene_atlas(manager)
    assert result["asset_count"] == 1
    atlas = json.loads(
        (manager.project_dir / "cache" / "vlog" / "atlas" / "atlas.json").read_text(
            encoding="utf-8"
        )
    )
    assert atlas["assets"][0]["media_id"] == "media_000"
    assert atlas["excluded"] == [
        {
            "duplicate_of": "media_000",
            "media_id": "media_duplicate",
            "reason": "exact_duplicate",
        }
    ]


def test_stale_hash_and_out_of_task_observations_are_rejected(tmp_path) -> None:
    manager = _manager(tmp_path, count=2)
    build_scene_atlas(manager, batch_size=1)
    task = next_atlas_inspection_batch(manager.project_dir)
    with pytest.raises(ValueError, match="stale"):
        ingest_atlas_observations(
            manager.require_document(),
            manager.project_dir,
            {
                "task_id": task["task_id"],
                "asset_hashes": {task["media_ids"][0]: "0" * 64},
                "observations": _observations(task),
            },
        )
    wrong = _observations(task)
    wrong[0]["media_id"] = "media_001"
    with pytest.raises(ValueError, match="outside task"):
        ingest_atlas_observations(
            manager.require_document(),
            manager.project_dir,
            {"task_id": task["task_id"], "observations": wrong},
        )


def test_status_reports_resumable_next_task(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    assert scene_atlas_status(manager.project_dir)["stage"] == "not_built"
    build_scene_atlas(manager)
    status = scene_atlas_status(manager.project_dir)
    assert status["stage"] == "inspection"
    assert status["next_task_id"].startswith("atlas_baseline_")
    assert status["next_command"] == "facut vlog inspect batch"


def test_perceptual_duplicates_are_candidates_not_exclusions() -> None:
    assets = [
        {
            "media_id": "one",
            "representative_frames": [{"perceptual_hash": "0000000000000000"}],
        },
        {
            "media_id": "two",
            "representative_frames": [{"perceptual_hash": "0000000000000003"}],
        },
    ]
    _annotate_near_duplicates(assets)
    assert assets[0]["near_duplicate_candidates"] == [
        {"media_id": "two", "hamming_distance": 2}
    ]
    assert assets[1]["near_duplicate_candidates"][0]["media_id"] == "one"
