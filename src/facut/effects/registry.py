"""Effect registry."""

from __future__ import annotations

from .base import EffectDefinition
from .video import builtins


class EffectRegistry:
    def __init__(self) -> None:
        self._items: dict[str, EffectDefinition] = {}

    def register(self, definition: EffectDefinition) -> None:
        if definition.name in self._items:
            raise ValueError(f"Effect already registered: {definition.name}")
        self._items[definition.name] = definition

    def get(self, name: str) -> EffectDefinition:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Unknown effect: {name}") from exc

    def list(self) -> list[EffectDefinition]:
        return sorted(self._items.values(), key=lambda item: item.name)


registry = EffectRegistry()
for _effect in builtins():
    registry.register(_effect)
