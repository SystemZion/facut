"""Plugin-based transition system."""

from .base import TransitionDefinition, TransitionValidationError
from .registry import TransitionRegistry, registry

__all__ = [
    "TransitionDefinition",
    "TransitionRegistry",
    "TransitionValidationError",
    "registry",
]
