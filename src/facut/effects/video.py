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
        EffectDefinition(
            "punch-zoom",
            "scale=iw*(1+{intensity}):ih*(1+{intensity}),"
            "crop=iw/(1+{intensity}):ih/(1+{intensity})",
            {
                "intensity": {
                    "type": "number",
                    "minimum": 0.02,
                    "maximum": 0.5,
                    "default": 0.12,
                }
            },
            category="vlog",
        ),
        EffectDefinition(
            "micro-shake",
            "scale=iw+{pixels}*2:ih+{pixels}*2,"
            "crop=iw-{pixels}*2:ih-{pixels}*2:"
            "x={pixels}+sin(t*37)*{pixels}:y={pixels}+cos(t*41)*{pixels}",
            {
                "pixels": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 24,
                    "default": 6,
                }
            },
            category="vlog",
        ),
        EffectDefinition(
            "red-highlight",
            "drawbox=x={x}:y={y}:w={width}:h={height}:color=red@{opacity}:t={thickness}",
            {
                "x": {"type": "number", "minimum": 0, "required": True},
                "y": {"type": "number", "minimum": 0, "required": True},
                "width": {"type": "number", "minimum": 1, "required": True},
                "height": {"type": "number", "minimum": 1, "required": True},
                "opacity": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 1,
                    "default": 0.85,
                },
                "thickness": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 8,
                },
            },
            category="vlog",
        ),
        EffectDefinition(
            "comic-impact",
            "eq=contrast={contrast}:saturation={saturation},vignette=PI/{vignette}",
            {
                "contrast": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 2,
                    "default": 1.25,
                },
                "saturation": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 3,
                    "default": 1.35,
                },
                "vignette": {
                    "type": "number",
                    "minimum": 2,
                    "maximum": 12,
                    "default": 4,
                },
            },
            category="vlog",
        ),
    )
