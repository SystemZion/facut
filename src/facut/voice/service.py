"""Authenticated loopback daemon for warm local voice synthesis."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from facut.exceptions import DependencyMissingError, FacutError

from .models import VoiceProfile
from .providers import (
    build_provider_request,
    invoke_provider_request,
    provider_status,
    resolve_voice_device,
    validate_provider_response,
)
from .store import default_voice_home


SERVICE_PROTOCOL = "facut-voice-service/1.0"
PROVIDER_STREAM_PROTOCOL = "facut-voice-provider-jsonl/1.0"
MAX_REQUEST_BYTES = 4 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _daemon_environment() -> dict[str, str]:
    """Build an isolated environment for the long-lived service process."""

    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        # A one-file PyInstaller child otherwise reuses the parent's _MEI
        # extraction directory. The short-lived CLI then cannot remove it,
        # and the daemon has no owner left to clean it after shutdown.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        # Pass only FACUT's import root. Re-exporting the parent's complete
        # sys.path can inject pytest/site-packages and foreign framework paths
        # into a fresh macOS interpreter before it reaches this module.
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return environment


class VoiceServiceError(FacutError):
    code = "VOICE_SERVICE_ERROR"


class VoiceCudaRequiredError(VoiceServiceError):
    code = "VOICE_CUDA_REQUIRED"


def default_service_state_path() -> Path:
    return default_voice_home() / "service" / "voice-service.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def _read_state(path: str | Path | None = None) -> dict[str, Any] | None:
    state_path = Path(path or default_service_state_path()).expanduser().resolve()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("protocol") != SERVICE_PROTOCOL:
        return None
    try:
        valid_endpoint = (
            payload.get("host") == "127.0.0.1"
            and 1 <= int(payload.get("port")) <= 65535
            and len(str(payload.get("token") or "")) >= 32
        )
    except (TypeError, ValueError):
        valid_endpoint = False
    if not valid_endpoint:
        return None
    return payload


class ProviderWorker:
    """Keep a JSONL-capable provider alive, with a safe one-shot fallback."""

    def __init__(
        self,
        executable: str | Path,
        *,
        device: str = "auto",
        require_cuda: bool = False,
        startup_timeout: float = 180.0,
        allow_oneshot_fallback: bool = True,
        stderr_path: str | Path | None = None,
    ) -> None:
        self.executable = str(Path(executable).expanduser().resolve())
        self.device = resolve_voice_device(device, require_cuda=require_cuda)
        self.require_cuda = require_cuda
        self.startup_timeout = startup_timeout
        self.allow_oneshot_fallback = allow_oneshot_fallback
        self.stderr_path = Path(stderr_path).resolve() if stderr_path else None
        self.process: subprocess.Popen[str] | None = None
        self.ready: dict[str, Any] = {}
        self.mode = "not_started"
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stderr_stream: Any = None
        self._lock = threading.Lock()

    def _exit_diagnostics(self) -> str:
        """Summarize a provider crash without leaking the full provider log."""

        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        exit_code = process.returncode if process is not None else None
        details = f"exit code {exit_code}" if exit_code is not None else "unknown exit code"
        if exit_code in {-1073741819, 3221225477}:
            details += " (Windows access violation 0xC0000005 during native model startup)"
        log_tail = ""
        if self.stderr_path and self.stderr_path.is_file():
            try:
                lines = self.stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()
                log_tail = " | ".join(lines[-8:])
            except OSError:
                pass
        return f"{details}: {log_tail}" if log_tail else details

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := self.process.stdout.readline():
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _read_payload(self, timeout: float) -> dict[str, Any]:
        try:
            line = self._responses.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("Voice provider did not respond before the timeout.") from error
        if line is None:
            raise RuntimeError(
                "Voice provider stream ended unexpectedly; " + self._exit_diagnostics()
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("Voice provider returned invalid JSONL.") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Voice provider JSONL response must be an object.")
        return payload

    def start(self) -> dict[str, Any]:
        started = time.perf_counter()
        environment = os.environ.copy()
        # The provider may use a different Python runtime from FACUT. Inheriting
        # FACUT's PYTHONPATH/PYTHONHOME can mix incompatible standard libraries
        # and native wheels (for example Python 3.13 into a Python 3.10 voice
        # venv), so the provider must start from its own isolated runtime.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["FACUT_VOICE_DEVICE"] = self.device
        environment["FACUT_VOICE_REQUIRE_CUDA"] = "1" if self.require_cuda else "0"
        if self.stderr_path:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_stream = self.stderr_path.open("a", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [self.executable, "--facut-voice-jsonl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_stream or subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0,
            )
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader.start()
            ready = self._read_payload(self.startup_timeout)
            if ready.get("status") != "ready" or ready.get("protocol") != PROVIDER_STREAM_PROTOCOL:
                raise RuntimeError("Voice provider does not support the persistent JSONL protocol.")
            if self.require_cuda and (
                ready.get("device") != "cuda" or ready.get("cuda") is not True
            ):
                raise VoiceCudaRequiredError(
                    "The persistent voice provider did not confirm CUDA execution.",
                    details={"provider_status": ready},
                )
            self.ready = ready
            self.mode = "persistent"
        except Exception as error:
            self.close()
            if self.require_cuda:
                raise VoiceCudaRequiredError(
                    "A CUDA-verified persistent voice provider could not be started.",
                    suggestion="Install the CUDA voice runtime and run `facut voice serve status --json`.",
                    details={"provider": self.executable, "reason": str(error)},
                ) from error
            if not self.allow_oneshot_fallback:
                raise VoiceServiceError(
                    "The voice provider does not support persistent synthesis.",
                    details={"provider": self.executable, "reason": str(error)},
                ) from error
            self.mode = "oneshot"
            self.ready = {
                "status": "ready",
                "protocol": "facut-voice-provider/1.0",
                "provider": Path(self.executable).name,
                "device": "unknown",
                "compute_type": None,
                "cuda": None,
                "persistent": False,
                "warning": "Provider lacks JSONL support; each request will cold-start.",
            }
        self.ready["cold_start_seconds"] = round(time.perf_counter() - started, 3)
        return dict(self.ready)

    def request(self, payload: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
        with self._lock:
            if self.mode == "oneshot":
                return invoke_provider_request(
                    self.executable,
                    payload,
                    timeout=timeout,
                    device=self.device,
                    require_cuda=self.require_cuda,
                )
            if self.mode != "persistent" or self.process is None or self.process.stdin is None:
                raise RuntimeError("Voice provider worker is not running.")
            if self.process.poll() is not None:
                raise RuntimeError("Voice provider exited before synthesis.")
            self.process.stdin.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            try:
                response = self._read_payload(timeout)
            except TimeoutError as error:
                # The stream has lost request/response synchronization. Kill
                # the worker before another request can enter; unique output
                # directories also quarantine any write already in flight.
                self.close()
                try:
                    self.start()
                except Exception as restart_error:
                    raise TimeoutError(
                        "Voice provider timed out and could not be restarted: "
                        f"{restart_error}"
                    ) from error
                raise TimeoutError(
                    "Voice provider timed out; the worker was restarted and its late output discarded."
                ) from error
            if response.get("status") == "error":
                error = response.get("error") or {}
                raise RuntimeError(str(error.get("message") or "Voice provider synthesis failed."))
            return response

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write('{"action":"shutdown"}\n')
                    process.stdin.flush()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if self._stderr_stream is not None:
            self._stderr_stream.close()
            self._stderr_stream = None


@dataclass(slots=True)
class VoiceServiceRuntime:
    worker: ProviderWorker
    idle_timeout: float
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    started_at: str = field(default_factory=_utc_now)
    last_activity_at: str | None = None
    last_synthesis_seconds: float | None = None
    request_count: int = 0
    _last_activity_monotonic: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def touch_synthesis(self, elapsed: float) -> None:
        with self.lock:
            self._last_activity_monotonic = time.monotonic()
            self.last_activity_at = _utc_now()
            self.last_synthesis_seconds = round(elapsed, 3)
            self.request_count += 1

    def is_idle(self) -> bool:
        return self.idle_timeout > 0 and time.monotonic() - self._last_activity_monotonic >= self.idle_timeout

    def status(self, host: str, port: int) -> dict[str, Any]:
        ready = self.worker.ready
        return {
            "protocol": SERVICE_PROTOCOL,
            "running": True,
            "endpoint": f"http://{host}:{port}",
            "pid": os.getpid(),
            "started_at": self.started_at,
            "last_activity_at": self.last_activity_at,
            "idle_timeout_seconds": self.idle_timeout,
            "request_count": self.request_count,
            "last_synthesis_seconds": self.last_synthesis_seconds,
            "provider": ready.get("provider"),
            "model_version": ready.get("model_version"),
            "model_path": ready.get("model_path"),
            "device": ready.get("device"),
            "compute_type": ready.get("compute_type"),
            "cuda": ready.get("cuda"),
            "gpu": ready.get("gpu"),
            "vram": ready.get("vram"),
            "cold_start_seconds": ready.get("cold_start_seconds"),
            "persistent_provider": self.worker.mode == "persistent",
            "warnings": [ready["warning"]] if ready.get("warning") else [],
        }


class VoiceServiceServer:
    """Serve authenticated synthesis requests on an OS-assigned loopback port."""

    def __init__(
        self,
        provider: str | Path,
        *,
        device: str = "auto",
        require_cuda: bool = False,
        idle_timeout: float = 600.0,
        port: int = 0,
        token: str | None = None,
        state_path: str | Path | None = None,
        provider_startup_timeout: float = 180.0,
        allow_oneshot_fallback: bool = True,
    ) -> None:
        if idle_timeout < 0:
            raise ValueError("idle_timeout cannot be negative.")
        state = Path(state_path or default_service_state_path()).expanduser().resolve()
        worker = ProviderWorker(
            provider,
            device=device,
            require_cuda=require_cuda,
            startup_timeout=provider_startup_timeout,
            allow_oneshot_fallback=allow_oneshot_fallback,
            stderr_path=state.parent / "voice-provider.log",
        )
        worker.start()
        self.runtime = VoiceServiceRuntime(
            worker=worker, idle_timeout=idle_timeout, token=token or secrets.token_urlsafe(32)
        )
        self.state_path = state
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler_type())
        self._httpd.daemon_threads = True
        self.host, self.port = self._httpd.server_address[:2]
        self._monitor: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "facut-voice-service/1.0"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _authorized(self) -> bool:
                bearer = self.headers.get("Authorization", "")
                supplied = (
                    bearer.removeprefix("Bearer ")
                    if bearer.startswith("Bearer ")
                    else self.headers.get("X-Facut-Token", "")
                )
                return secrets.compare_digest(supplied, owner.runtime.token)

            def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
                content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(content)

            def _error(self, status: HTTPStatus, code: str, message: str) -> None:
                self._json(status, {"status": "error", "error": {"code": code, "message": message}})

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._error(HTTPStatus.FORBIDDEN, "INVALID_TOKEN", "Invalid voice service token.")
                    return
                if self.path != "/status":
                    self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Route not found.")
                    return
                self._json(HTTPStatus.OK, owner.runtime.status(owner.host, owner.port))

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._error(HTTPStatus.FORBIDDEN, "INVALID_TOKEN", "Invalid voice service token.")
                    return
                if self.path == "/shutdown":
                    self._json(HTTPStatus.OK, {"status": "success", "stopping": True})
                    threading.Thread(target=owner._httpd.shutdown, daemon=True).start()
                    return
                if self.path != "/synthesize":
                    self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Route not found.")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "INVALID_SIZE", "Synthesis request must be between 1 byte and 4 MiB.")
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    request_payload = payload.get("request")
                    if not isinstance(request_payload, dict):
                        raise ValueError("Synthesis payload requires a request object.")
                    started = time.perf_counter()
                    request_timeout = float(payload.get("timeout") or 300.0)
                    if not 1.0 <= request_timeout <= 3600.0:
                        raise ValueError("Synthesis timeout must be between 1 and 3600 seconds.")
                    response = owner.runtime.worker.request(request_payload, timeout=request_timeout)
                    owner.runtime.touch_synthesis(time.perf_counter() - started)
                except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "VOICE_SYNTHESIS_FAILED", str(error))
                    return
                self._json(HTTPStatus.OK, response)

        return Handler

    def _write_state(self) -> None:
        _atomic_write_json(
            self.state_path,
            {
                "protocol": SERVICE_PROTOCOL,
                "host": self.host,
                "port": self.port,
                "token": self.runtime.token,
                "pid": os.getpid(),
                "started_at": self.runtime.started_at,
            },
        )

    def _idle_monitor(self) -> None:
        while not self.runtime.is_idle():
            time.sleep(min(0.25, max(0.05, self.runtime.idle_timeout / 4)))
        self._httpd.shutdown()

    def serve(self) -> None:
        self._write_state()
        if self.runtime.idle_timeout > 0:
            self._monitor = threading.Thread(target=self._idle_monitor, daemon=True)
            self._monitor.start()
        try:
            self._httpd.serve_forever(poll_interval=0.1)
        finally:
            self.close()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self._httpd.server_close()
        self.runtime.worker.close()
        state = _read_state(self.state_path)
        if state and secrets.compare_digest(str(state.get("token") or ""), self.runtime.token):
            self.state_path.unlink(missing_ok=True)


def _service_request(
    state: dict[str, Any], path: str, *, payload: dict[str, Any] | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    url = f"http://{state['host']}:{int(state['port'])}{path}"
    content = json.dumps(payload, ensure_ascii=True).encode("ascii") if payload is not None else None
    request = Request(
        url,
        data=content,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {state['token']}",
            **({"Content-Type": "application/json"} if content is not None else {}),
        },
    )
    try:
        # Loopback control traffic must never inherit a corporate/system proxy.
        # Besides leaking the bearer token, proxy discovery can make localhost
        # health checks stall on macOS runners and managed workstations.
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            message = detail.get("error", {}).get("message")
        except (ValueError, AttributeError):
            message = None
        raise VoiceServiceError(message or f"Voice service returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise VoiceServiceError("The local voice service is not reachable.") from error


def voice_service_status(*, state_path: str | Path | None = None) -> dict[str, Any]:
    """Return a token-free public status, including stale-state detection."""

    state = _read_state(state_path)
    if not state:
        return {"protocol": SERVICE_PROTOCOL, "running": False, "state": "absent"}
    try:
        return _service_request(state, "/status", timeout=2.0)
    except VoiceServiceError:
        return {
            "protocol": SERVICE_PROTOCOL,
            "running": False,
            "state": "stale",
            "pid": state.get("pid"),
        }


def start_voice_service(
    *,
    provider: str | Path | None = None,
    device: str = "auto",
    require_cuda: bool = False,
    idle_timeout: float = 600.0,
    port: int = 0,
    state_path: str | Path | None = None,
    startup_timeout: float = 240.0,
    daemon_command: list[str] | None = None,
) -> dict[str, Any]:
    """Launch a hidden service process and wait for its authenticated status."""

    resolved_device = resolve_voice_device(device, require_cuda=require_cuda)
    current = voice_service_status(state_path=state_path)
    if current.get("running"):
        if require_cuda and (
            current.get("device") != "cuda" or current.get("cuda") is not True
        ):
            raise VoiceCudaRequiredError(
                "The running voice service has not confirmed CUDA execution.",
                suggestion="Stop it, then start again with `--device cuda --require-cuda`.",
                details={"service_status": current},
            )
        if str(device).casefold() != "auto" and current.get("device") != resolved_device:
            raise VoiceServiceError(
                f"The running voice service uses {current.get('device') or 'an unknown device'}, "
                f"not the requested {resolved_device} device.",
                suggestion=(
                    "Stop the service, then start it again with "
                    f"`facut voice serve start --device {resolved_device}`."
                ),
                details={
                    "requested_device": resolved_device,
                    "service_status": current,
                },
            )
        return {**current, "already_running": True}
    provider_info = provider_status(provider)
    if not provider_info["available"]:
        raise DependencyMissingError(
            "No local voice provider is available for the voice service.",
            suggestion="Configure a provider, or run `facut download voice_model` first.",
            details=provider_info,
        )
    target = Path(state_path or default_service_state_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if daemon_command is None:
        if getattr(sys, "frozen", False):
            daemon_command = [sys.executable, "__voice_service_daemon__"]
        else:
            daemon_command = [sys.executable, "-m", "facut.voice.service"]
    command = [
        *daemon_command,
        "--provider",
        str(provider_info["executable"]),
        "--device",
        resolved_device,
        "--idle-timeout",
        str(idle_timeout),
        "--port",
        str(port),
        "--state",
        str(target),
        *(["--require-cuda"] if require_cuda else []),
    ]
    log_path = target.parent / "voice-service.log"
    log_stream = log_path.open("a", encoding="utf-8")
    environment = _daemon_environment()
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
    log_stream.close()
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error_type = VoiceCudaRequiredError if require_cuda else VoiceServiceError
            message = (
                "CUDA voice service startup failed; the provider did not confirm GPU inference."
                if require_cuda
                else "The voice service exited during startup."
            )
            raise error_type(
                message,
                suggestion=(
                    "Check `nvidia-smi`, the NVIDIA device status, and the voice runtime; "
                    f"then inspect {log_path}."
                    if require_cuda
                    else f"Inspect {log_path} for provider diagnostics."
                ),
                details={"exit_code": process.returncode, "log": str(log_path)},
            )
        state = _read_state(target)
        if state:
            try:
                status = _service_request(state, "/status", timeout=2.0)
                return {**status, "already_running": False}
            except VoiceServiceError:
                pass
        time.sleep(0.1)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    raise VoiceServiceError(
        "The voice service did not become ready before the startup timeout.",
        suggestion=f"Inspect {log_path} for model-loading diagnostics.",
        details={"pid": process.pid, "log": str(log_path)},
    )


def stop_voice_service(*, state_path: str | Path | None = None, timeout: float = 10.0) -> dict[str, Any]:
    state_file = Path(state_path or default_service_state_path()).expanduser().resolve()
    state = _read_state(state_file)
    if not state:
        return {"protocol": SERVICE_PROTOCOL, "running": False, "stopped": False}
    try:
        _service_request(state, "/shutdown", payload={}, timeout=2.0)
    except VoiceServiceError:
        state_file.unlink(missing_ok=True)
        return {"protocol": SERVICE_PROTOCOL, "running": False, "stopped": False, "stale_state_removed": True}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and state_file.exists():
        time.sleep(0.05)
    if not state_file.exists():
        return {"protocol": SERVICE_PROTOCOL, "running": False, "stopped": True}
    pid = int(state.get("pid") or 0)
    if pid <= 0:
        process_alive = False
    else:
        try:
            os.kill(pid, 0)
            process_alive = True
        except OSError:
            process_alive = False
    if not process_alive:
        state_file.unlink(missing_ok=True)
        return {
            "protocol": SERVICE_PROTOCOL,
            "running": False,
            "stopped": True,
            "stale_state_removed": True,
        }
    raise VoiceServiceError(
        "The voice service did not stop before the timeout.",
        suggestion="Retry `facut cleanram --service voice`, then inspect `facut voice serve status`.",
        details={"pid": pid, "timeout_seconds": timeout},
    )


def synthesize_with_voice_service(
    profile: VoiceProfile,
    profile_directory: str | Path,
    lines: list[dict[str, Any]],
    output_directory: str | Path,
    *,
    provider: str | Path | None = None,
    state_path: str | Path | None = None,
    auto_start: bool = True,
    device: str = "auto",
    require_cuda: bool = False,
    idle_timeout: float = 600.0,
    timeout: float = 300.0,
    daemon_command: list[str] | None = None,
) -> dict[str, Any]:
    """Synthesize through the warm daemon and retain provider output validation."""

    resolved_device = resolve_voice_device(device, require_cuda=require_cuda)
    state = _read_state(state_path)
    current = voice_service_status(state_path=state_path)
    if not state or not current.get("running"):
        if not auto_start:
            raise VoiceServiceError(
                "The local voice service is not running.",
                suggestion="Run `facut voice serve start`.",
            )
        start_voice_service(
            provider=provider,
            device=resolved_device,
            require_cuda=require_cuda,
            idle_timeout=idle_timeout,
            state_path=state_path,
            daemon_command=daemon_command,
        )
        state = _read_state(state_path)
        current = voice_service_status(state_path=state_path)
    if require_cuda and (
        current.get("device") != "cuda" or current.get("cuda") is not True
    ):
        raise VoiceCudaRequiredError(
            "The running voice service has not confirmed CUDA execution.",
            suggestion="Stop it, then start again with `--device cuda --require-cuda`.",
            details={"service_status": current},
        )
    if str(device).casefold() != "auto" and current.get("device") != resolved_device:
        raise VoiceServiceError(
            f"The running voice service uses {current.get('device') or 'an unknown device'}, "
            f"not the requested {resolved_device} device.",
            suggestion=(
                "Stop the service, then start it again with "
                f"`facut voice serve start --device {resolved_device}`."
            ),
            details={
                "requested_device": resolved_device,
                "service_status": current,
            },
        )
    assert state is not None
    request, destination = build_provider_request(
        profile, profile_directory, lines, output_directory
    )
    payload = _service_request(
        state, "/synthesize", payload={"request": request, "timeout": timeout}, timeout=timeout + 5
    )
    return validate_provider_response(
        payload, destination, profile.id, request=request
    )


def _daemon_entry(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--idle-timeout", type=float, default=600.0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state", required=True)
    options = parser.parse_args(argv)
    server = VoiceServiceServer(
        options.provider,
        device=options.device,
        require_cuda=options.require_cuda,
        idle_timeout=options.idle_timeout,
        port=options.port,
        state_path=options.state,
    )
    server.serve()


if __name__ == "__main__":
    _daemon_entry()


__all__ = [
    "SERVICE_PROTOCOL",
    "VoiceCudaRequiredError",
    "VoiceServiceError",
    "VoiceServiceServer",
    "default_service_state_path",
    "start_voice_service",
    "stop_voice_service",
    "synthesize_with_voice_service",
    "voice_service_status",
]
