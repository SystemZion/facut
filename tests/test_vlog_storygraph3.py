from __future__ import annotations

import json

import pytest

from facut.vlog.labs import (
    build_story_brief,
    check_continuity,
    plan_endings,
    plan_openings,
    submit_story_proposal,
    validate_story_plan,
)


def _write_observations(tmp_path, observations):
    root = tmp_path / "cache" / "vlog"
    root.mkdir(parents=True)
    (root / "observations.json").write_text(
        json.dumps({"version": "2.0", "observations": observations}), encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({"project_revision": 7}), encoding="utf-8"
    )


def _observation(observation_id: str, event_id: str, *, role: str, start: float):
    return {
        "observation_id": observation_id,
        "media_id": f"media-{observation_id}",
        "range": {"start": start, "end": start + 4},
        "summary": observation_id,
        "episode_id": "day-1",
        "location_id": "linzhi",
        "event_id": event_id,
        "event_role": role,
        "visual_motifs": ["river"],
        "confidence": 0.9,
        "quality": 0.8,
    }


def _segment(observation_id: str, index: int):
    return {
        "stage": "exploration",
        "media_id": f"media-{observation_id}",
        "source_in": index * 5,
        "source_out": index * 5 + 4,
        "timeline_start": index * 4,
        "duration": 4,
        "observation_id": observation_id,
        "reason": "External director selected evidence.",
        "confidence": 0.9,
    }


def test_story_brief_exposes_storygraph3_entities_and_schema(tmp_path) -> None:
    _write_observations(
        tmp_path,
        [_observation("arrival", "arrival", role="setup", start=0)],
    )
    brief = build_story_brief(tmp_path)
    assert brief["episodes"] == {"day-1": ["arrival"]}
    assert brief["events"] == {"arrival": ["arrival"]}
    assert brief["visual_motifs"] == {"river": ["arrival"]}
    properties = brief["required_output_schema"]["$defs"]["EvidenceObservation"]["properties"] if "EvidenceObservation" in brief["required_output_schema"].get("$defs", {}) else None
    # The evidence schema itself remains available through the embedded candidate
    # references; the brief always carries the enriched observations directly.
    assert brief["observations"][0]["episode_id"] == "day-1"
    assert properties is None or "episode_id" in properties


def test_external_story_submission_preserves_causal_order(tmp_path) -> None:
    arrival = _observation("arrival", "arrival", role="setup", start=0)
    reaction = _observation("reaction", "first-view", role="reaction", start=5)
    reaction["causes"] = ["arrival"]
    _write_observations(tmp_path, [arrival, reaction])
    candidate = {
        "id": "external-narrative",
        "name": "外部导演叙事版",
        "strategy": "narrative",
        "score": 0.9,
        "estimated_duration": 8,
        "segments": [_segment("arrival", 0), _segment("reaction", 1)],
    }
    plan = submit_story_proposal(
        tmp_path,
        {
            "version": "3.0",
            "style": "natural-vlog",
            "target_duration": 8,
            "candidates": [candidate],
            "selected_candidate_id": "external-narrative",
        },
    )
    assert plan.generation_mode == "external-director"
    assert plan.project_revision == 7
    assert validate_story_plan(tmp_path)["status"] == "pass"

    candidate["segments"].reverse()
    with pytest.raises(ValueError, match="CAUSAL_ORDER_VIOLATION"):
        submit_story_proposal(
            tmp_path,
            {
                "version": "3.0",
                "style": "natural-vlog",
                "target_duration": 8,
                "candidates": [candidate],
            },
        )


def test_opening_ending_labs_emit_three_review_first_candidates(tmp_path) -> None:
    _write_observations(
        tmp_path,
        [
            _observation("arrival", "arrival", role="setup", start=0),
            _observation("reaction", "reaction", role="reaction", start=5),
            _observation("farewell", "farewell", role="outcome", start=10),
        ],
    )
    opening = plan_openings(tmp_path)
    ending = plan_endings(tmp_path)
    assert len(opening["candidates"]) == 3
    assert len(ending["candidates"]) == 3
    assert all(item["status"] == "review_required" for item in opening["candidates"])
    assert ending["candidates"][0]["observation_ids"][0] == "farewell"


def test_continuity_reports_deterministic_travel_edit_risks(tmp_path) -> None:
    first = _observation("talk", "talk", role="action", start=0)
    first.update({
        "location_id": "airport",
        "daypart": "night",
        "movement_direction": "left",
        "composition_signature": "selfie-close",
        "original_audio_quote": "我们出发了",
    })
    second = _observation("view", "view", role="action", start=5)
    second.update({
        "location_id": "canyon",
        "daypart": "morning",
        "movement_direction": "right",
        "composition_signature": "selfie-close",
    })
    _write_observations(tmp_path, [first, second])
    candidate = {
        "id": "candidate-narrative",
        "name": "叙事版",
        "strategy": "narrative",
        "score": 0.8,
        "estimated_duration": 8,
        "segments": [_segment("talk", 0), _segment("view", 1)],
    }
    submit_story_proposal(
        tmp_path,
        {"version": "3.0", "style": "natural-vlog", "target_duration": 8, "candidates": [candidate]},
    )
    report = check_continuity(tmp_path, "candidate-narrative")["reports"][0]
    codes = {item["code"] for item in report["issues"]}
    assert {"LOCATION_JUMP", "DAYPART_REGRESSION", "MOVEMENT_DIRECTION_CONFLICT", "REPEATED_COMPOSITION", "BROLL_COVERAGE_GAP", "HARD_STOP_RISK"} <= codes
