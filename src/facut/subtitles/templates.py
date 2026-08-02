"""Built-in title templates with stable, machine-readable parameters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TEXT_TEMPLATES: dict[str, dict[str, Any]] = {
    "documentary-lower-third": {
        "description": "Neutral documentary lower third.",
        "renderer": "standard",
        "x": "8%",
        "y": "82%",
        "style": {
            "font_size": 52.0,
            "color": "#FFFFFF",
            "stroke_color": "#000000",
            "stroke_width": 1.5,
            "background": "#00000099",
            "shadow": 2.0,
            "alignment": "left",
            "safe_area": True,
        },
    },
    "chapter": {
        "description": "Centered chapter card.",
        "renderer": "standard",
        "x": "center",
        "y": "center",
        "style": {
            "font_size": 84.0,
            "color": "#FFFFFF",
            "stroke_width": 2.0,
            "shadow": 3.0,
            "alignment": "center",
            "safe_area": True,
        },
    },
    "song-title": {
        "description": "Compact music credit card.",
        "renderer": "standard",
        "x": "8%",
        "y": "12%",
        "style": {
            "font_size": 46.0,
            "color": "#FFFFFF",
            "stroke_width": 1.0,
            "background": "#101820B3",
            "shadow": 2.0,
            "alignment": "left",
            "safe_area": True,
        },
    },
    "caption-box": {
        "description": "Centered accessible caption box.",
        "renderer": "standard",
        "x": "center",
        "y": "88%",
        "style": {
            "font_size": 48.0,
            "color": "#FFFFFF",
            "stroke_width": 0.0,
            "background": "#000000B3",
            "shadow": 0.0,
            "alignment": "center",
            "safe_area": True,
        },
    },
    "lingang-cinematic-panel": {
        "description": "Lingang cinematic bilingual panel with cyan accent.",
        "renderer": "lingang-panel",
        "x": "5%",
        "y": "71.3%",
        "style": {
            "font_size": 46.0,
            "font_weight": "bold",
            "color": "#FFFFFF",
            "stroke_width": 0.0,
            "shadow": 0.0,
            "alignment": "left",
            "safe_area": True,
        },
        "parameters": {
            "panel_color": "#030A12A4",
            "panel_outline": "#FFFFFF18",
            "accent_color": "#66E1FF",
            "subtitle_color": "#BEDEE8",
            "subtitle_font_size": 25.0,
            "panel_width_percent": 37.5,
            "panel_height_percent": 14.8,
            "corner_radius": 22.0,
        },
    },
    "lingang-cinematic-mint": {
        "description": "Lingang panel with a hopeful mint accent.",
        "inherits": "lingang-cinematic-panel",
        "parameters": {"accent_color": "#74EFCB"},
    },
    "lingang-cinematic-warm": {
        "description": "Lingang panel with a warm human-story accent.",
        "inherits": "lingang-cinematic-panel",
        "parameters": {"accent_color": "#FFC468"},
    },
    "lingang-day-card": {
        "description": "Lingang day/chapter card for a section change.",
        "inherits": "lingang-cinematic-panel",
        "style": {"font_size": 52.0},
        "parameters": {
            "accent_color": "#66E1FF",
            "panel_width_percent": 42.0,
        },
    },
    "lingang-main-title": {
        "description": "Large Lingang opening title with cyan editorial accent.",
        "renderer": "lingang-main-title",
        "x": "8%",
        "y": "30%",
        "style": {
            "font_size": 82.0,
            "font_weight": "bold",
            "color": "#FFFFFF",
            "stroke_width": 0.0,
            "shadow": 0.0,
            "alignment": "left",
            "safe_area": True,
        },
        "parameters": {
            "panel_color": "#020A12A6",
            "panel_outline": "#FFFFFF18",
            "accent_color": "#61E1FF",
            "subtitle_color": "#D8F2F9",
            "subtitle_font_size": 49.0,
            "panel_width_percent": 48.0,
            "panel_height_percent": 28.0,
            "corner_radius": 26.0,
        },
    },
}


def resolve_text_template(name: str) -> dict[str, Any]:
    """Resolve inheritance and return an isolated template dictionary."""

    if name not in TEXT_TEMPLATES:
        raise KeyError(name)
    raw = deepcopy(TEXT_TEMPLATES[name])
    parent_name = raw.pop("inherits", None)
    if parent_name is None:
        return raw
    parent = resolve_text_template(parent_name)
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(parent.get(key), dict):
            parent[key].update(value)
        else:
            parent[key] = value
    parent["name"] = name
    return parent


def list_text_templates() -> dict[str, dict[str, Any]]:
    """Return fully resolved definitions for CLI and Agent discovery."""

    return {name: resolve_text_template(name) for name in TEXT_TEMPLATES}
