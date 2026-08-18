from __future__ import annotations

from facut.core.models import (
    Clip,
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    Track,
    TrackType,
)
from facut.core.project_manager import ProjectManager
from facut.subtitles.director import (
    add_glossary_entry,
    apply_transcript_plan,
    build_transcript_plan,
    format_caption,
)


class _Fonts:
    def match(self, role, *, language):
        assert role == "caption-sans"
        return {
            "selected_family": "Test CJK Sans",
            "selected_path": "test.ttf",
            "role": role,
            "language": language,
            "source": "test",
            "license_file": None,
            "candidates": [],
        }


def _manager(tmp_path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="trip")
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"talk")
    manager.document.media.append(
        MediaAsset(
            id="media_talk",
            kind=MediaKind.VIDEO,
            path=manager.store_path(source),
            original_name=source.name,
            size=source.stat().st_size,
            sha256="a" * 64,
            technical=MediaTechnicalInfo(duration=20),
        )
    )
    manager.document.tracks.append(
        Track(
            id="V1",
            name="V1",
            type=TrackType.VIDEO,
            clips=[
                Clip(
                    id="talk",
                    media_id="media_talk",
                    track_id="V1",
                    timeline_start=5,
                    source_in=2,
                    source_out=12,
                )
            ],
        )
    )
    manager.document.recompute_duration()
    manager.save(create_snapshot=False)
    return manager


def test_caption_formatting_reports_readability_without_changing_meaning() -> None:
    text = "今天我们终于来到了上海天文馆一起看看里面有什么"
    formatted, warnings = format_caption(text, "zh-CN", 2)
    assert "\n" in formatted
    assert formatted.replace("\n", "") == text
    assert "SUBTITLE_READING_SPEED_HIGH" in warnings


def test_caption_formatting_never_truncates_long_text() -> None:
    text = "这是一段非常长的字幕内容用于确认系统不会为了满足行宽而偷偷删除任何一个原始字符"
    formatted, warnings = format_caption(text, "zh-CN", 8)
    assert formatted.replace("\n", "") == text
    assert "SUBTITLE_LINE_TOO_LONG" in warnings


def test_transcript_plan_maps_source_to_timeline_and_preserves_raw_text(tmp_path) -> None:
    manager = _manager(tmp_path)
    add_glossary_entry(manager.project_dir, "上海天文馆", "attraction")
    plan = build_transcript_plan(
        manager.require_document(),
        manager.project_dir,
        {
            "media_talk": {
                "segments": [
                    {
                        "start": 3,
                        "end": 6,
                        "text": "我们来到了上海天文馆",
                        "confidence": 0.96,
                        "words": [
                            {"start": 3, "end": 4, "text": "我们", "confidence": 0.98}
                        ],
                    }
                ]
            }
        },
        language="zh-CN",
        model="test-whisper",
        word_timestamps=True,
    )
    cue = plan.cues[0]
    assert cue.timeline_start == 6
    assert cue.timeline_end == 9
    assert cue.raw_text.replace("\n", "") == "我们来到了上海天文馆"
    assert cue.status == "approved"


def test_apply_transcript_uses_readable_font_and_one_revision(tmp_path) -> None:
    manager = _manager(tmp_path)
    plan = build_transcript_plan(
        manager.require_document(),
        manager.project_dir,
        {
            "media_talk": {
                "segments": [
                    {"start": 3, "end": 6, "text": "自然字幕", "confidence": 0.99}
                ]
            }
        },
        language="zh-CN",
        model="test",
        word_timestamps=False,
    )
    result = apply_transcript_plan(manager, plan, font_catalog=_Fonts())
    assert result["project_revision"] == 1
    state = manager.require_document()
    assert state.subtitle_cues[0].style.font_family == "Test CJK Sans"
    assert state.subtitle_cues[0].metadata["raw_text"] == "自然字幕"
    assert state.find_track("S_DIALOGUE").metadata["role"] == "dialogue-caption"
