"""Reusable deterministic API for quick, audition-first voice narration."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from .models import VoiceProfile
from .presets import VOICE_STYLE_PRESETS, validate_voice_styles
from .providers import synthesize_with_provider
from .service import synthesize_with_voice_service


_STYLE_SIGNALS: dict[str, tuple[tuple[str, float], ...]] = {
    "excited": (
        ("快看", 3.0), ("终于", 2.5), ("真的到了", 3.0), ("太美", 2.0),
        ("壮观", 2.0), ("惊喜", 2.0), ("第一次", 1.0), ("！", 0.7),
    ),
    "comedy": (
        ("结果", 1.3), ("没想到", 2.0), ("走错", 2.5), ("摔倒", 2.5),
        ("尴尬", 2.0), ("居然", 1.5), ("哈哈", 3.0), ("翻车", 2.5),
    ),
    "chat": (
        ("我跟你说", 3.0), ("你看", 2.0), ("你觉得", 2.5), ("咱们", 1.8),
        ("有没有", 1.3), ("吧", 0.5), ("？", 0.7),
    ),
    "broadcast": (
        ("现在是", 2.5), ("接下来", 2.0), ("位于", 2.0), ("开放时间", 2.5),
        ("公里", 1.4), ("分钟", 1.0), ("入口", 1.0), ("建议", 1.0),
    ),
}


def classify_auto_style(text: str, *, purpose: str | None = None) -> dict[str, Any]:
    """Select a conservative VLOG delivery style from explicit text evidence."""

    normalized = str(text).strip()
    if not normalized:
        raise ValueError("Voice text cannot be empty.")
    scores = {style: 0.0 for style in _STYLE_SIGNALS}
    evidence: dict[str, list[str]] = {style: [] for style in _STYLE_SIGNALS}
    for style, signals in _STYLE_SIGNALS.items():
        for token, weight in signals:
            if token in normalized:
                scores[style] += weight
                evidence[style].append(token)
    purpose_value = str(purpose or "").casefold().strip()
    purpose_styles = {
        "information": "broadcast",
        "info": "broadcast",
        "direct-address": "chat",
        "conversation": "chat",
        "mishap": "comedy",
        "humor": "comedy",
        "arrival": "excited",
        "scenic-reveal": "excited",
    }
    if chosen_by_purpose := purpose_styles.get(purpose_value):
        scores[chosen_by_purpose] += 3.0
        evidence[chosen_by_purpose].append(f"purpose:{purpose_value}")
    ranked = sorted(scores, key=lambda item: (-scores[item], item))
    strongest = ranked[0]
    strongest_score = scores[strongest]
    # Weak punctuation or a generic token alone should not force a performed
    # style. Natural is the safe default for ordinary travel narration.
    selected = strongest if strongest_score >= 1.25 else "natural"
    confidence = (
        min(0.97, 0.55 + strongest_score * 0.08)
        if selected != "natural"
        else 0.72 if strongest_score == 0 else 0.58
    )
    alternatives = [
        item for item in ranked if item != selected and scores[item] > 0
    ][:2]
    if "natural" != selected and "natural" not in alternatives:
        alternatives.append("natural")
    return {
        "requested_style": "auto",
        "selected_style": selected,
        "confidence": round(confidence, 3),
        "reason": (
            f"Matched {', '.join(evidence[strongest])}."
            if selected != "natural"
            else "No strong performed-style evidence; using the natural VLOG default."
        ),
        "alternatives": alternatives[:2],
        "evidence": evidence[selected] if selected != "natural" else [],
        "scores": {key: round(value, 3) for key, value in scores.items()},
    }


def build_say_lines(
    text: str,
    *,
    style: str = "auto",
    takes: int = 1,
    speed: float = 1.0,
    intensity: float = 0.5,
    instruction: str | None = None,
    purpose: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build provider lines and return the auditable style decision."""

    clean_text = re.sub(r"\s+", " ", str(text)).strip()
    if not clean_text:
        raise ValueError("Voice text cannot be empty.")
    if len(clean_text) > 5000:
        raise ValueError("Quick voice narration is limited to 5000 characters.")
    if not 1 <= takes <= 10:
        raise ValueError("takes must be between 1 and 10.")
    if not 0.5 <= speed <= 2.0:
        raise ValueError("speed must be between 0.5 and 2.0.")
    if not 0.0 <= intensity <= 1.0:
        raise ValueError("intensity must be between 0 and 1.")
    if style == "auto":
        selection = classify_auto_style(clean_text, purpose=purpose)
        selected = str(selection["selected_style"])
    else:
        selected = validate_voice_styles([style])[0]
        if selected not in VOICE_STYLE_PRESETS:
            # Legacy styles remain provider-compatible, while `voice say` keeps
            # its public automatic choices to the five stable presets.
            raise ValueError("voice say supports natural, broadcast, chat, comedy, excited, or auto.")
        selection = {
            "requested_style": selected,
            "selected_style": selected,
            "confidence": 1.0,
            "reason": "The caller selected this style explicitly.",
            "alternatives": [],
            "evidence": [],
            "scores": {},
        }
    custom = str(instruction or "").strip()
    if len(custom) > 220:
        raise ValueError("instruction must be 220 characters or fewer.")
    intensity_hint = (
        "整体表达克制、少表演。" if intensity < 0.34 else
        "保持自然强度，不要刻意表演。" if intensity < 0.67 else
        "重点和情绪可以更鲜明，但不要喊叫或夸张。"
    )
    guidance = " ".join(item for item in (intensity_hint, custom) if item)
    lines = [
        {
            "id": f"say_{index + 1:02d}",
            "draft_text": clean_text,
            "delivery": selected,
            "speed": speed,
            "instruction": guidance,
            "candidate_index": index,
        }
        for index in range(takes)
    ]
    return lines, {**selection, "intensity": intensity, "speed": speed, "takes": takes}


def synthesize_voice_say(
    profile: VoiceProfile,
    profile_directory: str | Path,
    text: str,
    output_directory: str | Path,
    *,
    style: str = "auto",
    takes: int = 1,
    speed: float = 1.0,
    intensity: float = 0.5,
    instruction: str | None = None,
    purpose: str | None = None,
    provider: str | Path | None = None,
    use_service: bool = True,
    service_options: dict[str, Any] | None = None,
    synthesize: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate audition candidates without modifying a timeline."""

    lines, selection = build_say_lines(
        text,
        style=style,
        takes=takes,
        speed=speed,
        intensity=intensity,
        instruction=instruction,
        purpose=purpose,
    )
    if synthesize is None:
        synthesize = synthesize_with_voice_service if use_service else synthesize_with_provider
    options = dict(service_options or {}) if use_service else {}
    if provider is not None:
        options["provider"] = provider
    result = synthesize(
        profile,
        profile_directory,
        lines,
        output_directory,
        **options,
    )
    return {
        **result,
        "command": "voice.say",
        "selection": selection,
        "audition_required": True,
        "timeline_modified": False,
    }


__all__ = ["build_say_lines", "classify_auto_style", "synthesize_voice_say"]
