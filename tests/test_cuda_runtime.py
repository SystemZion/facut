from __future__ import annotations

import os
from pathlib import Path

from facut.media import cuda_runtime


def test_cuda_candidates_include_explicit_directories(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "cudnn"
    second = tmp_path / "cublas"
    monkeypatch.setenv("FACUT_CUDA_DLL_DIRS", os.pathsep.join([str(first), str(second)]))

    candidates = cuda_runtime.cuda_dll_candidates()

    assert first.resolve() in candidates
    assert second.resolve() in candidates


def test_cuda_configuration_reports_existing_explicit_directory(monkeypatch, tmp_path: Path) -> None:
    directory = tmp_path / "cuda-bin"
    directory.mkdir()
    monkeypatch.setenv("FACUT_CUDA_DLL_DIRS", str(directory))
    monkeypatch.setattr(cuda_runtime, "_CONFIGURED", None)

    configured = cuda_runtime.configure_cuda_dll_directories()

    assert directory.resolve() in configured
