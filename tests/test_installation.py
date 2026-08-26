from __future__ import annotations

import json
import os
from pathlib import Path
import zipfile

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


def test_install_copies_portable_runtime_directory(tmp_path: Path) -> None:
    source = _fake_executable(tmp_path / "bundle" / "facut.exe")
    runtime = source.parent / "facut_runtime"
    runtime.mkdir()
    (runtime / "python311.dll").write_bytes(b"runtime")

    result = install_facut(
        executable=source,
        directory=tmp_path / "installed",
        exclude=["model"],
        add_path=False,
        native=False,
    )

    assert result["runtime"]["installed"] is True
    assert (tmp_path / "installed" / "facut_runtime" / "python311.dll").read_bytes() == b"runtime"


@pytest.mark.skipif(os.name != "nt", reason="Windows portable bundle semantics")
def test_update_replaces_complete_portable_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    _fake_executable(install_dir / "facut.exe", b"A")
    (install_dir / "facut_runtime").mkdir()
    (install_dir / "facut_runtime" / "old.dll").write_bytes(b"old")
    (install_dir / "install.json").write_text(
        json.dumps({"version": "old", "executable": str(install_dir / "facut.exe")}),
        encoding="utf-8",
    )
    source = tmp_path / "source"
    source.mkdir()
    _fake_executable(source / "facut.exe", b"B")
    (source / "facut_runtime").mkdir()
    (source / "facut_runtime" / "new.dll").write_bytes(b"new")
    archive = tmp_path / "facut-windows-x64.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for item in source.rglob("*"):
            if item.is_file():
                package.write(item, item.relative_to(source).as_posix())

    result = update_facut(from_file=archive, directory=install_dir)

    assert result["mode"] == "replaced-bundle"
    assert result["bundle"] is True
    assert (install_dir / "facut_runtime" / "new.dll").read_bytes() == b"new"
    assert not (install_dir / "facut_runtime" / "old.dll").exists()
    assert json.loads((install_dir / "install.json").read_text("utf-8"))["version"] == "local"


@pytest.mark.skipif(os.name != "nt", reason="Windows portable bundle semantics")
def test_portable_update_refuses_a_directory_with_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    install_dir = tmp_path / "shared-tools"
    install_dir.mkdir()
    _fake_executable(install_dir / "facut.exe")
    (install_dir / "unrelated.txt").write_text("keep", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    _fake_executable(source / "facut.exe", b"B")
    archive = tmp_path / "facut-windows-x64.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(source / "facut.exe", "facut.exe")

    with pytest.raises(InvalidArgumentError, match="not owned by FACUT"):
        update_facut(from_file=archive, directory=install_dir)
    assert (install_dir / "unrelated.txt").read_text("utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows native sidecar bundle")
def test_install_copies_complete_adjacent_native_bundle(tmp_path: Path) -> None:
    source = _fake_executable(tmp_path / "bundle" / "facut.exe")
    (source.parent / "facut-native.exe").write_bytes(b"native")
    dll_names = [
        "avcodec-62.dll", "avdevice-62.dll", "avfilter-11.dll",
        "avformat-62.dll", "avutil-60.dll", "swresample-6.dll", "swscale-9.dll",
    ]
    for name in dll_names:
        (source.parent / name).write_bytes(name.encode("ascii"))
    (source.parent / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")

    result = install_facut(
        executable=source,
        directory=tmp_path / "installed",
        exclude=["model"],
        add_path=False,
    )

    assert result["native"]["installed"] is True
    assert (tmp_path / "installed" / "facut-native.exe").read_bytes() == b"native"
    assert set(dll_names).issubset(result["native"]["files"])


def test_install_without_adjacent_native_bundle_keeps_python_fallback(tmp_path: Path) -> None:
    source = _fake_executable(tmp_path / "bundle" / "facut.exe")
    result = install_facut(
        executable=source,
        directory=tmp_path / "installed",
        exclude=["model"],
        add_path=False,
    )
    assert result["native"]["installed"] is False
    assert "fallback" in result["native"]["reason"]


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


def test_offline_update_replaces_isolated_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
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


def test_online_legacy_single_file_update_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    install_dir = tmp_path / "installed"
    _fake_executable(_installed_executable(install_dir), b"O")
    replacement = _fake_executable(tmp_path / "release.exe", b"N")
    monkeypatch.setattr(
        "facut.installation.latest_release",
        lambda repository: {
            "repository": repository,
            "current_version": "0.8.3",
            "latest_version": "0.8.4",
            "release_url": "https://example.invalid/release",
            "asset_url": "https://example.invalid/facut.exe",
            "asset_name": "facut.exe",
            "asset_size": replacement.stat().st_size,
            "source": "test",
            "update_available": True,
        },
    )

    def fake_download(_url, destination, *, archive=False):
        assert archive is False
        destination.write_bytes(replacement.read_bytes())
        return destination

    monkeypatch.setattr("facut.installation._download_update", fake_download)
    result = update_facut(directory=install_dir)
    assert result["mode"] == "replaced"
    assert _installed_executable(install_dir).read_bytes() == replacement.read_bytes()


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
    expected = "facut-windows-x64.zip" if os.name == "nt" else "facut"
    assert release["asset_url"].endswith(f"/v9.8.7/{expected}")

