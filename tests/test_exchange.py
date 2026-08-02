from __future__ import annotations

from xml.etree import ElementTree as ET

import opentimelineio as otio

from facut.core.models import (
    Marker,
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
)
from facut.core.timeline_engine import TimelineEngine
from facut.core.project_manager import ProjectManager
from facut.exchange import (
    apply_import_plan,
    export_fcpxml,
    export_otio,
    plan_fcpxml_import,
    plan_otio_import,
)


def _project(tmp_path) -> ProjectDocument:
    source = tmp_path / "camera.mp4"
    source.write_bytes(b"media-reference")
    document = ProjectDocument(
        project=ProjectSettings(name="Travel Exchange", width=3840, height=2160, fps=25),
        media=[
            MediaAsset(
                id="media_1",
                kind=MediaKind.VIDEO,
                path=str(source),
                original_name=source.name,
                size=source.stat().st_size,
                sha256="1" * 64,
                technical=MediaTechnicalInfo(
                    duration=20,
                    video_codec="h264",
                    audio_codec="aac",
                    frame_rate=25,
                ),
            )
        ],
        markers=[Marker(at=4, label="抵达", metadata={"category": "story"})],
    )
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    engine.add_clip("media_1", "V1", at=2, source_in=3, source_out=9)
    return document


def test_otio_export_is_readable_and_preserves_facut_metadata(tmp_path) -> None:
    document = _project(tmp_path)
    output = tmp_path / "timeline.otio"
    result = export_otio(document, tmp_path, output)
    timeline = otio.adapters.read_from_file(str(output))
    assert result.clips == 1
    assert timeline.name == "Travel Exchange"
    assert isinstance(timeline.tracks[0][0], otio.schema.Gap)
    clip = timeline.tracks[0][1]
    assert clip.metadata["facut"]["media_id"] == "media_1"
    assert clip.source_range.start_time.to_seconds() == 3
    assert timeline.tracks.markers[0].name == "抵达"


def test_fcpxml_export_has_resources_spine_and_loss_report(tmp_path) -> None:
    document = _project(tmp_path)
    document.text_overlays = []
    output = tmp_path / "timeline.fcpxml"
    result = export_fcpxml(document, tmp_path, output)
    root = ET.parse(output).getroot()
    assert root.tag == "fcpxml"
    assert root.attrib["version"] == "1.9"
    assert root.find("./resources/asset") is not None
    clip = root.find("./library/event/project/sequence/spine/asset-clip")
    assert clip is not None
    assert clip.attrib["offset"] == "2s"
    assert clip.attrib["start"] == "3s"
    assert result.clips == 1


def test_otio_round_trip_apply_preserves_facut_clip_and_marker(tmp_path, monkeypatch) -> None:
    document = _project(tmp_path)
    document.tracks[0].clips[0].speed = 2
    output = tmp_path / "timeline.otio"
    export_otio(document, tmp_path, output)
    plan = plan_otio_import(output)
    assert plan.metadata["markers"][0]["label"] == "抵达"

    project_dir = tmp_path / "imported"
    manager = ProjectManager.create(project_dir, name="imported")
    source_asset = document.media[0]
    monkeypatch.setattr(
        "facut.media.importer.MediaImporter._prepare",
        lambda _self, _path: source_asset.model_copy(deep=True),
    )
    imported = apply_import_plan(manager, plan)
    clip = imported.tracks[0].clips[0]
    assert imported.revision == 1
    assert clip.source_in == 3
    assert clip.source_out == 9
    assert clip.speed == 2
    assert clip.timeline_start == 2
    assert imported.markers[0].label == "抵达"


def test_fcpxml_import_is_plan_first_and_reads_markers(tmp_path) -> None:
    document = _project(tmp_path)
    output = tmp_path / "timeline.fcpxml"
    export_fcpxml(document, tmp_path, output)
    plan = plan_fcpxml_import(output)
    assert plan.clip_count == 1
    assert plan.tracks[0].clips[0].timeline_start == 2
    assert plan.metadata["markers"][0]["label"] == "抵达"
