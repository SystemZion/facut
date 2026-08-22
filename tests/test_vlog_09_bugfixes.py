from __future__ import annotations

import json

import pytest

from facut.cli.vlog_commands import _apply_candidate
from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.vlog.director import candidate_document, ingest_observations, refine_story_candidate
from facut.vlog.labs import submit_story_proposal


def _manager(tmp_path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="trip")
    source = tmp_path / "trip.mp4"
    source.write_bytes(b"video")

    def seed(document):
        document.media.append(
            MediaAsset(
                id="media_trip",
                kind=MediaKind.VIDEO,
                path=manager.store_path(source),
                original_name=source.name,
                size=source.stat().st_size,
                sha256="a" * 64,
                technical=MediaTechnicalInfo(
                    duration=10,
                    video_codec="h264",
                    audio_codec="aac",
                    frame_rate=30,
                ),
            )
        )

    manager.mutate("test.seed", "Seed trip media", seed)
    manager.load()
    return manager


def _write_observations(manager: ProjectManager, observations: list[dict]) -> None:
    root = manager.project_dir / "cache" / "vlog"
    root.mkdir(parents=True, exist_ok=True)
    (root / "observations.json").write_text(
        json.dumps({"version": "2.0", "observations": observations}),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps({"project_revision": manager.require_document().revision}),
        encoding="utf-8",
    )


def _observation(
    observation_id: str,
    start: float,
    *,
    location: str | None = None,
    role: str | None = None,
    order: int | None = None,
) -> dict:
    return {
        "observation_id": observation_id,
        "media_id": "media_trip",
        "range": {"start": start, "end": start + 1},
        "summary": observation_id,
        "location_id": location,
        "subject_id": "woman_01" if role else None,
        "event_chain": "ski-fall-recovery" if role else None,
        "event_order": order,
        "story_role": role,
        "confidence": 0.95,
        "quality": 0.9,
    }


def _segment(observation_id: str, source_in: float, timeline_start: float) -> dict:
    return {
        "stage": "change",
        "media_id": "media_trip",
        "source_in": source_in,
        "source_out": source_in + 1,
        "timeline_start": timeline_start,
        "duration": 1,
        "observation_id": observation_id,
        "reason": "Evidence-backed selection.",
        "confidence": 0.95,
    }


def _proposal(segments: list[dict], *, candidate_id: str = "external") -> dict:
    return {
        "version": "3.0",
        "style": "natural-vlog",
        "target_duration": 5,
        "selected_candidate_id": candidate_id,
        "candidates": [
            {
                "id": candidate_id,
                "name": "External director",
                "strategy": "narrative",
                "score": 0.9,
                "estimated_duration": sum(item["duration"] for item in segments),
                "segments": segments,
            }
        ],
    }


def test_external_story_is_immediately_refinable_and_rejects_stale_evidence(tmp_path) -> None:
    manager = _manager(tmp_path)
    observations = [_observation("arrival", 0)]
    _write_observations(manager, observations)
    plan = submit_story_proposal(
        manager.project_dir,
        _proposal([_segment("arrival", 0, 0)]),
    )
    assert plan.trip_bible_sha256
    assert plan.candidates[0].polish_plan["trip_bible"]["trip_name"] == "trip"
    assert refine_story_candidate(manager.project_dir, "external").status == "ready"

    ingest_observations(
        manager.require_document(),
        manager.project_dir,
        {"observations": [_observation("new-evidence", 2)]},
    )
    with pytest.raises(ValueError, match="Visual evidence changed"):
        candidate_document(manager.require_document(), manager.project_dir, "external")


def test_external_story_enforces_event_chain_order_and_recovery(tmp_path) -> None:
    manager = _manager(tmp_path)
    observations = [
        _observation("fall", 0, role="incident", order=1),
        _observation("recover", 1, role="recovery", order=2),
    ]
    _write_observations(manager, observations)
    with pytest.raises(ValueError, match="EVENT_CHAIN_ORDER_VIOLATION"):
        submit_story_proposal(
            manager.project_dir,
            _proposal([_segment("recover", 1, 0), _segment("fall", 0, 1)]),
        )
    with pytest.raises(ValueError, match="MISSING_EVENT_CHAIN_RECOVERY"):
        submit_story_proposal(
            manager.project_dir,
            _proposal([_segment("fall", 0, 0)]),
        )


def test_external_story_rejects_invalid_source_and_timeline_ranges(tmp_path) -> None:
    manager = _manager(tmp_path)
    _write_observations(
        manager,
        [_observation("first", 0), _observation("second", 2)],
    )
    invalid_source = _segment("first", 0, 0)
    invalid_source["source_out"] = 11
    invalid_source["duration"] = 11
    with pytest.raises(ValueError, match="STORY_SOURCE_RANGE_EXCEEDS_MEDIA"):
        submit_story_proposal(manager.project_dir, _proposal([invalid_source]))

    with pytest.raises(ValueError, match="STORY_TIMELINE_OVERLAP"):
        submit_story_proposal(
            manager.project_dir,
            _proposal([_segment("first", 0, 0), _segment("second", 2, 0.5)]),
        )


def test_location_card_survives_candidate_application(tmp_path) -> None:
    manager = _manager(tmp_path)
    _write_observations(
        manager,
        [_observation("place-a", 0, location="A"), _observation("place-b", 2, location="B")],
    )
    proposal = _proposal(
        [_segment("place-a", 0, 0), _segment("place-b", 2, 1)],
        candidate_id="location-cut",
    )
    proposal["candidates"][0]["polish_plan"] = {
        "transition_intents": [
            {"type": "location-card", "between": ["place-a", "place-b"]}
        ]
    }
    submit_story_proposal(manager.project_dir, proposal)
    preview, _ = candidate_document(
        manager.require_document(), manager.project_dir, "location-cut"
    )
    assert len(preview.text_overlays) == 1
    saved, _, _ = _apply_candidate(
        manager, candidate_id="location-cut", preset="youtube-4k"
    )
    assert len(saved.text_overlays) == 1
    assert saved.text_overlays[0].metadata["source"] == "vlog-location-card"
    revision = saved.revision
    repeated, _, _ = _apply_candidate(
        manager, candidate_id="location-cut", preset="youtube-4k"
    )
    assert repeated.revision == revision
    assert len(repeated.text_overlays) == 1
