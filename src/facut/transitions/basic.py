"""Built-in first-version transitions."""

from __future__ import annotations

from .base import TransitionDefinition


DIRECTIONS = [
    "left",
    "right",
    "up",
    "down",
    "in",
    "out",
]
EASINGS = ["linear", "ease-in", "ease-out", "ease-in-out", "cubic"]


def builtins() -> tuple[TransitionDefinition, ...]:
    """Return transition definitions supported by the FFmpeg backend."""

    common = {
        "direction": {"type": "string", "enum": DIRECTIONS, "default": "left"},
        "easing": {"type": "string", "enum": EASINGS, "default": "linear"},
        "intensity": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "default": 1,
        },
    }
    return (
        TransitionDefinition(
            "fade-in", category="basic", xfade_name="fade", fallback=None
        ),
        TransitionDefinition(
            "fade-out", category="basic", xfade_name="fade", fallback=None
        ),
        TransitionDefinition(
            "dissolve", ("crossfade",), "basic", 0.5, "fade", common
        ),
        TransitionDefinition(
            "cross-dissolve", (), "basic", 0.5, "fade", common
        ),
        TransitionDefinition(
            "fade-black", (), "basic", 0.6, "fadeblack", common
        ),
        TransitionDefinition(
            "fade-white", ("flash-white",), "basic", 0.4, "fadewhite", common
        ),
        TransitionDefinition(
            "slide", ("push",), "basic", 0.5, "slideleft", common
        ),
        TransitionDefinition(
            "wipe", (), "basic", 0.5, "wipeleft", common
        ),
        TransitionDefinition(
            "zoom", (), "basic", 0.45, "zoomin", common
        ),
        # FFmpeg xfade has no blur primitive. A smooth dissolve is the
        # documented deterministic fallback, not a fake unsupported filter.
        TransitionDefinition(
            "blur", ("blur-transition",), "basic", 0.5, "fade", common
        ),
        TransitionDefinition(
            "restrained-dissolve", (), "vlog", 0.45, "fade", common
        ),
        TransitionDefinition(
            "movement-match", (), "vlog", 0.3, "slideleft", common
        ),
    )
