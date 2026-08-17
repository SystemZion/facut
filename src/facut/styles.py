"""Inspectable VLOG directing style packs, not opaque filter presets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STYLE_PACKS: dict[str, dict[str, Any]] = {
    "natural-vlog": {
        "story": "chronological-experience",
        "pace": "natural",
        "audio": ["preserve-dialogue", "preserve-useful-ambience"],
        "typography": {"body": "caption-sans", "titles": "caption-sans"},
        "transition_policy": "hard-cuts-and-audio-bridges-first",
    },
    "comedy-vlog": {
        "story": "setup-reaction-payoff",
        "pace": "variable-with-comedic-pauses",
        "audio": ["preserve-reactions", "music-stop", "licensed-impact-sfx"],
        "typography": {"body": "caption-sans", "titles": "comedy-heavy"},
        "transition_policy": "hard-cut-freeze-or-flash-only-with-evidence",
        "effects": [
            {"name": "punch-zoom", "requires": ["clear-reaction"]},
            {"name": "freeze-repeat", "requires": ["complete-action"]},
            {"name": "laser-eyes", "requires": ["two-eye-anchors", "confidence>=0.8"], "fallback": "punch-zoom"},
        ],
        "restraint": "Return to normal pacing after each joke; scenery and sincere emotion stay clean.",
    },
    "cinematic-travel": {
        "story": "visual-emotion-arc",
        "pace": "measured",
        "audio": ["ambience-bridges", "music-dynamics"],
        "typography": {"body": "caption-sans", "titles": "cinematic-light"},
        "transition_policy": "movement-match-or-restrained-dissolve",
    },
    "humanities-documentary": {
        "story": "place-context-human-detail",
        "pace": "informative",
        "audio": ["dialogue-priority", "room-tone"],
        "typography": {"body": "caption-sans", "titles": "documentary-serif"},
        "transition_policy": "hard-cut-location-card-or-dissolve",
    },
    "family-trip": {
        "story": "shared-experience-and-reaction",
        "pace": "warm",
        "audio": ["family-reactions", "gentle-music-ducking"],
        "typography": {"body": "caption-sans", "titles": "friendly-rounded"},
        "transition_policy": "soft-cuts-and-ambience",
    },
    "food-walk": {
        "story": "arrival-detail-taste-reaction",
        "pace": "detail-forward",
        "audio": ["cooking-sounds", "reaction-dialogue"],
        "typography": {"body": "caption-sans", "titles": "food-handwritten"},
        "transition_policy": "detail-match-and-hard-cuts",
    },
    "relaxed-daily": {
        "story": "small-moments",
        "pace": "unhurried",
        "audio": ["natural-room-tone", "light-music"],
        "typography": {"body": "caption-sans", "titles": "friendly-rounded"},
        "transition_policy": "mostly-hard-cuts",
    },
}


def list_styles() -> dict[str, dict[str, Any]]:
    return deepcopy(STYLE_PACKS)


def describe_style(name: str) -> dict[str, Any]:
    if name not in STYLE_PACKS:
        raise ValueError(f'Unknown VLOG style "{name}".')
    return {"name": name, **deepcopy(STYLE_PACKS[name])}


def validate_style_usage(document: Any, name: str) -> dict[str, Any]:
    definition = describe_style(name)
    minutes = max(document.project.duration / 60, 1 / 60)
    transition_density = len(document.transitions) / minutes
    effect_count = sum(len(clip.effects) for track in document.tracks for clip in track.clips)
    effect_density = effect_count / minutes
    issues = []
    if transition_density > 6:
        issues.append({"code": "EXCESSIVE_TRANSITION_DENSITY", "per_minute": round(transition_density, 2)})
    if effect_density > 5:
        issues.append({"code": "EXCESSIVE_EFFECT_DENSITY", "per_minute": round(effect_density, 2)})
    return {
        "style": definition,
        "status": "pass" if not issues else "review_required",
        "issues": issues,
        "metrics": {"transitions_per_minute": transition_density, "effects_per_minute": effect_density},
    }
