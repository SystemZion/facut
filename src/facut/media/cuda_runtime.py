"""Discover pip-provided NVIDIA DLLs before importing GPU media modules."""

from __future__ import annotations

import os
from pathlib import Path
import site
import sys
from typing import Any


_DLL_HANDLES: list[Any] = []
_CONFIGURED: list[Path] | None = None


def cuda_dll_candidates() -> list[Path]:
    """Return ordered CUDA runtime directories without assuming one Python path."""

    roots: list[Path] = []
    explicit = os.environ.get("FACUT_CUDA_DLL_DIRS", "")
    roots.extend(Path(item).expanduser() for item in explicit.split(os.pathsep) if item.strip())
    try:
        site_roots = [Path(item) for item in site.getsitepackages()]
    except AttributeError:
        site_roots = []
    user_site = site.getusersitepackages()
    if user_site:
        site_roots.append(Path(user_site))
    site_roots.extend(
        [
            Path(sys.prefix) / "Lib" / "site-packages",
            Path(sys.base_prefix) / "Lib" / "site-packages",
        ]
    )
    for root in site_roots:
        roots.extend(
            [
                root / "nvidia" / "cudnn" / "bin",
                root / "nvidia" / "cublas" / "bin",
                root / "nvidia" / "cuda_runtime" / "bin",
            ]
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in roots:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def configure_cuda_dll_directories() -> list[Path]:
    """Expose installed NVIDIA wheel DLLs and retain Windows directory handles."""

    global _CONFIGURED
    if _CONFIGURED is not None:
        return list(_CONFIGURED)
    available = [path for path in cuda_dll_candidates() if path.is_dir()]
    if os.name == "nt":
        for path in available:
            if hasattr(os, "add_dll_directory"):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(path)))
                except OSError:
                    continue
        current = os.environ.get("PATH", "")
        current_keys = {
            os.path.normcase(item.rstrip("\\/"))
            for item in current.split(os.pathsep)
            if item
        }
        additions = [
            str(path)
            for path in available
            if os.path.normcase(str(path).rstrip("\\/")) not in current_keys
        ]
        if additions:
            os.environ["PATH"] = os.pathsep.join([*additions, current])
    _CONFIGURED = available
    return list(available)

