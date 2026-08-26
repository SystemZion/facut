"""Versioned JSONL bridge to the optional facut-native sidecar."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from uuid import uuid4

from facut.exceptions import DependencyMissingError, FacutError, MediaProbeError


PROTOCOL_VERSION = 1
ProgressCallback = Callable[[dict[str, Any]], None]
_GENERATED_DIRECTORY_NAMES = {
    ".facut-native",
    ".facut-native-benchmark",
    "analysis",
    "cache",
    "deliverables",
    "exports",
    "final",
    "output",
    "outputs",
    "previews",
    "renders",
    "成片",
}


def _is_generated_directory(name: str) -> bool:
    """Recognize versioned FACUT output directories such as analysis-v3-cold."""

    folded = name.casefold()
    return any(
        folded == base or folded.startswith((f"{base}-", f"{base}_", f"{base}."))
        for base in _GENERATED_DIRECTORY_NAMES
    )


def discover_native(explicit: str | Path | None = None) -> Path | None:
    """Return a usable native sidecar path without mutating PATH."""

    names = (
        ["facut-native.exe", "facut-native"] if os.name == "nt" else ["facut-native"]
    )
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

    def __init__(
        self, executable: str | Path | None = None, *, timeout: float = 3600.0
    ) -> None:
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
        timeout: float | None = None,
    ) -> dict[str, Any]:
        task_id = str(request.setdefault("task_id", f"native_{uuid4().hex}"))
        request.setdefault("protocol", PROTOCOL_VERSION)
        environment = os.environ.copy()
        environment["PATH"] = (
            str(self.executable.parent) + os.pathsep + environment.get("PATH", "")
        )
        effective_timeout = self.timeout if timeout is None else timeout
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
        timed_out = threading.Event()

        def kill_on_timeout() -> None:
            try:
                running = process.poll() is None
            except AttributeError:  # Lightweight test doubles.
                running = getattr(process, "returncode", None) is None
            if running:
                timed_out.set()
                process.kill()

        watchdog = threading.Timer(effective_timeout, kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()
        assert process.stdin is not None and process.stdout is not None
        try:
            ready_line = process.stdout.readline()
            if timed_out.is_set():
                raise NativeProtocolError(
                    "facut-native exceeded the configured timeout."
                )
            try:
                ready = json.loads(ready_line)
            except json.JSONDecodeError as error:
                process.kill()
                raise NativeProtocolError(
                    "facut-native did not emit a valid ready event.",
                    details={"line": ready_line[-1000:]},
                ) from error
            if (
                ready.get("event") != "ready"
                or ready.get("protocol") != PROTOCOL_VERSION
            ):
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
                if timed_out.is_set():
                    raise NativeProtocolError(
                        "facut-native exceeded the configured timeout."
                    )
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    process.kill()
                    raise NativeProtocolError(
                        "facut-native emitted invalid JSONL.",
                        details={"line": line[-1000:]},
                    ) from error
                if event.get("task_id") != task_id:
                    continue
                events.append(
                    {
                        key: event[key]
                        for key in (
                            "event",
                            "task_id",
                            "media_id",
                            "progress",
                            "completed",
                            "total",
                            "warning",
                        )
                        if key in event
                    }
                )
                if progress and event.get("event") in {
                    "progress",
                    "asset_complete",
                    "warning",
                }:
                    progress(event)
                if event.get("event") == "error":
                    process.kill()
                    payload = event.get("error") or {}
                    raise NativeProtocolError(
                        str(
                            payload.get("message")
                            or "facut-native rejected the request."
                        ),
                        details={"native_code": payload.get("code")},
                    )
                if event.get("event") == "complete":
                    final = event
                    break
            return_code = process.wait(timeout=10)
            stderr = process.stderr.read() if process.stderr else ""
            if timed_out.is_set():
                raise NativeProtocolError(
                    "facut-native exceeded the configured timeout."
                )
            if return_code != 0 or final is None:
                raise NativeProtocolError(
                    "facut-native terminated before completing the task.",
                    details={"returncode": return_code, "stderr": stderr[-4000:]},
                )
            data = dict(final.get("data") or {})
            data["task_id"] = task_id
            data["events"] = events
            return data
        finally:
            watchdog.cancel()

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
        jobs: int = 3,
        asset_timeout_seconds: float = 180.0,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if mode not in {"fast", "deep"}:
            raise ValueError("Native scan mode must be fast or deep.")
        if not 1 <= jobs <= 8:
            raise ValueError("Native scan jobs must be between 1 and 8.")
        input_list = list(inputs)
        output_root = Path(output_directory).expanduser().resolve()

        if asset_timeout_seconds <= 0:
            raise ValueError("Native per-asset timeout must be positive.")

        def run_with_retry(
            batch: list[dict[str, Any]], callback=None
        ) -> dict[str, Any]:
            request = {
                "action": "media.batch_scan",
                "inputs": batch,
                "options": {
                    "output_directory": str(output_root),
                    "mode": mode,
                    "thumbnail_width": thumbnail_width,
                    "audio_window_seconds": audio_window_seconds,
                    "jobs": 1,
                },
            }
            first_error: NativeProtocolError | None = None
            for attempt in range(2):
                try:
                    return self._run(
                        dict(request), progress=callback, timeout=asset_timeout_seconds
                    )
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
            raise AssertionError(
                "Native retry loop ended unexpectedly."
            )  # pragma: no cover

        if not input_list:
            result = {
                "task_id": f"native_empty_{uuid4().hex}",
                "results": [],
                "elapsed_seconds": 0.0,
                "events": [],
            }
        else:
            # A native process handles exactly one asset. Some FFmpeg demuxers retain
            # process-global state after several mixed image/video inputs; isolation
            # keeps one damaged asset from freezing every remaining item.
            worker_count = min(jobs, len(input_list))
            callback_lock = threading.Lock()
            completed_count = 0

            def aggregate_progress(event: dict[str, Any]) -> None:
                if not progress:
                    return
                with callback_lock:
                    if event.get("event") == "warning":
                        progress(event)

            started = time.monotonic()
            results_by_id: dict[str, dict[str, Any]] = {}
            worker_tasks: list[str] = []
            events: list[dict[str, Any]] = []
            with ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="facut-native"
            ) as pool:
                futures = {
                    pool.submit(run_with_retry, [item], aggregate_progress): item
                    for item in input_list
                }
                for future in as_completed(futures):
                    item = futures[future]
                    media_id = str(item.get("media_id"))
                    try:
                        part = future.result()
                        worker_tasks.append(str(part.get("task_id")))
                        events.extend(part.get("events", []))
                        part_results = list(part.get("results") or [])
                        if part_results:
                            results_by_id[media_id] = part_results[0]
                        else:
                            results_by_id[media_id] = {
                                "media_id": media_id,
                                "source": item.get("original_path") or item.get("path"),
                                "status": "error",
                                "error": {
                                    "code": "NATIVE_EMPTY_RESULT",
                                    "message": "Native scan returned no result.",
                                },
                            }
                    except Exception as error:
                        results_by_id[media_id] = {
                            "media_id": media_id,
                            "source": item.get("original_path") or item.get("path"),
                            "status": "error",
                            "error": {
                                "code": getattr(error, "code", "NATIVE_ASSET_FAILED"),
                                "message": str(error),
                            },
                        }
                    completed_count += 1
                    if progress:
                        elapsed = max(time.monotonic() - started, 1e-9)
                        assets_per_second = completed_count / elapsed
                        remaining = len(input_list) - completed_count
                        with callback_lock:
                            progress(
                                {
                                    "event": "progress",
                                    "stage": "native.batch_scan",
                                    "status": results_by_id[media_id].get("status", "unknown"),
                                    "completed": completed_count,
                                    "total": len(input_list),
                                    "progress": completed_count / len(input_list),
                                    "media_id": media_id,
                                    "elapsed_seconds": round(elapsed, 3),
                                    "assets_per_second": round(assets_per_second, 3),
                                    "eta_seconds": round(remaining / assets_per_second, 3),
                                }
                            )
            result = {
                "task_id": f"native_parallel_{uuid4().hex}",
                "results": [
                    results_by_id[str(item.get("media_id"))] for item in input_list
                ],
                "elapsed_seconds": time.monotonic() - started,
                "worker_tasks": worker_tasks,
                "events": events,
            }
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
        "cache_hits": sum(
            bool((item.get("cache") or {}).get("hit")) for item in succeeded
        ),
        "proxy_assets": sum(bool(item.get("used_proxy")) for item in succeeded),
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


def batch_failure_warnings(summary: dict[str, Any]) -> list[str]:
    """Expose partial failures and reject batches where no asset was analyzed."""

    succeeded = int(summary.get("succeeded") or 0)
    failed = int(summary.get("failed") or 0)
    if failed <= 0:
        return []
    failures = list(summary.get("failures") or [])
    if succeeded <= 0:
        raise MediaProbeError(
            f"All {failed} media assets failed analysis.",
            suggestion="Inspect the input files and `details.failures`, then retry the failed assets.",
            details={"failures": failures[:50]},
        )
    return [
        f"{failed} of {succeeded + failed} media assets failed analysis; "
        "inspect `data.failures` before treating the batch as complete."
    ]


def collect_batch_inputs(
    folder: str | Path,
    *,
    output_directory: str | Path | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect originals once, exclude generated output, and pair exact-name LRF proxies."""

    from facut.media.importer import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
    from facut.media.proxy_manager import is_probable_proxy_path, proxy_base_stem

    root = Path(folder).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve() if output_directory else None
    candidates: list[Path] = []
    excluded_generated = 0
    for item in sorted(root.rglob("*"), key=lambda path: str(path).casefold()):
        if not item.is_file() or item.suffix.casefold() not in (
            VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
        ):
            continue
        resolved = item.resolve()
        relative_directories = resolved.relative_to(root).parts[:-1]
        output_project_root = None
        if (
            output is not None
            and output.parent != root
            and output.parent.name.casefold() == "project"
        ):
            output_project_root = output.parent
        if (
            (output is not None and (resolved == output or output in resolved.parents))
            or (
                output_project_root is not None
                and output_project_root in resolved.parents
            )
            or any(_is_generated_directory(part) for part in relative_directories)
        ):
            excluded_generated += 1
            continue
        candidates.append(resolved)

    proxy_candidates: dict[tuple[str, str], list[Path]] = {}
    originals: list[Path] = []
    for path in candidates:
        key = (str(path.parent).casefold(), proxy_base_stem(path))
        if is_probable_proxy_path(path):
            if path.stat().st_size > 0:
                proxy_candidates.setdefault(key, []).append(path)
        else:
            originals.append(path)
    if limit is not None:
        originals = originals[:limit]

    inputs: list[dict[str, Any]] = []
    linked = 0
    ambiguous = 0
    for index, original in enumerate(originals, 1):
        key = (str(original.parent).casefold(), proxy_base_stem(original))
        matches = sorted(
            proxy_candidates.get(key, []), key=lambda path: str(path).casefold()
        )
        analysis_path = original
        used_proxy = False
        if len(matches) == 1:
            analysis_path = matches[0]
            used_proxy = True
            linked += 1
        elif len(matches) > 1:
            ambiguous += 1
        inputs.append(
            {
                "media_id": f"batch_{index:06d}",
                "path": str(analysis_path),
                "original_path": str(original),
                "used_proxy": used_proxy,
            }
        )
    return inputs, {
        "original_count": len(originals),
        "proxy_linked": linked,
        "proxy_ambiguous": ambiguous,
        "proxy_candidates_excluded": sum(
            len(items) for items in proxy_candidates.values()
        ),
        "generated_files_excluded": excluded_generated,
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
