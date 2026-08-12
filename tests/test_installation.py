from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from urllib.error import HTTPError

from facut.exceptions import InvalidArgumentError
from facut.installation import _prioritize_path, install_facut, latest_release, update_facut


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


def test_install_path_is_deduplicated_and_prioritized(tmp_path: Path) -> None:
    target = (tmp_path / "facut-bin").resolve()
    entries = [str(tmp_path / "python-scripts"), str(target), str(target) + os.sep]
    updated, changed = _prioritize_path(entries, target)
    assert changed is True
    assert updated == [str(target), str(tmp_path / "python-scripts")]
    same, changed_again = _prioritize_path(updated, target)
    assert same == updated
    assert changed_again is False


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


def test_latest_release_falls_back_when_anonymous_api_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PublicResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self) -> str:
            return "https://github.com/SystemZion/facut/releases/tag/v9.8.7"

    calls = 0

    def fake_urlopen(_request, timeout=20):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("api", 403, "rate limit exceeded", {}, None)
        return PublicResponse()

    monkeypatch.setattr("facut.installation.urlopen", fake_urlopen)
    release = latest_release()

    assert release["latest_version"] == "9.8.7"
    assert release["source"] == "public-release-redirect"
    assert release["asset_url"].endswith("/v9.8.7/facut.exe")

