"""Executable VLOG polish registry coverage."""

from facut.effects.registry import registry as effect_registry
from facut.transitions.registry import registry as transition_registry


def test_deterministic_vlog_effects_compile_to_real_filters() -> None:
    zoom = effect_registry.get("punch-zoom")
    assert zoom.category == "vlog"
    assert "scale=" in zoom.compile({"intensity": 0.15})

    shake = effect_registry.get("micro-shake")
    assert "sin(t*37)" in shake.compile({"pixels": 4})

    highlight = effect_registry.get("red-highlight")
    compiled = highlight.compile({"x": 10, "y": 20, "width": 100, "height": 80})
    assert "drawbox=" in compiled

    impact = effect_registry.get("comic-impact")
    assert "vignette=" in impact.compile()


def test_vlog_transitions_have_real_backend_mappings() -> None:
    restrained = transition_registry.get("restrained-dissolve")
    movement = transition_registry.get("movement-match")
    assert restrained.xfade_name == "fade"
    assert movement.xfade_name == "slideleft"
    assert restrained.category == movement.category == "vlog"
