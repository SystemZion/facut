"""Built-in video effects."""

from __future__ import annotations

from .base import EffectDefinition


def builtins() -> tuple[EffectDefinition, ...]:
    return (
        EffectDefinition(
            "brightness",
            "eq=brightness={value}",
            {"value": {"type": "number", "minimum": -1, "maximum": 1}},
        ),
        EffectDefinition(
            "contrast",
            "eq=contrast={value}",
            {"value": {"type": "number", "minimum": -2, "maximum": 2}},
        ),
        EffectDefinition(
            "saturation",
            "eq=saturation={value}",
            {"value": {"type": "number", "minimum": 0, "maximum": 3}},
        ),
        EffectDefinition(
            "gamma",
            "eq=gamma={value}",
            {"value": {"type": "number", "minimum": 0.1, "maximum": 10}},
        ),
        EffectDefinition(
            "blur",
            "gblur=sigma={radius}",
            {"radius": {"type": "number", "minimum": 0, "maximum": 100}},
        ),
        EffectDefinition(
            "sharpen",
            "unsharp=5:5:{amount}",
            {"amount": {"type": "number", "minimum": -1.5, "maximum": 1.5}},
        ),
        EffectDefinition("black-white", "hue=s=0"),
        EffectDefinition(
            "vignette",
            "vignette=PI/{angle}",
            {"angle": {"type": "number", "minimum": 2, "maximum": 12, "default": 5}},
        ),
        EffectDefinition(
            "pixelate",
            "scale=iw/{size}:ih/{size}:flags=neighbor,scale=iw*{size}:ih*{size}:flags=neighbor",
            {"size": {"type": "integer", "minimum": 2, "maximum": 64, "default": 12}},
        ),
        EffectDefinition(
            "lut",
            "lut3d=file='{file}'",
            {"file": {"type": "string", "required": True}},
        ),
    )
