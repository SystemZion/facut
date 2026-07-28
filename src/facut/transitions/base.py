"""Transition plugin contracts.

Definitions are intentionally independent from FFmpeg so alternate render
backends can consume the same validated transition parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TransitionValidationError(ValueError):
    """Raised when transition parameters do not match their declaration."""


@dataclass(frozen=True, slots=True)
class TransitionDefinition:
    """A registered transition and its render-backend mapping."""

    name: str
    aliases: tuple[str, ...] = ()
    category: str = "basic"
    default_duration: float = 0.5
    xfade_name: str = "fade"
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    preview_supported: bool = True
    fallback: str | None = "dissolve"

    def validate(
        self, duration: float | None = None, parameters: dict[str, Any] | None = None
    ) -> tuple[float, dict[str, Any]]:
        """Validate and normalize a transition invocation."""

        normalized_duration = (
            self.default_duration if duration is None else float(duration)
        )
        if not 0 < normalized_duration <= 10:
            raise TransitionValidationError(
                "Transition duration must be greater than 0 and at most 10 seconds."
            )
        supplied = dict(parameters or {})
        unknown = set(supplied).difference(self.parameters)
        if unknown:
            raise TransitionValidationError(
                f"Unsupported parameters for {self.name}: {', '.join(sorted(unknown))}"
            )
        for key, rule in self.parameters.items():
            if key not in supplied and "default" in rule:
                supplied[key] = rule["default"]
            if key not in supplied:
                continue
            value = supplied[key]
            if "enum" in rule and value not in rule["enum"]:
                raise TransitionValidationError(
                    f"{key} must be one of: {', '.join(map(str, rule['enum']))}"
                )
            if isinstance(value, (int, float)):
                if "minimum" in rule and value < rule["minimum"]:
                    raise TransitionValidationError(
                        f"{key} must be >= {rule['minimum']}"
                    )
                if "maximum" in rule and value > rule["maximum"]:
                    raise TransitionValidationError(
                        f"{key} must be <= {rule['maximum']}"
                    )
        return normalized_duration, supplied

    def schema(self) -> dict[str, Any]:
        """Return a machine-readable JSON-schema-like declaration."""

        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "category": self.category,
            "default_duration": self.default_duration,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "additionalProperties": False,
            },
            "preview_supported": self.preview_supported,
            "fallback": self.fallback,
        }

    def as_dict(self) -> dict[str, Any]:
        """Compatibility-friendly registry description for CLI output."""

        return self.schema()
