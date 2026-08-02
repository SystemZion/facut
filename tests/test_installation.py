from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from facut.exceptions import InvalidArgumentError
from facut.installation import install_facut, update_facut


def _installed_executable(directory: Path) -> Path:
    return directory / ("facut.exe" if os.name == "nt" else "facut")


def _fake_executable(path: Path, marker: bytes = b"A") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ" + marker * (1024 * 1024 + 31))
    return path


def test_install_replaces_executable_without_downloading_models(tmp_path: Path) -> None:
    source = _fake_executable(tmp_path / "source" / "facut.exe", b"A")
    install_dir = tmp_path / "installed"

    first = install_facut(
        executable=source,
        directory=install_dir,
        exclude=["model"],
        add_path=False,
    )
    assert Path(first["executable"]).read_bytes() == source.read_bytes()
    assert first["model_results"] == []
    assert first["excluded"] == ["srt_model", "voice_model"]
    assert first["path"]["skipped"] is True
    assert json.loads((install_dir / "install.json").read_text(encoding="utf-8"))["version"]

    replacement = _fake_executable(tmp_path / "replacement" / "facut.exe", b"B")
    second = install_facut(
        executable=replacement,
        directory=install_dir,
        exclude=["models"],
        add_path=False,
    )
    assert second["replaced_old_version"] is True
    assert _installed_executable(install_dir).read_bytes() == replacement.read_bytes()


def test_install_rejects_unknown_exclusion(tmp_path: Path) -> None:
    source = _fake_executable(tmp_path / "facut.exe")
    with pytest.raises(InvalidArgumentError):
        install_facut(
            executable=source,
            directory=tmp_path / "installed",
            exclude=["mystery"],
            add_path=False,
        )


def test_offline_update_replaces_isolated_install(tmp_path: Path) -> None:
    install_dir = tmp_path / "installed"
    old = _fake_executable(_installed_executable(install_dir), b"O")
    update = _fake_executable(tmp_path / "new-facut.exe", b"N")
    assert old.read_bytes() != update.read_bytes()

    result = update_facut(
        from_file=update,
        force=True,
        directory=install_dir,
    )

    assert result["updated"] is True
    assert result["mode"] == "replaced"
    assert _installed_executable(install_dir).read_bytes() == update.read_bytes()

