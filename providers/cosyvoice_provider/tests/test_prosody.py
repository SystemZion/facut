from __future__ import annotations

from facut_cosyvoice_provider.cli import (
    _instruction,
    _output_path,
    _pause_seconds,
    _split_for_prosody,
)


def test_split_for_prosody_keeps_sentence_punctuation() -> None:
    text = "今天不赶时间，就慢慢走一走。天气很好，风也很轻！这样就很舒服。"
    assert _split_for_prosody(text) == [
        "今天不赶时间，就慢慢走一走。",
        "天气很好，风也很轻！",
        "这样就很舒服。",
    ]


def test_long_clause_splits_on_soft_boundary() -> None:
    text = "这是一个很长的开场白，接下来我们会沿着河边继续向前走，顺便看看今天傍晚的风景和路边的小店。"
    chunks = _split_for_prosody(text, max_chars=22)
    assert "".join(chunks) == text
    assert all(len(item) <= 35 for item in chunks)


def test_delivery_instruction_and_pause_are_natural() -> None:
    instruction = _instruction({"instruction": "重点不要过分强调。"}, "natural-vlog")
    assert "不要播音腔" in instruction
    assert "重点不要过分强调" in instruction
    assert instruction.endswith("<|endofprompt|>")
    assert _pause_seconds("一句话。") > _pause_seconds("半句话，")


def test_five_user_styles_have_distinct_guidance() -> None:
    styles = ["natural", "broadcast", "chat", "comedy", "excited"]
    instructions = [_instruction({}, style) for style in styles]
    assert len(set(instructions)) == 5
    assert "播音腔" in instructions[0]
    assert "信息感" in instructions[1]
    assert "朋友聊天" in instructions[2]
    assert "笑意" in instructions[3]
    assert "兴奋" in instructions[4]


def test_cache_key_changes_with_delivery_and_candidate(tmp_path) -> None:
    base = {"delivery": "natural-vlog", "candidate_index": 0}
    first = _output_path(tmp_path, "voice_TEST", 0, "你好。", base)
    second = _output_path(
        tmp_path, "voice_TEST", 0, "你好。", {**base, "delivery": "reflective"}
    )
    third = _output_path(
        tmp_path, "voice_TEST", 0, "你好。", {**base, "candidate_index": 1}
    )
    assert len({first, second, third}) == 3
