from __future__ import annotations

import json

from facut.core.models import (
    Clip,
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
    Track,
    TrackType,
)
from facut.core.project_manager import ProjectManager
from facut.intelligence import (
    apply_story_plan,
    build_semantic_index,
    build_narration_plan,
    build_story_plan,
    diagnose_broll,
    load_semantic_index,
    search_semantic_index,
)


def _document(tmp_path) -> ProjectDocument:
    assets = []
    for index, name in enumerate(("airport.mp4", "sunset_family.mp4", "food.mp4"), start=1):
        source = tmp_path / name
        source.write_bytes(name.encode())
        assets.append(
            MediaAsset(
                id=f"media_{index}",
                kind=MediaKind.VIDEO,
                path=str(source),
                original_name=name,
                size=source.stat().st_size,
                sha256=str(index) * 64,
                technical=MediaTechnicalInfo(duration=20, width=3840, height=2160),
            )
        )
    return ProjectDocument(project=ProjectSettings(name="trip"), media=assets)


def test_external_visual_observations_are_searchable_with_evidence(tmp_path) -> None:
    document = _document(tmp_path)
    observations = tmp_path / "observations.json"
    observations.write_text(
        json.dumps(
            {
                "provider": "test-vision",
                "observations": [
                    {
                        "media_id": "media_2",
                        "start": 3,
                        "end": 9,
                        "caption": "一家人在海边看日落",
                        "tags": ["family", "sunset-window", "reaction"],
                        "confidence": 0.93,
                        "evidence": [{"type": "sampled_frames", "frames": [75, 150]}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    built = build_semantic_index(document, tmp_path, observations_file=observations)
    assert built["input_hash"]
    result = search_semantic_index(load_semantic_index(tmp_path), "海边日落")
    assert result["results"][0]["media_id"] == "media_2"
    assert result["results"][0]["start"] == 3
    assert result["results"][0]["evidence"][0]["type"] == "sampled_frames"
    loaded = load_semantic_index(tmp_path)
    ids = [item["id"] for item in loaded["observations"]]
    assert len(ids) == len(set(ids))
    assert any(item.startswith("obs_ext_media_2_") for item in ids)


def test_story_plan_is_review_first_and_apply_creates_named_sequence(tmp_path, monkeypatch) -> None:
    document = _document(tmp_path)
    index = {
        "observations": [
            {"id": "a", "media_id": "media_1", "start": 0, "end": 6, "tags": ["transport"], "confidence": 0.9},
            {"id": "b", "media_id": "media_2", "start": 2, "end": 10, "tags": ["sunset-window", "family"], "confidence": 0.9},
            {"id": "c", "media_id": "media_3", "start": 1, "end": 7, "tags": ["food", "detail"], "confidence": 0.8},
        ]
    }
    plan = build_story_plan(document, index, target_duration=30)
    assert plan["segments"]
    manager = ProjectManager.create(tmp_path / "project", name="trip")
    manager.document.media = document.media
    manager.save(create_snapshot=False)
    state = apply_story_plan(manager, plan, sequence="youtube-story")
    assert state.revision == 1
    assert state.settings["active_sequence"] == "youtube-story"
    assert state.settings["sequences"]["youtube-story"]["tracks"]


def test_broll_diagnostic_finds_long_uncovered_aroll_and_repeat(tmp_path) -> None:
    document = _document(tmp_path)
    document.media[0].metadata = {
        "analysis": {"travel": {"candidates": [{"label": "a-roll"}]}}
    }
    document.tracks = [
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[
                Clip(media_id="media_1", track_id="V1", source_out=10),
                Clip(media_id="media_1", track_id="V1", timeline_start=10, source_out=15),
            ],
        )
    ]
    result = diagnose_broll(document)
    codes = {item["code"] for item in result["issues"]}
    assert "LONG_AROLL_WITHOUT_BROLL" in codes
    assert "REPEATED_SOURCE" in codes


def test_broll_diagnostic_uses_semantic_aroll_observations(tmp_path) -> None:
    document = _document(tmp_path)
    document.tracks = [
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[Clip(id="talk", media_id="media_1", track_id="V1", source_out=12)],
        )
    ]
    result = diagnose_broll(
        document,
        {
            "observations": [
                {
                    "id": "vision-talk",
                    "media_id": "media_1",
                    "start": 1,
                    "end": 11,
                    "tags": ["a-roll", "talking-head"],
                }
            ]
        },
    )
    issue = next(
        item for item in result["issues"] if item["code"] == "LONG_AROLL_WITHOUT_BROLL"
    )
    assert issue["evidence"]["semantic_observation_ids"] == ["vision-talk"]


def test_narration_plan_maps_visual_evidence_to_timeline(tmp_path) -> None:
    document = _document(tmp_path)
    document.tracks = [
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[
                Clip(
                    id="sunset",
                    media_id="media_2",
                    track_id="V1",
                    timeline_start=5,
                    source_in=2,
                    source_out=10,
                )
            ],
        )
    ]
    plan = build_narration_plan(
        document,
        {
            "observations": [
                {
                    "id": "sunset-observation",
                    "media_id": "media_2",
                    "start": 3,
                    "end": 8,
                    "caption": "一家人在海边看日落",
                    "tags": ["family", "sunset"],
                    "confidence": 0.93,
                    "provider": "test-vision",
                    "evidence": [{"type": "sampled_frames", "frames": [90, 180]}],
                }
            ]
        },
    )
    assert plan["status"] == "review_required"
    assert plan["lines"][0]["timeline_range"] == {"start": 6.0, "end": 11.0}
    assert "海边看日落" in plan["lines"][0]["draft_text"]
    assert plan["lines"][0]["evidence"]["provider"] == "test-vision"


def test_narration_plan_maps_reverse_clip_source_time(tmp_path) -> None:
    document = _document(tmp_path)
    document.tracks = [
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[
                Clip(
                    id="reverse",
                    media_id="media_2",
                    track_id="V1",
                    timeline_start=5,
                    source_in=2,
                    source_out=10,
                    speed=-1,
                )
            ],
        )
    ]
    plan = build_narration_plan(
        document,
        {
            "observations": [
                {
                    "id": "reverse-observation",
                    "media_id": "media_2",
                    "start": 3,
                    "end": 8,
                    "caption": "逆向镜头中的人物",
                    "confidence": 0.9,
                }
            ]
        },
    )
    assert plan["lines"][0]["timeline_range"] == {"start": 7.0, "end": 12.0}
