from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from facut.cli.main import app
from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.recipe import RecipeDocument, RecipeEngine
from facut.agent import action_schema
from facut.vlog import (
    build_story_candidates,
    compare_story_candidates,
    director_status,
    ingest_observations,
    load_trip_bible,
    next_inbox_items,
    next_inspection_task,
    prepare_evidence_manifest,
    rebuild_director_inbox,
    refine_story_candidate,
    resolve_inbox_item,
    save_trip_bible,
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
    inbox = next_inbox_items(manager.project_dir, limit=2)
    assert inbox["returned"] == 2
    assert all(item["kind"] == "baseline" for item in inbox["items"])
    assert inbox["items"][0]["priority"] >= inbox["items"][1]["priority"]


def test_director_inbox_prioritizes_incomplete_event_chain_and_keeps_resolution(
    tmp_path,
) -> None:
    manager = _manager(tmp_path, count=1)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=1)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [{
                "observation_id": "fall",
                "media_id": "media_0",
                "range": {"start": 1, "end": 5},
                "summary": "女士摔倒，后续尚未确认",
                "subject_id": "woman_01",
                "event_chain": "ski-fall-recovery",
                "event_order": 1,
                "story_role": "incident",
                "confidence": 0.9,
            }]
        },
        task_id="inspect_0001",
    )
    inbox = rebuild_director_inbox(manager.project_dir)
    assert inbox["items"][0]["kind"] == "continuity"
    assert inbox["items"][0]["priority"] == 100
    item_id = inbox["items"][0]["id"]
    resolved = resolve_inbox_item(
        manager.project_dir, item_id, resolution="已检查代理，原素材没有恢复镜头。"
    )
    assert resolved["item"]["status"] == "resolved"
    rebuilt = rebuild_director_inbox(manager.project_dir)
    assert next(item for item in rebuilt["items"] if item["id"] == item_id)["status"] == "resolved"


def test_trip_bible_separates_confirmed_uncertain_and_rejected_claims(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    saved = save_trip_bible(
        manager.project_dir,
        {
            "version": "1.0",
            "trip_name": "西藏旅行",
            "places": [{"id": "linzhi", "display_name": "林芝", "confirmed": True}],
            "glossary": {"雅鲁藏布江大峡谷": "place"},
            "facts": [
                {
                    "id": "weather",
                    "statement": "当天有雨",
                    "status": "uncertain",
                    "source": "external-agent",
                },
                {
                    "id": "arrival",
                    "statement": "行程抵达林芝",
                    "status": "confirmed",
                    "source": "user",
                },
            ],
            "forbidden_claims": ["未经证据确认具体海拔"],
        },
    )
    assert saved["uncertain_fact_count"] == 1
    bible = load_trip_bible(manager.project_dir)
    assert bible.trip_name == "西藏旅行"
    prepare_evidence_manifest(manager, generate_frames=False)
    inbox = rebuild_director_inbox(manager.project_dir)
    assert any(item["kind"] == "fact-review" for item in inbox["items"])


def test_prepare_manifest_accepts_native_frames_without_python_decode(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    frame = tmp_path / "native.jpg"
    frame.write_bytes(b"jpeg")
    native = {
        "media_0": {
            "status": "success",
            "fingerprint": "abc123",
            "engine": {"name": "facut-native", "version": "0.8.2", "mode": "fast"},
            "waveform": {"peaks": []},
            "representative_frames": [
                {
                    "requested_seconds": 0.5,
                    "actual_seconds": 0.52,
                    "frame": str(frame),
                    "luminance_mean": 100.0,
                    "sharpness": 7.5,
                    "motion": 0.0,
                }
            ],
        }
    }
    result = prepare_evidence_manifest(manager, generate_frames=False, native_results=native)
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    item = manifest["assets"][0]
    assert item["analysis_engine"]["name"] == "facut-native"
    assert item["native_fingerprint"] == "abc123"
    assert item["representative_frames"][0]["at"] == 0.52
    assert item["baseline_coverage"] == "complete"


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


def test_storygraph_redistributes_sparse_stage_duration(tmp_path) -> None:
    manager = _manager(tmp_path, count=8)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=8)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": f"obs_{index}",
                    "media_id": f"media_{index}",
                    "range": {"start": 0, "end": 10},
                    "summary": "museum exploration detail",
                    "tags": ["museum", "explore"],
                    "quality": 0.9,
                    "confidence": 0.9,
                }
                for index in range(8)
            ]
        },
        task_id="inspect_0001",
    )
    plan = build_story_candidates(
        manager.require_document(), manager.project_dir, target_duration=60
    )
    assert all(candidate.estimated_duration == 60 for candidate in plan.candidates)


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


