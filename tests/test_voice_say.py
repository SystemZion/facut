from __future__ import annotations

from pathlib import Path

import pytest

from facut.voice.models import VoiceProfile
from facut.voice.say import build_say_lines, classify_auto_style, synthesize_voice_say
from facut.voice.store import VoiceProfileStore


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("今天我们出来随便走走。", "natural"),
        ("现在是下午四点，接下来前往观景平台。", "broadcast"),
        ("我跟你说，你看这里是不是特别舒服？", "chat"),
        ("其实这里还挺舒服的，反正我们就慢慢走。", "daily-chat"),
        ("结果我们三个人又走错路了，真的有点尴尬。", "comedy"),
        ("快看，真的到了！眼前的景色太壮观了！", "excited"),
    ],
)
def test_auto_style_classifier_is_conservative_and_explainable(text: str, expected: str) -> None:
    result = classify_auto_style(text)
    assert result["selected_style"] == expected
    assert 0 <= result["confidence"] <= 1
    assert result["reason"]


def test_say_lines_create_distinct_audition_candidates() -> None:
    lines, selection = build_say_lines(
        "今天不赶时间，就慢慢走一走。",
        style="auto",
        takes=3,
        speed=1.05,
        intensity=0.7,
        instruction="句尾放松。",
    )
    assert selection["selected_style"] == "natural"
    assert [item["candidate_index"] for item in lines] == [0, 1, 2]
    assert all(item["delivery"] == "natural" for item in lines)
    assert all(item["speed"] == 1.05 for item in lines)
    assert "句尾放松" in lines[0]["instruction"]


def test_daily_chat_is_an_explicit_audition_style() -> None:
    lines, selection = build_say_lines(
        "这里比照片里看起来大多了。", style="daily-chat", takes=2
    )
    assert selection["selected_style"] == "daily-chat"
    assert [item["delivery"] for item in lines] == ["daily-chat", "daily-chat"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"takes": 0},
        {"speed": 2.1},
        {"intensity": -0.1},
        {"style": "documentary"},
    ],
)
def test_say_lines_reject_invalid_quick_call_parameters(kwargs) -> None:
    with pytest.raises(ValueError):
        build_say_lines("测试", **kwargs)


def test_voice_say_returns_selection_and_never_modifies_timeline(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile: VoiceProfile = store.create(
        "Zion",
        speaker_id="zion",
        consent_relationship="self",
        consent_statement="I explicitly authorize local test voice synthesis.",
    )
    captured = {}

    def fake_synthesize(profile, profile_directory, lines, output_directory, **kwargs):
        captured["lines"] = lines
        captured["options"] = kwargs
        return {
            "status": "success", "provider": "fake", "model_version": "test",
            "profile_id": profile.id, "outputs": [], "warnings": [],
        }

    result = synthesize_voice_say(
        profile,
        store.profile_directory(profile.id),
        "我跟你说，今天这里真的挺舒服。",
        tmp_path / "auditions",
        takes=2,
        use_service=False,
        service_options={"device": "cpu", "require_cuda": False},
        synthesize=fake_synthesize,
    )
    assert result["command"] == "voice.say"
    assert result["selection"]["selected_style"] == "chat"
    assert result["audition_required"] is True
    assert result["timeline_modified"] is False
    assert len(captured["lines"]) == 2
    assert captured["options"] == {"device": "cpu", "require_cuda": False}
