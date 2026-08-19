"""Versioned JSONL bridge to the optional facut-native sidecar."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4

from facut.exceptions import DependencyMissingError, FacutError


PROTOCOL_VERSION = 1
ProgressCallback = Callable[[dict[str, Any]], None]


def discover_native(explicit: str | Path | None = None) -> Path | None:
    """Return a usable native sidecar path without mutating PATH."""

    names = ["facut-native.exe", "facut-native"] if os.name == "nt" else ["facut-native"]
    candidates: list[Path] = []
    configured = explicit or os.environ.get("FACUT_NATIVE")
    if configured:
        candidates.append(Path(configured).expanduser())
    executable = Path(sys.executable).resolve()
    candidates.extend(executable.parent / name for name in names)
    package_root = Path(__file__).resolve().parents[3]
    candidates.extend(package_root / "build" / "native-win64" / name for name in names)
    for name in names:
        located = shutil.which(name)
        if located:
            candidates.append(Path(located))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


class NativeProtocolError(FacutError):
    """The native sidecar violated or rejected protocol v1."""

    code = "NATIVE_PROTOCOL_ERROR"


class NativeClient:
    """Run one bounded JSONL session and collect deterministic results."""

    def __init__(self, executable: str | Path | None = None, *, timeout: float = 3600.0) -> None:
        resolved = discover_native(executable)
        if resolved is None:
            raise DependencyMissingError(
                "FACUT Native Accelerator is not installed.",
                suggestion="Install the Windows x64 native sidecar or use `--engine python`.",
            )
        self.executable = resolved
        self.timeout = timeout

    def _run(
        self,
        request: dict[str, Any],
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        task_id = str(request.setdefault("task_id", f"native_{uuid4().hex}"))
        request.setdefault("protocol", PROTOCOL_VERSION)
        environment = os.environ.copy()
        environment["PATH"] = str(self.executable.parent) + os.pathsep + environment.get("PATH", "")
        started = time.monotonic()
        process = subprocess.Popen(
            [str(self.executable), "--jsonl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert process.stdin is not None and process.stdout is not None
        ready_line = process.stdout.readline()
        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as error:
            process.kill()
            raise NativeProtocolError(
                "facut-native did not emit a valid ready event.",
                details={"line": ready_line[-1000:]},
            ) from error
        if ready.get("event") != "ready" or ready.get("protocol") != PROTOCOL_VERSION:
            process.kill()
            raise NativeProtocolError(
                "facut-native uses an incompatible protocol.",
                details={"expected": PROTOCOL_VERSION, "received": ready},
            )
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        process.stdin.close()
        final: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        for line in process.stdout:
            if time.monotonic() - started > self.timeout:
                process.kill()
                raise NativeProtocolError("facut-native exceeded the configured timeout.")
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                process.kill()
                raise NativeProtocolError(
                    "facut-native emitted invalid JSONL.", details={"line": line[-1000:]}
                ) from error
            if event.get("task_id") != task_id:
                continue
            events.append(
                {
                    key: event[key]
                    for key in ("event", "task_id", "media_id", "progress", "completed", "total", "warning")
                    if key in event
                }
            )
            if progress and event.get("event") in {"progress", "asset_complete", "warning"}:
                progress(event)
            if event.get("event") == "error":
                process.kill()
                payload = event.get("error") or {}
                raise NativeProtocolError(
                    str(payload.get("message") or "facut-native rejected the request."),
                    details={"native_code": payload.get("code")},
                )
            if event.get("event") == "complete":
                final = event
                break
        return_code = process.wait(timeout=10)
        stderr = process.stderr.read() if process.stderr else ""
        if return_code != 0 or final is None:
            raise NativeProtocolError(
                "facut-native terminated before completing the task.",
                details={"returncode": return_code, "stderr": stderr[-4000:]},
            )
        data = dict(final.get("data") or {})
        data["task_id"] = task_id
        data["events"] = events
        return data

    def doctor(self) -> dict[str, Any]:
        return self._run({"action": "doctor"})

    def batch_scan(
        self,
        inputs: Iterable[dict[str, Any]],
        *,
        output_directory: str | Path,
        mode: str = "fast",
        thumbnail_width: int = 480,
        audio_window_seconds: float = 0.1,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if mode not in {"fast", "deep"}:
            raise ValueError("Native scan mode must be fast or deep.")
        request = {
            "action": "media.batch_scan",
            "inputs": list(inputs),
            "options": {
                "output_directory": str(Path(output_directory).expanduser().resolve()),
                "mode": mode,
                "thumbnail_width": thumbnail_width,
                "audio_window_seconds": audio_window_seconds,
            },
        }
        first_error: NativeProtocolError | None = None
        for attempt in range(2):
            try:
                # A fresh process is intentional: one automatic restart recovers
                # a crashed decoder without reusing corrupted native state.
                result = self._run(dict(request), progress=progress)
                break
            except NativeProtocolError as error:
                if attempt:
                    if first_error is not None:
                        error.details = {
                            **(error.details or {}),
                            "first_attempt": str(first_error),
                            "attempts": 2,
                        }
                    raise
                first_error = error
        else:  # pragma: no cover
            raise AssertionError("Native retry loop ended unexpectedly.")
        output_root = Path(output_directory).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        result_path = output_root / "native-results.json"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=output_root, suffix=".tmp", delete=False
        ) as stream:
            json.dump(result, stream, ensure_ascii=False, separators=(",", ":"))
            temporary = Path(stream.name)
        os.replace(temporary, result_path)
        result["result_file"] = str(result_path)
        return result


def compact_batch_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return an Agent-safe summary while the complete evidence remains on disk."""

    items = list(result.get("results") or [])
    succeeded = [item for item in items if item.get("status") == "success"]
    failed = [item for item in items if item.get("status") != "success"]
    return {
        "task_id": result.get("task_id"),
        "result_file": result.get("result_file"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "asset_count": len(items),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failures": [
            {"media_id": item.get("media_id"), "error": item.get("error")}
            for item in failed[:50]
        ],
        "representative_frame_count": sum(
            len(item.get("representative_frames") or []) for item in succeeded
        ),
        "waveform_window_count": sum(
            len((item.get("waveform") or {}).get("peaks") or []) for item in succeeded
        ),
    }


def scan_project_media(
    manager: Any,
    *,
    mode: str = "fast",
    executable: str | Path | None = None,
) -> dict[str, Any]:
    """Scan project video/image assets, preferring linked proxies for analysis."""

    from facut.core.models import MediaKind

    inputs: list[dict[str, Any]] = []
    for asset in manager.require_document().media:
        if asset.kind not in {MediaKind.VIDEO, MediaKind.IMAGE}:
            continue
        media_path = manager.resolve_path(asset.path)
        if asset.proxy_path:
            proxy_path = manager.resolve_path(asset.proxy_path)
            if proxy_path.is_file() and proxy_path.stat().st_size > 0:
                media_path = proxy_path
        inputs.append({"media_id": asset.id, "path": str(media_path)})
    return NativeClient(executable).batch_scan(
        inputs,
        output_directory=manager.project_dir / "cache" / "native" / "v1",
        mode=mode,
    )
