"""Lifecycle controls for optional warm background services.

Stopping a service releases only reconstructable process memory.  Models,
recordings, project files and render caches are never deleted here.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable
import json

from platformdirs import user_data_path

from facut.config import AppConfig, load_config, save_config


SUPPORTED_SERVICES = ("voice",)


def normalize_services(services: Iterable[str]) -> list[str]:
    """Validate service selectors and expand ``all`` deterministically."""

    requested = [str(item).strip().casefold() for item in services if str(item).strip()]
    if not requested:
        raise ValueError(
            "Choose at least one service with --service. "
            f"Available services: {', '.join(SUPPORTED_SERVICES)} or all."
        )
    if "all" in requested:
        if len(set(requested)) != 1:
            raise ValueError("Use --service all by itself.")
        return list(SUPPORTED_SERVICES)
    unknown = sorted(set(requested) - set(SUPPORTED_SERVICES))
    if unknown:
        raise ValueError(
            f"Unknown background service(s): {', '.join(unknown)}. "
            f"Available services: {', '.join(SUPPORTED_SERVICES)}."
        )
    return list(dict.fromkeys(requested))


def service_status(services: Iterable[str]) -> dict[str, Any]:
    selected = normalize_services(services)
    result: dict[str, Any] = {}
    for service in selected:
        if service == "voice":
            from facut.voice.service import voice_service_status

            result[service] = voice_service_status()
    return result


def warm_services(services: Iterable[str]) -> dict[str, Any]:
    """Start selected services and wait until each has reported ready."""

    selected = normalize_services(services)
    config = load_config()
    result: dict[str, Any] = {}
    for service in selected:
        if service == "voice":
            from facut.voice.service import start_voice_service

            result[service] = start_voice_service(
                device="auto",
                idle_timeout=config.runtime.voice_idle_timeout,
            )
    return {
        "selected": selected,
        "services": result,
        "background": False,
        "persistent_data_deleted": False,
    }


def clean_services(services: Iterable[str], *, dry_run: bool = False) -> dict[str, Any]:
    """Stop only selected services; do not remove persistent data or disk caches."""

    selected = normalize_services(services)
    before = service_status(selected)
    result: dict[str, Any] = {}
    for service in selected:
        if dry_run:
            result[service] = {"would_stop": bool(before[service].get("running"))}
        elif service == "voice":
            from facut.voice.service import stop_voice_service

            result[service] = stop_voice_service()
    return {
        "selected": selected,
        "dry_run": dry_run,
        "before": before,
        "result": result,
        "persistent_data_deleted": False,
        "note": "The operating system reclaims stopped service memory; models and caches remain on disk.",
    }


def configure_autoload(service: str, *, enabled: bool) -> dict[str, Any]:
    """Persist whether a registered service should warm automatically."""

    selected = normalize_services([service])
    config = load_config()
    current = list(dict.fromkeys(config.runtime.services))
    for name in selected:
        if enabled and name not in current:
            current.append(name)
        elif not enabled and name in current:
            current.remove(name)
    values = config.model_dump()
    values["runtime"]["services"] = current
    values["runtime"]["autoload"] = bool(current)
    destination = save_config(AppConfig.model_validate(values))
    return {
        "enabled": enabled,
        "service": service,
        "autoload": bool(current),
        "services": current,
        "config": str(destination.resolve()),
    }


def autoload_status() -> dict[str, Any]:
    config = load_config()
    configured = [item for item in config.runtime.services if item in SUPPORTED_SERVICES]
    return {
        "autoload": config.runtime.autoload,
        "configured_services": configured,
        "supported_services": list(SUPPORTED_SERVICES),
        "voice_idle_timeout": config.runtime.voice_idle_timeout,
        "services": service_status(configured) if configured else {},
    }


def _warmup_lock_path() -> Path:
    root = Path(os.environ.get("FACUT_VOICE_HOME") or (user_data_path("facut", appauthor=False) / "voices"))
    return root.expanduser().resolve() / "service" / "autoload.lock"


def _voice_state_path() -> Path:
    root = Path(os.environ.get("FACUT_VOICE_HOME") or (user_data_path("facut", appauthor=False) / "voices"))
    return root.expanduser().resolve() / "service" / "voice-service.json"


def _voice_state_has_live_process() -> bool:
    try:
        payload = json.loads(_voice_state_path().read_text("utf-8"))
        pid = int(payload["pid"])
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _remove_stale_warmup_lock(lock_path: Path, *, ttl_seconds: float = 900.0) -> bool:
    """Remove a lock only when its owner is dead or the lock exceeded its TTL."""

    try:
        payload = json.loads(lock_path.read_text("utf-8"))
        pid = int(payload.get("pid", 0))
        created_at = float(payload.get("created_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pid = 0
        try:
            created_at = lock_path.stat().st_mtime
        except OSError:
            return True
    stale = not _pid_is_alive(pid) or time.time() - created_at > ttl_seconds
    if stale:
        lock_path.unlink(missing_ok=True)
    return stale


def schedule_default_warmup() -> dict[str, Any]:
    """Launch a detached warmup helper without delaying the requested command."""

    config = load_config()
    selected = [item for item in config.runtime.services if item in SUPPORTED_SERVICES]
    if not config.runtime.autoload or not selected:
        return {"scheduled": False, "reason": "autoload-disabled", "services": selected}
    # A valid daemon removes its state file during normal shutdown.  Avoid
    # importing the voice/ML stack merely to ping it on every light CLI call.
    if selected == ["voice"] and _voice_state_has_live_process():
        return {"scheduled": False, "reason": "state-present", "services": selected}
    lock_path = _warmup_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if attempt == 0 and _remove_stale_warmup_lock(lock_path):
                continue
            return {"scheduled": False, "reason": "warmup-in-progress", "services": selected}
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "created_at": time.time()}, stream)
            break
    if getattr(sys, "frozen", False):
        command = [sys.executable, "__runtime_warmup__", *selected]
    else:
        command = [sys.executable, "-m", "facut.runtime_control", "__runtime_warmup__", *selected]
    log_path = lock_path.parent / "runtime-warmup.log"
    log_stream = log_path.open("a", encoding="utf-8")
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            env=environment,
            shell=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
            if os.name == "nt"
            else 0,
            start_new_session=os.name != "nt",
        )
        if lock_path.exists():
            lock_path.write_text(
                json.dumps({"pid": process.pid, "created_at": time.time()}), encoding="utf-8"
            )
    except Exception:
        lock_path.unlink(missing_ok=True)
        raise
    finally:
        log_stream.close()
    return {
        "scheduled": True,
        "services": selected,
        "pid": process.pid,
        "log": str(log_path),
    }


def warmup_child(services: Iterable[str]) -> int:
    """Detached helper entry; failure is logged and never blocks the foreground CLI."""

    lock_path = _warmup_lock_path()
    try:
        warm_services(services)
        return 0
    except Exception as error:
        print(f"FACUT background warmup failed: {error}", flush=True)
        return 1
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "__runtime_warmup__":
        raise SystemExit(warmup_child(arguments[1:]))
    raise SystemExit("runtime_control is an internal module; use facut warmup/cleanram/autoload.")
