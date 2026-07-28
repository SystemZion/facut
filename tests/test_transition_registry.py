from __future__ import annotations

import pytest

from facut.transitions import TransitionValidationError, registry


def test_basic_transitions_are_registered() -> None:
    names = {item.name for item in registry.list()}
    assert {
        "fade-in",
        "fade-out",
        "dissolve",
        "fade-black",
        "fade-white",
        "slide",
        "wipe",
        "zoom",
        "blur",
    } <= names


def test_transition_validation_and_schema() -> None:
    definition = registry.get("crossfade")
    duration, parameters = definition.validate(
        0.5, {"direction": "right", "intensity": 0.8}
    )
    assert duration == 0.5
    assert parameters["direction"] == "right"
    assert definition.schema()["parameters"]["additionalProperties"] is False
    with pytest.raises(TransitionValidationError):
        definition.validate(0)
    with pytest.raises(TransitionValidationError):
        definition.validate(0.5, {"direction": "diagonal"})
