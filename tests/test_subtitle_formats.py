"""Subtitle parser and serializer tests."""

from __future__ import annotations

import pytest

from facut.subtitles.formats import (
    SubtitleFormatError,
    export_srt,
    export_vtt,
    parse_srt,
    parse_vtt,
)


def test_srt_multiline_round_trip() -> None:
    cues = parse_srt(
        "\ufeff1\r\n00:00:01,250 --> 00:00:03,500\r\n第一行\r\n第二行\r\n\r\n"
        "2\r\n00:01:04.000 --> 00:01:05.125\r\nDone\r\n"
    )
    assert len(cues) == 2
    assert cues[0].start == 1.25
    assert cues[0].end == 3.5
    assert cues[0].text == "第一行\n第二行"
    assert cues[0].identifier == "1"

    rendered = export_srt(cues)
    assert "00:00:01,250 --> 00:00:03,500" in rendered
    assert rendered.endswith("\n")
    reparsed = parse_srt(rendered)
    assert [(cue.start, cue.end, cue.text) for cue in reparsed] == [
        (cue.start, cue.end, cue.text) for cue in cues
    ]


def test_webvtt_metadata_settings_and_round_trip() -> None:
    cues = parse_vtt(
        "WEBVTT - facut\nLanguage: zh-CN\nKind: captions\n\n"
        "NOTE generated fixture\nignore this\n\n"
        "intro\n00:01.000 --> 00:03.250 align:start position:10%\nHello\nworld\n"
    )
    assert len(cues) == 1
    assert cues[0].identifier == "intro"
    assert cues[0].settings == {"align": "start", "position": "10%"}

    rendered = export_vtt(cues)
    assert rendered.startswith("WEBVTT\n\n")
    assert "00:01.000 --> 00:03.250 align:start position:10%" in rendered
    assert parse_vtt(rendered)[0] == cues[0]


@pytest.mark.parametrize(
    "content",
    [
        "1\n00:00:02,000 --> 00:00:01,000\nBackwards\n",
        "1\nnot a timing line\nBroken\n",
        "1\n00:00:01,000 --> 00:00:02,000\n",
    ],
)
def test_invalid_srt_is_rejected(content: str) -> None:
    with pytest.raises(SubtitleFormatError):
        parse_srt(content)
