"""Project subtitle and text overlay tests."""

from __future__ import annotations

import pytest

from facut.core.models import ProjectDocument, ProjectSettings, Track, TrackType
from facut.subtitles.compiler import SubtitleCompiler
from facut.subtitles.editor import (
    SubtitleEditError,
    add_text_overlay,
    import_cues,
    shift_track,
)
from facut.subtitles.formats import ParsedCue


def _project() -> ProjectDocument:
    return ProjectDocument(
        project=ProjectSettings(name="captions", width=1920, height=1080),
        tracks=[
            Track(id="S1", type=TrackType.SUBTITLE, name="Captions"),
            Track(id="V1", type=TrackType.VIDEO, name="Video", order=1),
        ],
    )


def test_import_shift_and_project_round_trip() -> None:
    project = _project()
    created = import_cues(
        project,
        "S1",
        [
            ParsedCue(0.5, 1.5, "你好"),
            ParsedCue(2.0, 3.0, "再见", settings={"align": "middle"}),
        ],
        offset=0.25,
        source="captions.srt",
    )
    assert [cue.start for cue in created] == [0.75, 2.25]
    assert project.project.duration == 3.25

    shifted = shift_track(project, "S1", 0.5)
    assert [cue.start for cue in shifted] == [1.25, 2.75]
    restored = ProjectDocument.model_validate_json(project.model_dump_json())
    assert restored.subtitle_cues[0].text == "你好"


def test_wrong_track_and_negative_shift_are_rejected() -> None:
    project = _project()
    with pytest.raises(SubtitleEditError, match="not a subtitle"):
        import_cues(project, "V1", [ParsedCue(0, 1, "bad")])
    import_cues(project, "S1", [ParsedCue(0.1, 1, "early")])
    with pytest.raises(SubtitleEditError, match="before time zero"):
        shift_track(project, "S1", -0.2)


def test_ass_compiler_includes_cues_and_positioned_text() -> None:
    project = _project()
    import_cues(project, "S1", [ParsedCue(1, 2, "Line 1\nLine 2")])
    add_text_overlay(
        project,
        text="临港 VLOG",
        at=0,
        duration=2,
        x="50%",
        y="20%",
    )
    plan = SubtitleCompiler().compile(project)
    assert plan.format == "ass"
    assert plan.cue_count == 1
    assert plan.text_overlay_count == 1
    assert r"Line 1\NLine 2" in plan.content
    assert r"\pos(960.00,216.00)" in plan.content
    assert "临港 VLOG" in plan.content


def test_lingang_template_compiles_vector_panel_and_bilingual_layers() -> None:
    project = _project()
    project.project.width = 3840
    project.project.height = 2160
    add_text_overlay(
        project,
        text="抬头，是更大的尺度",
        subtitle="LOOK UP · THE SCALE CHANGES",
        at=0,
        duration=3,
        x="5%",
        y="71.3%",
        template="lingang-cinematic-panel",
        template_parameters={"accent_color": "#66E1FF"},
    )
    plan = SubtitleCompiler().compile(project)
    assert r"\p1" in plan.content
    assert "Dialogue: 4" in plan.content
    assert "抬头，是更大的尺度" in plan.content
    assert "LOOK UP · THE SCALE CHANGES" in plan.content
    assert "FFE166" in plan.content  # ASS BGR form of #66E1FF.
    assert "Fontsize" in plan.content


def test_default_caption_style_scales_for_4k_and_uses_cjk_font() -> None:
    project = _project()
    project.project.width = 3840
    project.project.height = 2160
    import_cues(project, "S1", [ParsedCue(1, 2, "上海天文馆")])

    plan = SubtitleCompiler().compile(project)

    assert "Style: Style0,Source Han Sans SC,104," in plan.content
    assert ",4,2,2," in plan.content
