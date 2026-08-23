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
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from platformdirs import user_data_path

from facut import __version__
from facut.config import AppConfig, load_config, save_config
from facut.downloads import MODEL_CATALOG, ModelDownloader
from facut.exceptions import InvalidArgumentError


ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_REPOSITORY = "SystemZion/facut"
WINDOWS_PORTABLE_ASSET = "facut-windows-x64.zip"
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


def _atomic_replace_directory(source: Path, destination: Path) -> None:
    """Replace one owned runtime directory and restore the previous copy on failure."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.installing-{os.getpid()}")
    backup = destination.with_name(f".{destination.name}.backup-{os.getpid()}")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(source, staged)
    try:
        if destination.exists():
            os.replace(destination, backup)
        os.replace(staged, destination)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _install_python_runtime(source_executable: Path, install_directory: Path) -> dict[str, Any]:
    runtime = source_executable.parent / "facut_runtime"
    destination = install_directory / "facut_runtime"
    if not runtime.is_dir():
        return {"installed": False, "mode": "single-file", "files": 0}
    _atomic_replace_directory(runtime, destination)
    return {
        "installed": True,
        "mode": "portable-directory",
        "directory": str(destination),
        "files": sum(1 for item in destination.rglob("*") if item.is_file()),
    }


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
    build_identity: dict[str, Any] | None = None
    source_build_manifest = source.parent / "facut-build.json"
    if source_build_manifest.is_file():
        build_identity = json.loads(source_build_manifest.read_text(encoding="utf-8-sig"))
        build_manifest_destination = install_directory / "facut-build.json"
        _atomic_copy(source_build_manifest, build_manifest_destination, minimum_size=0)
    runtime_result = _install_python_runtime(source, install_directory)
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
        "runtime": runtime_result,
        "build_identity": build_identity,
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
    if (
        not runtime_result["installed"]
        and (previous_manifest.get("runtime") or {}).get("installed")
    ):
        shutil.rmtree(install_directory / "facut_runtime", ignore_errors=True)
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
        preferred_assets = (
            (WINDOWS_PORTABLE_ASSET, "facut.exe") if os.name == "nt" else ("facut",)
        )
        assets = {
            str(item.get("name", "")).casefold(): item for item in payload.get("assets", [])
        }
        asset = next((assets.get(name.casefold()) for name in preferred_assets if assets.get(name.casefold())), None)
        release_manifest_asset = assets.get("facut-release.json")
        release_manifest = None
        if release_manifest_asset and release_manifest_asset.get("browser_download_url"):
            manifest_request = Request(
                str(release_manifest_asset["browser_download_url"]),
                headers={"User-Agent": f"facut/{__version__}"},
            )
            with urlopen(manifest_request, timeout=20) as response:
                release_manifest = json.load(response)
        latest_version = str(payload.get("tag_name") or "").lstrip("v")
        release_url = payload.get("html_url")
        asset_url = asset.get("browser_download_url") if asset else None
        asset_size = asset.get("size") if asset else None
        asset_name = asset.get("name") if asset else None
        asset_digest = asset.get("digest") if asset else None
        release_commitish = payload.get("target_commitish")
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
        asset_name = WINDOWS_PORTABLE_ASSET if os.name == "nt" else "facut"
        asset_url = f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"
        asset_size = None
        asset_digest = None
        release_commitish = None
        release_manifest = None
        source = "public-release-redirect"
    return {
        "repository": repository,
        "current_version": __version__,
        "latest_version": latest_version,
        "release_url": release_url,
        "asset_url": asset_url,
        "asset_size": asset_size,
        "asset_name": asset_name,
        "asset_digest": asset_digest,
        "release_commitish": release_commitish,
        "release_manifest": release_manifest,
        "source": source,
        "update_available": _version_tuple(latest_version) > _version_tuple(__version__),
    }


def release_publish_check(
    identity: dict[str, Any], repository: str = DEFAULT_REPOSITORY
) -> dict[str, Any]:
    """Verify that the declared build is actually obtainable as latest Release."""

    release = latest_release(repository)
    expected_version = str(identity.get("version") or __version__)
    expected_commit = str(identity.get("commit") or "")
    digest = str(release.get("asset_digest") or "")
    if digest.casefold().startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    release_manifest = release.get("release_manifest") or {}
    manifest_archive_sha = str(release_manifest.get("archive_sha256") or "").casefold()
    checks = {
        "version_matches": release["latest_version"] == expected_version,
        "asset_available": bool(release.get("asset_url")),
        "release_manifest_available": bool(release_manifest),
        "asset_digest_matches_manifest": bool(
            digest and manifest_archive_sha and digest.casefold() == manifest_archive_sha
        ),
        "commit_declared": bool(expected_commit and expected_commit != "unknown"),
        "commit_matches": bool(
            expected_commit
            and release_manifest.get("commit")
            and expected_commit == str(release_manifest["commit"])
        ),
        "manifest_version_matches": release_manifest.get("version") == expected_version,
    }
    passed = all(checks.values())
    return {
        "status": "pass" if passed else "blocked",
        "checks": checks,
        "identity": identity,
        "release": release,
        "hard_fail": not passed,
    }


def _download_update(url: str, destination: Path, *, archive: bool = False) -> Path:
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
            return _download_update(url, destination, archive=archive)
        with part.open("ab" if start else "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
    if part.stat().st_size <= 1024 * 1024:
        raise ValueError("Downloaded GitHub Release asset is unexpectedly small.")
    if archive:
        if not zipfile.is_zipfile(part):
            raise ValueError("Downloaded GitHub Release asset is not a valid FACUT ZIP archive.")
    elif part.read_bytes()[:2] != b"MZ":
        raise ValueError("Downloaded GitHub Release asset is not a valid FACUT executable.")
    os.replace(part, destination)
    return destination


def _extract_portable_archive(archive: Path, destination: Path) -> Path:
    """Safely extract a portable release and return its bundle root."""

    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 20_000 or sum(item.file_size for item in members) > 2 * 1024**3:
            raise ValueError("Portable release exceeds the safe extraction limit.")
        for member in members:
            member_path = Path(member.filename)
            unix_mode = member.external_attr >> 16
            if (
                member_path.is_absolute()
                or member_path.drive
                or ".." in member_path.parts
                or any(":" in part for part in member_path.parts)
                or (unix_mode & 0o170000) == 0o120000
            ):
                raise ValueError("Portable release contains an unsafe path or link.")
            target = (root / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise ValueError("Portable release contains an unsafe path.") from error
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    candidates = [root, *[item for item in root.iterdir() if item.is_dir()]]
    bundle = next((item for item in candidates if (item / _executable_name()).is_file()), None)
    if bundle is None:
        raise ValueError(f'Portable release does not contain "{_executable_name()}".')
    executable = bundle / _executable_name()
    if executable.stat().st_size <= 1024 * 1024 or executable.read_bytes()[:2] != b"MZ":
        raise ValueError("Portable release contains an invalid FACUT executable.")
    return bundle


def _assert_owned_install_directory(destination: Path) -> None:
    """Refuse whole-directory replacement when unrelated user files are present."""

    resolved = destination.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise InvalidArgumentError("Refusing to replace a broad install directory.")
    if (resolved / "install.json").is_file() or not resolved.exists():
        return
    allowed_names = {
        _executable_name(),
        "facut_runtime",
        "facut-native.exe",
        "facut-native",
        *NATIVE_NOTICE_FILES,
    }
    unknown = sorted(
        item.name
        for item in resolved.iterdir()
        if item.name not in allowed_names
        and not item.name.casefold().startswith(NATIVE_LIBRARY_PREFIXES)
    )
    if unknown:
        raise InvalidArgumentError(
            "Refusing to replace a directory that contains files not owned by FACUT.",
            suggestion="Install FACUT into its own directory, then retry the update.",
            details={"directory": str(resolved), "unexpected": unknown[:20]},
        )


def _replace_install_bundle(source: Path, destination: Path) -> Path | None:
    """Replace the FACUT-owned install directory while preserving rollback."""

    parent = destination.parent
    backup = parent / f".{destination.name}.backup-{os.getpid()}"
    shutil.rmtree(backup, ignore_errors=True)
    try:
        if destination.exists():
            os.replace(destination, backup)
        os.replace(source, destination)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)
    return None


def _schedule_running_bundle_replacement(source: Path, destination: Path) -> Path:
    helper = destination.parent / f"facut-update-{os.getpid()}.ps1"
    backup = destination.parent / f".{destination.name}.backup-update"
    helper.write_text(
        "param([int]$ProcessId,[string]$Source,[string]$Destination,[string]$Backup,[string]$Helper)\n"
        "Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue\n"
        "Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue\n"
        "try {\n"
        "  if (Test-Path -LiteralPath $Destination) { Move-Item -LiteralPath $Destination -Destination $Backup -Force }\n"
        "  Move-Item -LiteralPath $Source -Destination $Destination -Force\n"
        "  Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue\n"
        "} catch {\n"
        "  if ((Test-Path -LiteralPath $Backup) -and -not (Test-Path -LiteralPath $Destination)) { Move-Item -LiteralPath $Backup -Destination $Destination -Force }\n"
        "  throw\n"
        "} finally { Remove-Item -LiteralPath $Helper -Force -ErrorAction SilentlyContinue }\n",
        encoding="utf-8-sig",
    )
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(helper), "-ProcessId", str(os.getpid()), "-Source", str(source),
            "-Destination", str(destination), "-Backup", str(backup), "-Helper", str(helper),
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return helper


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
        "asset_name": Path(from_file).name,
        "asset_size": Path(from_file).expanduser().resolve().stat().st_size,
        "update_available": True,
    }
    if check_only or (not release["update_available"] and not force):
        return {**release, "updated": False, "check_only": check_only}
    from facut.runtime_control import clean_services

    stopped_services = clean_services(["all"])
    supplied = Path(from_file).expanduser().resolve() if from_file is not None else None
    archive_update = bool(
        os.name == "nt"
        and (
            (supplied is not None and zipfile.is_zipfile(supplied))
            or str(release.get("asset_name") or "").casefold().endswith(".zip")
        )
    )
    if supplied is not None:
        if archive_update:
            staged_asset = supplied
        else:
            staged = destination.with_name(f"{destination.name}.update-{os.getpid()}")
            _atomic_copy(supplied, staged)
    else:
        if not release.get("asset_url"):
            raise FileNotFoundError("The latest GitHub Release has no compatible FACUT asset.")
        suffix = ".zip" if archive_update else ".exe"
        download_target = install_directory.parent / f".facut-download-{os.getpid()}{suffix}"
        try:
            downloaded = _download_update(
                str(release["asset_url"]), download_target, archive=archive_update
            )
        except HTTPError as error:
            if not archive_update or error.code != 404:
                raise
            legacy_url = str(release["asset_url"]).rsplit("/", 1)[0] + "/facut.exe"
            downloaded = _download_update(
                legacy_url,
                install_directory.parent / f".facut-download-{os.getpid()}.exe",
            )
            archive_update = False
            release["asset_url"] = legacy_url
            release["asset_name"] = "facut.exe"
            release["legacy_asset_fallback"] = True
        if archive_update:
            staged_asset = downloaded
        else:
            staged = downloaded

    if archive_update:
        _assert_owned_install_directory(install_directory)
        stage_container = install_directory.parent / f".facut-update-{os.getpid()}"
        extracted = _extract_portable_archive(staged_asset, stage_container)
        if extracted != stage_container:
            flattened = install_directory.parent / f".facut-bundle-{os.getpid()}"
            shutil.rmtree(flattened, ignore_errors=True)
            os.replace(extracted, flattened)
            shutil.rmtree(stage_container, ignore_errors=True)
            stage_container = flattened
        prior_manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
        prior_manifest.update(
            {
                "version": release["latest_version"],
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "executable": str(install_directory / _executable_name()),
                "runtime": {
                    "installed": (stage_container / "facut_runtime").is_dir(),
                    "mode": "portable-directory",
                },
            }
        )
        (stage_container / "install.json").write_text(
            json.dumps(prior_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        running = getattr(sys, "frozen", False) and _same_path(Path(sys.executable), destination)
        if running:
            helper = _schedule_running_bundle_replacement(stage_container, install_directory)
            mode = "scheduled-bundle-after-exit"
        else:
            _replace_install_bundle(stage_container, install_directory)
            helper = None
            mode = "replaced-bundle"
        if supplied is None:
            staged_asset.unlink(missing_ok=True)
        return {
            **release,
            "updated": True,
            "destination": str(install_directory / _executable_name()),
            "mode": mode,
            "helper": str(helper) if helper else None,
            "bundle": True,
            "stopped_services": stopped_services["result"],
        }

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
        "stopped_services": stopped_services["result"],
    }
