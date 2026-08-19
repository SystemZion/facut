"""Self-installation and GitHub Release update helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from platformdirs import user_data_path

from facut import __version__
from facut.config import AppConfig, ModelConfig, load_config, save_config
from facut.downloads import MODEL_CATALOG, ModelDownloader
from facut.exceptions import InvalidArgumentError


ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_REPOSITORY = "SystemZion/facut"
NATIVE_LIBRARY_PREFIXES = (
    "avcodec-",
    "avdevice-",
    "avfilter-",
    "avformat-",
    "avutil-",
    "swresample-",
    "swscale-",
)
NATIVE_NOTICE_FILES = {"THIRD_PARTY_NOTICES.md", "FFMPEG_LICENSE.txt"}


def default_install_directory() -> Path:
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or user_data_path("facut", appauthor=False))
        return (local / "Programs" / "facut").resolve()
    return (Path.home() / ".local" / "bin").resolve()


def default_model_directory() -> Path:
    return (user_data_path("facut", appauthor=False) / "models").resolve()


def _executable_name() -> str:
    return "facut.exe" if os.name == "nt" else "facut"


def resolve_install_source(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve()
    else:
        candidate = Path(__file__).resolve().parents[2] / "dist" / _executable_name()
    if not candidate.is_file():
        raise FileNotFoundError(
            f'FACUT executable "{candidate}" was not found. Build the EXE or pass --executable.'
        )
    return candidate


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.expandvars(value.strip()).rstrip("\\/"))


def _prioritize_path(entries: list[str], target: Path) -> tuple[list[str], bool]:
    """Return a de-duplicated PATH with the FACUT directory first."""

    target_key = _path_key(str(target))
    cleaned = [item for item in entries if item.strip() and _path_key(item) != target_key]
    updated = [str(target), *cleaned]
    current_keys = [_path_key(item) for item in entries if item.strip()]
    updated_keys = [_path_key(item) for item in updated]
    return updated, current_keys != updated_keys


def _atomic_copy(source: Path, destination: Path, *, minimum_size: int = 1024 * 1024) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, delete=False, suffix=".installing"
    ) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        if temporary.stat().st_size <= minimum_size:
            raise ValueError(f'Install source "{source.name}" is unexpectedly small.')
        last_error: PermissionError | None = None
        for _ in range(6):
            try:
                os.replace(temporary, destination)
                last_error = None
                break
            except PermissionError as error:
                last_error = error
                time.sleep(0.1)
        if last_error is not None:
            # Windows antivirus can briefly retain an executable handle. A
            # final exact-target delete/retry keeps the operation predictable.
            destination.unlink(missing_ok=True)
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _native_bundle_files(executable: Path) -> list[Path]:
    """Return only the allowlisted native sidecar files beside an EXE."""

    root = executable.parent
    sidecar = root / ("facut-native.exe" if os.name == "nt" else "facut-native")
    if not sidecar.is_file():
        return []
    files = [sidecar]
    if os.name == "nt":
        dlls = [
            item
            for item in root.glob("*.dll")
            if item.name.casefold().startswith(NATIVE_LIBRARY_PREFIXES)
        ]
        present = {item.name.casefold().split("-", 1)[0] for item in dlls}
        required = {prefix[:-1] for prefix in NATIVE_LIBRARY_PREFIXES}
        missing = sorted(required - present)
        if missing:
            raise FileNotFoundError(
                "FACUT Native bundle is incomplete; missing shared libraries: "
                + ", ".join(missing)
            )
        files.extend(sorted(dlls, key=lambda item: item.name.casefold()))
    files.extend(
        root / name for name in sorted(NATIVE_NOTICE_FILES) if (root / name).is_file()
    )
    return files


def _install_native_bundle(source_executable: Path, destination: Path) -> dict[str, Any]:
    files = _native_bundle_files(source_executable)
    if not files:
        return {
            "requested": True,
            "installed": False,
            "reason": "No adjacent facut-native sidecar was found; Python/FFmpeg fallback remains available.",
            "files": [],
        }
    installed = []
    for source in files:
        target = destination / source.name
        _atomic_copy(source, target, minimum_size=0)
        installed.append(source.name)
    return {
        "requested": True,
        "installed": True,
        "executable": str(destination / files[0].name),
        "files": installed,
    }


def add_to_user_path(directory: str | Path) -> dict[str, Any]:
    target = Path(directory).expanduser().resolve()
    if os.name != "nt":
        current = [Path(item).expanduser().resolve() for item in os.environ.get("PATH", "").split(os.pathsep) if item]
        return {
            "changed": False,
            "available_now": target in current,
            "directory": str(target),
            "warning": f'Add "{target}" to PATH in your shell profile.',
        }

    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        entries = [item for item in str(current).split(";") if item.strip()]
        entries, changed = _prioritize_path(entries, target)
        if changed:
            winreg.SetValueEx(key, "Path", 0, value_type, ";".join(entries))
    process_entries = [item for item in os.environ.get("PATH", "").split(";") if item]
    process_entries, process_changed = _prioritize_path(process_entries, target)
    if process_changed:
        os.environ["PATH"] = ";".join(process_entries)
    if changed:
        try:
            HWND_BROADCAST, WM_SETTINGCHANGE = 0xFFFF, 0x001A
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 0x0002, 5000, None
            )
        except (AttributeError, OSError):
            pass
    return {
        "changed": changed,
        "available_now": True,
        "directory": str(target),
        "precedence": "first",
    }


def _normalize_exclusions(exclude: list[str]) -> set[str]:
    normalized = {item.strip().lower().replace("-", "_") for item in exclude}
    allowed = {"model", "models", *MODEL_CATALOG}
    unknown = sorted(normalized - allowed)
    if unknown:
        raise InvalidArgumentError(
            f'Unknown install exclusion "{unknown[0]}".',
            suggestion=f"Choose from: model, {', '.join(MODEL_CATALOG)}.",
        )
    if normalized & {"model", "models"}:
        normalized.update(MODEL_CATALOG)
        normalized.difference_update({"model", "models"})
    return normalized


def install_facut(
    *,
    executable: str | Path | None = None,
    directory: str | Path | None = None,
    model_directory: str | Path | None = None,
    exclude: list[str] | None = None,
    add_path: bool = True,
    native: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    source = resolve_install_source(executable)
    install_directory = Path(directory).expanduser().resolve() if directory else default_install_directory()
    destination = install_directory / _executable_name()
    previous_manifest_path = install_directory / "install.json"
    previous_manifest = (
        json.loads(previous_manifest_path.read_text(encoding="utf-8"))
        if previous_manifest_path.is_file()
        else {}
    )
    replaced = destination.exists() and not _same_path(source, destination)
    if not _same_path(source, destination):
        _atomic_copy(source, destination)
    native_result = (
        _install_native_bundle(source, install_directory)
        if native
        else {"requested": False, "installed": False, "files": []}
    )
    exclusions = _normalize_exclusions(exclude or [])

    config = load_config()
    models_root = (
        Path(model_directory).expanduser().resolve()
        if model_directory is not None
        else Path(config.models.directory).expanduser().resolve()
    )
    if model_directory is not None:
        config_values = config.model_dump()
        config_values["models"]["directory"] = models_root
        config = AppConfig.model_validate(config_values)
        save_config(config)

    model_results = []
    for name, package in MODEL_CATALOG.items():
        if name in exclusions:
            continue
        model_results.append(ModelDownloader(package, models_root, progress=progress).download())

    path_result = add_to_user_path(install_directory) if add_path else {
        "changed": False,
        "available_now": False,
        "directory": str(install_directory),
        "skipped": True,
    }
    manifest = {
        "version": __version__,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "executable": str(destination),
        "models_directory": str(models_root),
        "models": [item["model"] for item in model_results],
        "native": native_result,
    }
    manifest_path = install_directory / "install.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    current_native_files = set(native_result.get("files") or [])
    for stale_name in (previous_manifest.get("native") or {}).get("files", []):
        if stale_name not in current_native_files:
            stale = install_directory / Path(str(stale_name)).name
            if stale.parent == install_directory and stale.name != destination.name:
                stale.unlink(missing_ok=True)
    for stale in install_directory.glob("facut.exe.update-*"):
        stale.unlink(missing_ok=True)
    return {
        **manifest,
        "source": str(source),
        "replaced_old_version": replaced,
        "path": path_result,
        "model_results": model_results,
        "excluded": sorted(exclusions),
        "manifest": str(manifest_path),
    }


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(item) for item in numbers[:4]) or (0,)


def latest_release(repository: str = DEFAULT_REPOSITORY) -> dict[str, Any]:
    api_request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"facut/{__version__}"},
    )
    try:
        with urlopen(api_request, timeout=20) as response:
            payload = json.load(response)
        asset = next(
            (
                item
                for item in payload.get("assets", [])
                if str(item.get("name", "")).lower() == "facut.exe"
            ),
            None,
        )
        latest_version = str(payload.get("tag_name") or "").lstrip("v")
        release_url = payload.get("html_url")
        asset_url = asset.get("browser_download_url") if asset else None
        asset_size = asset.get("size") if asset else None
        source = "github-api"
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        # GitHub's anonymous REST quota is small and shared by some networks.
        # The public /releases/latest redirect is not API-rate-limited and its
        # final URL contains the canonical tag. Release assets have stable URLs.
        public_request = Request(
            f"https://github.com/{repository}/releases/latest",
            headers={"User-Agent": f"facut/{__version__}"},
        )
        with urlopen(public_request, timeout=20) as response:
            release_url = response.geturl()
        match = re.search(r"/releases/tag/v?([^/?#]+)", release_url)
        if match is None:
            raise ValueError("GitHub did not return a recognizable latest Release tag.")
        latest_version = match.group(1)
        tag = f"v{latest_version}"
        asset_url = f"https://github.com/{repository}/releases/download/{tag}/facut.exe"
        asset_size = None
        source = "public-release-redirect"
    return {
        "repository": repository,
        "current_version": __version__,
        "latest_version": latest_version,
        "release_url": release_url,
        "asset_url": asset_url,
        "asset_size": asset_size,
        "source": source,
        "update_available": _version_tuple(latest_version) > _version_tuple(__version__),
    }


def _download_update(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": f"facut/{__version__}"}
    if start:
        headers["Range"] = f"bytes={start}-"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        if start and response.status != 206:
            part.unlink(missing_ok=True)
            start = 0
            return _download_update(url, destination)
        with part.open("ab" if start else "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
    if part.stat().st_size <= 1024 * 1024 or part.read_bytes()[:2] != b"MZ":
        raise ValueError("Downloaded GitHub Release asset is not a valid FACUT executable.")
    os.replace(part, destination)
    return destination


def _schedule_running_executable_replacement(source: Path, destination: Path) -> Path:
    helper = destination.parent / f"facut-update-{os.getpid()}.ps1"
    helper.write_text(
        "param([int]$ProcessId,[string]$Source,[string]$Destination,[string]$Helper)\n"
        "Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue\n"
        "Move-Item -LiteralPath $Source -Destination $Destination -Force\n"
        "Remove-Item -LiteralPath $Helper -Force -ErrorAction SilentlyContinue\n",
        encoding="utf-8-sig",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(helper), "-ProcessId", str(os.getpid()), "-Source", str(source),
            "-Destination", str(destination), "-Helper", str(helper),
        ],
        close_fds=True,
        creationflags=flags,
    )
    return helper


def update_facut(
    *,
    repository: str = DEFAULT_REPOSITORY,
    check_only: bool = False,
    force: bool = False,
    from_file: str | Path | None = None,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    install_directory = (
        Path(directory).expanduser().resolve()
        if directory is not None
        else default_install_directory()
    )
    manifest_path = install_directory / "install.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        destination = Path(manifest.get("executable") or install_directory / _executable_name()).resolve()
    else:
        destination = (install_directory / _executable_name()).resolve()

    release = latest_release(repository) if from_file is None else {
        "repository": repository,
        "current_version": __version__,
        "latest_version": "local",
        "release_url": None,
        "asset_url": None,
        "asset_size": Path(from_file).expanduser().resolve().stat().st_size,
        "update_available": True,
    }
    if check_only or (not release["update_available"] and not force):
        return {**release, "updated": False, "check_only": check_only}
    if from_file is not None:
        staged = destination.with_name(f"{destination.name}.update-{os.getpid()}")
        _atomic_copy(Path(from_file).expanduser().resolve(), staged)
    else:
        if not release.get("asset_url"):
            raise FileNotFoundError("The latest GitHub Release has no facut.exe asset.")
        staged = _download_update(
            str(release["asset_url"]), destination.with_name(f"{destination.name}.update-{os.getpid()}")
        )

    running = getattr(sys, "frozen", False) and _same_path(Path(sys.executable), destination)
    if running and os.name == "nt":
        helper = _schedule_running_executable_replacement(staged, destination)
        mode = "scheduled-after-exit"
    else:
        _atomic_copy(staged, destination)
        staged.unlink(missing_ok=True)
        helper = None
        mode = "replaced"
    return {
        **release,
        "updated": True,
        "destination": str(destination),
        "mode": mode,
        "helper": str(helper) if helper else None,
    }
