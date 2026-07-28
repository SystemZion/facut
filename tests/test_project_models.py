from __future__ import annotations

import pytest
from pydantic import ValidationError

from facut.core.models import Clip, MediaAsset, MediaKind, ProjectDocument, ProjectSettings, Track


def asset() -> MediaAsset:
    return MediaAsset(
        id="media_A",
        kind=MediaKind.VIDEO,
        path="sample.mp4",
        original_name="sample.mp4",
        size=123,
        sha256="a" * 64,
    )


def test_project_round_trip_and_computed_duration() -> None:
    clip = Clip(
        id="clip_A",
        media_id="media_A",
        track_id="V1",
        timeline_start=2.0,
        source_in=1.0,
        source_out=7.0,
        speed=2.0,
    )
    document = ProjectDocument(
        project=ProjectSettings(name="demo"),
        media=[asset()],
        tracks=[Track(id="V1", type="video", name="V1", clips=[clip])],
    )
    assert document.recompute_duration() == 5.0
    restored = ProjectDocument.model_validate_json(document.model_dump_json())
    assert restored.find_clip("clip_A") is not None
    assert restored.project.duration == 5.0


def test_project_rejects_dangling_media_reference() -> None:
    with pytest.raises(ValidationError, match="unknown media"):
        ProjectDocument(
            project=ProjectSettings(name="bad"),
            tracks=[
                Track(
                    id="V1",
                    type="video",
                    name="V1",
                    clips=[
                        Clip(
                            media_id="missing",
                            track_id="V1",
                            source_in=0,
                            source_out=1,
                        )
                    ],
                )
            ],
        )


def test_clip_rejects_inverted_source_range() -> None:
    with pytest.raises(ValidationError, match="source_out"):
        Clip(media_id="media_A", track_id="V1", source_in=2, source_out=1)
