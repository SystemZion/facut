"""Bundled JSON Schemas."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def schema_path(name: str) -> Path:
    if name not in {"project", "command"}:
        raise ValueError(f'Unknown schema "{name}".')
    return Path(str(files(__package__).joinpath(f"{name}.schema.json")))


__all__ = ["schema_path"]