def test_ready_candidate_can_be_applied_atomically_from_cli_batch(tmp_path) -> None:
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
        task_id="inspect_0001",
    )
    build_story_candidates(manager.require_document(), manager.project_dir)
    refine_story_candidate(manager.project_dir, "candidate-narrative")
    batch = tmp_path / "apply.json"
    batch.write_text(
        json.dumps(
            {
                "version": "1.0",
                "atomic": True,
                "commands": [
                    {"action": "vlog.apply", "candidate_id": "candidate-narrative"}
                ],
            }
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["--project", str(manager.project_dir), "--json", "run", str(batch)],
    )
    assert result.exit_code == 0, result.output
    reloaded = ProjectManager(manager.project_dir)
    reloaded.load()
    assert reloaded.require_document().settings["vlog_director"]["candidate_id"] == "candidate-narrative"


def test_vlog_apply_is_public_atomic_agent_action() -> None:
    schema = action_schema("vlog.apply")
    assert schema["rpc"] is True
    assert schema["mutates"] is True
    assert "candidate_id" in schema["parameters"]["required"]


def test_director_inbox_and_trip_bible_are_public_agent_actions() -> None:
    assert action_schema("vlog.inbox.next")["rpc"] is True
    assert action_schema("vlog.inbox.resolve")["mutates"] is True
    bible = action_schema("vlog.bible.import")
    assert bible["rpc"] is True
    assert "bible" in bible["parameters"]["required"]


def test_event_chain_keeps_same_subject_incident_and_recovery(tmp_path) -> None:
    manager = _manager(tmp_path, count=3)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=3)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": "arrival",
                    "media_id": "media_0",
                    "range": {"start": 0, "end": 5},
                    "summary": "抵达雪场",
                    "tags": ["arrival"],
                },
                {
                    "observation_id": "fall",
                    "media_id": "media_1",
                    "range": {"start": 1, "end": 5},
                    "summary": "女士滑行后摔倒",
                    "subject_id": "woman_01",
                    "event_chain": "ski-fall-recovery",
                    "event_order": 1,
                    "story_role": "incident",
                    "quality": 0.8,
                },
                {
                    "observation_id": "recovery",
                    "media_id": "media_1",
                    "range": {"start": 6, "end": 11},
                    "summary": "同一女士爬起来继续滑",
                    "subject_id": "woman_01",
                    "event_chain": "ski-fall-recovery",
                    "event_order": 2,
                    "story_role": "recovery",
                    "quality": 0.3,
                },
                {
                    "observation_id": "other-skier",
                    "media_id": "media_2",
                    "range": {"start": 2, "end": 10},
                    "summary": "另一位滑雪者的高质量反应高潮",
                    "tags": ["climax", "reaction"],
                    "quality": 1.0,
                    "confidence": 1.0,
                },
            ]
        },
        task_id="inspect_0001",
    )
    plan = build_story_candidates(
        manager.require_document(), manager.project_dir, target_duration=18
    )
    for candidate in plan.candidates:
        ids = [segment.observation_id for segment in candidate.segments]
        assert "fall" in ids
        assert "recovery" in ids
        assert ids.index("fall") < ids.index("recovery")
        assert "other-skier" not in ids


def test_incomplete_event_chain_requires_review(tmp_path) -> None:
    manager = _manager(tmp_path, count=1)
    prepare_evidence_manifest(manager, generate_frames=False, batch_size=1)
    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {
            "observations": [
                {
                    "observation_id": "fall",
                    "media_id": "media_0",
                    "range": {"start": 1, "end": 5},
                    "summary": "女士摔倒",
                    "subject_id": "woman_01",
                    "event_chain": "ski-fall-recovery",
                    "event_order": 1,
                    "story_role": "incident",
                }
            ]
        },
        task_id="inspect_0001",
    )
    plan = build_story_candidates(manager.require_document(), manager.project_dir)
    refined = refine_story_candidate(manager.project_dir, plan.candidates[0].id)
    assert refined.status == "review_required"
    assert any(
        gap.get("code") == "EVENT_CHAIN_INCOMPLETE"
        for gap in refined.candidates[0].unresolved_gaps
    )
