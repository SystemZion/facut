from __future__ import annotations

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo, ProjectDocument, ProjectSettings
from facut.core.sequences import (
    checkout_sequence,
    duplicate_sequence,
    list_sequences,
    materialize_sequence,
    snapshot_sequence,
)
from facut.core.timeline_engine import TimelineEngine


def test_named_sequences_preserve_independent_timelines() -> None:
    document = ProjectDocument(
        project=ProjectSettings(name="sequences"),
        media=[
            MediaAsset(
                id="m1",
                kind=MediaKind.VIDEO,
                path="source.mp4",
                original_name="source.mp4",
                size=1,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(duration=20, video_codec="h264"),
            )
        ],
    )
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    timeline.add_clip("m1", "V1", source_out=20)
    snapshot_sequence(document, "main")
    document.tracks[0].clips[0].source_out = 5
    document.recompute_duration()
    snapshot_sequence(document, "highlight")
    assert materialize_sequence(document, "main").project.duration == 20
    assert materialize_sequence(document, "highlight").project.duration == 5
    duplicate_sequence(document, "highlight", "social")
    checkout_sequence(document, "main")
    assert document.project.duration == 20
    assert [item["name"] for item in list_sequences(document)] == [
        "highlight",
        "main",
        "social",
    ]
