"""Effect plugin contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    """Declarative effect that compiles to an FFmpeg video filter."""

    name: str
    ffmpeg_filter: str
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    category: str = "video"

    def compile(self, values: dict[str, Any] | None = None) -> str:
        """Validate parameter names and build the filter expression."""

        supplied = dict(values or {})
        unknown = set(supplied).difference(self.parameters)
        if unknown:
            raise ValueError(
                f"Unsupported parameters for {self.name}: {', '.join(sorted(unknown))}"
            )
        normalized: dict[str, Any] = {}
        for key, rule in self.parameters.items():
            if key in supplied:
                value = supplied[key]
            elif "default" in rule:
                value = rule["default"]
            elif rule.get("required"):
                raise ValueError(f"Missing required effect parameter: {key}")
            else:
                continue
            if isinstance(value, (int, float)):
                if "minimum" in rule and value < rule["minimum"]:
                    raise ValueError(f"{key} must be >= {rule['minimum']}")
                if "maximum" in rule and value > rule["maximum"]:
                    raise ValueError(f"{key} must be <= {rule['maximum']}")
            normalized[key] = value
        return self.ffmpeg_filter.format(**normalized)
