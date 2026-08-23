"""ASR round-trip verification for factual voice synthesis."""

from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
import re
from pathlib import Path
from typing import Any


_PUNCTUATION = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")
_ARABIC_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CHINESE_NUMBER = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")
_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}


def _normalize(text: str) -> str:
    return _PUNCTUATION.sub("", text).casefold()


def _chinese_integer(text: str) -> int:
    if all(character in _DIGITS for character in text):
        return int("".join(str(_DIGITS[item]) for item in text))
    total = section = number = 0
    for character in text:
        if character in _DIGITS:
            number = _DIGITS[character]
        elif character in _SMALL_UNITS:
            unit = _SMALL_UNITS[character]
            section += (number or 1) * unit
            number = 0
        elif character in _LARGE_UNITS:
            section += number
            total += (section or 1) * _LARGE_UNITS[character]
            section = number = 0
    return total + section + number


def factual_numbers(text: str) -> list[str]:
    values = [match.group(0).lstrip("0") or "0" for match in _ARABIC_NUMBER.finditer(text)]
    values.extend(str(_chinese_integer(match.group(0))) for match in _CHINESE_NUMBER.finditer(text))
    return values


def transcript_text(payload: dict[str, Any]) -> str:
    return "".join(str(item.get("text") or "") for item in payload.get("segments", []))


def verify_transcript(
    expected: str,
    actual: str,
    *,
    required_entities: list[str] | None = None,
    minimum_similarity: float = 0.70,
) -> dict[str, Any]:
    expected_normalized = _normalize(expected)
    actual_normalized = _normalize(actual)
    similarity = SequenceMatcher(None, expected_normalized, actual_normalized).ratio()
    expected_numbers = factual_numbers(expected)
    actual_numbers = factual_numbers(actual)
    missing_numbers = [item for item in expected_numbers if item not in actual_numbers]
    missing_entities = [
        item for item in (required_entities or []) if _normalize(item) not in actual_normalized
    ]
    passed = (
        similarity >= minimum_similarity
        and not missing_numbers
        and not missing_entities
    )
    return {
        "passed": passed,
        "similarity": round(similarity, 6),
        "minimum_similarity": minimum_similarity,
        "expected_numbers": expected_numbers,
        "actual_numbers": actual_numbers,
        "missing_numbers": missing_numbers,
        "missing_entities": missing_entities,
        "transcript": actual,
    }


def verify_outputs(
    paths: list[str | Path],
    expected: str,
    *,
    transcribe: Callable[[Path], dict[str, Any]],
    required_entities: list[str] | None = None,
    minimum_similarity: float = 0.70,
) -> list[dict[str, Any]]:
    reports = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        payload = transcribe(path)
        report = verify_transcript(
            expected,
            transcript_text(payload),
            required_entities=required_entities,
            minimum_similarity=minimum_similarity,
        )
        reports.append({"output": str(path), **report})
    return reports


__all__ = ["factual_numbers", "transcript_text", "verify_outputs", "verify_transcript"]
