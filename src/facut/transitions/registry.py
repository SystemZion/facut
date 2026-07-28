"""Transition registry."""

from __future__ import annotations

from collections.abc import Iterable

from .base import TransitionDefinition
from .basic import builtins


class TransitionRegistry:
    """Register and resolve transition plugins by canonical name or alias."""

    def __init__(self, definitions: Iterable[TransitionDefinition] = ()) -> None:
        self._items: dict[str, TransitionDefinition] = {}
        self._aliases: dict[str, str] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: TransitionDefinition) -> None:
        if definition.name in self._items:
            raise ValueError(f"Transition already registered: {definition.name}")
        self._items[definition.name] = definition
        for alias in definition.aliases:
            if alias in self._aliases or alias in self._items:
                raise ValueError(f"Transition alias already registered: {alias}")
            self._aliases[alias] = definition.name

    def get(self, name: str) -> TransitionDefinition:
        canonical = self._aliases.get(name, name)
        try:
            return self._items[canonical]
        except KeyError as exc:
            raise KeyError(f"Unknown transition: {name}") from exc

    def list(self) -> list[TransitionDefinition]:
        return sorted(self._items.values(), key=lambda item: item.name)


registry = TransitionRegistry(builtins())
