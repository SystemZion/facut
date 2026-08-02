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
