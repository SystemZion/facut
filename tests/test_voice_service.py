from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import textwrap
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from facut.voice.models import VoiceProfile
from facut.voice.service import (
    VoiceCudaRequiredError,
    VoiceServiceServer,
    start_voice_service,
    stop_voice_service,
    synthesize_with_voice_service,
    voice_service_status,
)
from facut.voice.store import VoiceProfileStore


def _fake_provider(tmp_path: Path, *, device: str = "cuda") -> Path:
    script = tmp_path / "fake_voice_provider.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json, os, pathlib, struct, sys, wave

            DEVICE = {device!r}

            def synthesize(request):
                root = pathlib.Path(request["output_directory"])
                root.mkdir(parents=True, exist_ok=True)
                outputs = []
                for index, line in enumerate(request["lines"]):
                    path = root / f"fake-{{index}}.wav"
                    with wave.open(str(path), "wb") as stream:
                        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
                        stream.writeframes(struct.pack("<h", 0) * 160)
                    outputs.append({{"output": str(path), "line_index": index,
                                    "reference_sample_id": "sample_TEST"}})
                return {{"status": "success", "provider": "fake-warm",
                        "model_version": "test", "outputs": outputs, "warnings": []}}

            if "--facut-voice-jsonl" in sys.argv:
                print(json.dumps({{"status": "ready",
                    "protocol": "facut-voice-provider-jsonl/1.0",
                    "provider": "fake-warm", "model_version": "test",
                    "model_path": "fake-model", "device": DEVICE,
                    "compute_type": "float16" if DEVICE == "cuda" else "float32",
                    "cuda": DEVICE == "cuda", "gpu": "Fake GPU" if DEVICE == "cuda" else None,
                    "vram": {{"allocated_mib": 12.0}} if DEVICE == "cuda" else None,
                    "requested_device": os.environ.get("FACUT_VOICE_DEVICE"),
                    "require_cuda_env": os.environ.get("FACUT_VOICE_REQUIRE_CUDA"),
                    "persistent": True}}), flush=True)
                for raw in sys.stdin:
                    request = json.loads(raw)
                    if request.get("action") == "shutdown": break
                    print(json.dumps(synthesize(request)), flush=True)
            elif "--facut-voice-json" in sys.argv:
                print(json.dumps(synthesize(json.load(sys.stdin))))
            else:
                raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        os.environ["FACUT_TEST_PYTHON"] = sys.executable
        wrapper = tmp_path / "fake-provider.cmd"
        wrapper.write_text('@"%FACUT_TEST_PYTHON%" "%~dp0fake_voice_provider.py" %*\n', encoding="ascii")
    else:
        wrapper = tmp_path / "fake-provider"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def _profile(tmp_path: Path) -> tuple[VoiceProfileStore, VoiceProfile]:
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "Test voice",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I explicitly authorize local test voice synthesis.",
    )
    return store, profile


def _wait_for_state(path: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not path.is_file():
        time.sleep(0.02)
    assert path.is_file()


def test_loopback_service_requires_token_and_validates_outputs(tmp_path: Path) -> None:
    provider = _fake_provider(tmp_path)
    state = tmp_path / "service.json"
    store, profile = _profile(tmp_path)
    server = VoiceServiceServer(
        provider, state_path=state, idle_timeout=30, require_cuda=True
    )
    thread = server.start_background()
    _wait_for_state(state)
    with pytest.raises(HTTPError) as denied:
        urlopen(server.url + "status", timeout=2)
    assert denied.value.code == 403

    result = synthesize_with_voice_service(
        profile,
        store.profile_directory(profile.id),
        [{"id": "line_1", "draft_text": "你好"}],
        tmp_path / "output",
        state_path=state,
        auto_start=False,
    )
    assert result["provider"] == "fake-warm"
    assert len(result["outputs"][0]["sha256"]) == 64
    status = voice_service_status(state_path=state)
    assert status["running"] is True
    assert status["device"] == "cuda"
    assert status["persistent_provider"] is True
    assert status["request_count"] == 1
    assert "token" not in status

    stopped = stop_voice_service(state_path=state)
    thread.join(timeout=5)
    assert stopped["stopped"] is True
    assert not thread.is_alive()
    assert not state.exists()


def test_service_stops_after_idle_timeout(tmp_path: Path) -> None:
    state = tmp_path / "idle.json"
    server = VoiceServiceServer(
        _fake_provider(tmp_path), state_path=state, idle_timeout=0.2
    )
    thread = server.start_background()
    _wait_for_state(state)
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert not state.exists()


def test_stop_service_reports_timeout_when_process_remains_alive(tmp_path: Path, monkeypatch) -> None:
    from facut.voice.service import VoiceServiceError

    state = tmp_path / "stuck.json"
    state.write_text(
        json.dumps(
            {
                "protocol": "facut-voice-service/1.0",
                "host": "127.0.0.1",
                "port": 12345,
                "token": "x" * 32,
                "pid": 54321,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("facut.voice.service._service_request", lambda *args, **kwargs: {})
    monkeypatch.setattr("facut.voice.service.os.kill", lambda *args, **kwargs: None)
    with pytest.raises(VoiceServiceError, match="did not stop"):
        stop_voice_service(state_path=state, timeout=0)
    assert state.exists()


def test_require_cuda_rejects_cpu_provider(tmp_path: Path) -> None:
    with pytest.raises(VoiceCudaRequiredError, match="CUDA"):
        VoiceServiceServer(
            _fake_provider(tmp_path, device="cpu"),
            state_path=tmp_path / "cpu.json",
            require_cuda=True,
        )


def test_provider_process_does_not_inherit_facut_python_runtime_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong-python-home"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "wrong-python-path"))
    server = VoiceServiceServer(
        _fake_provider(tmp_path), state_path=tmp_path / "isolated.json", idle_timeout=30
    )
    try:
        assert server.runtime.worker.ready["status"] == "ready"
    finally:
        server.close()


def test_service_auto_uses_cpu_overlay_and_clears_parent_cuda_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACUT_CPU_TORCH_OVERLAY", str(tmp_path / "cpu-overlay"))
    monkeypatch.setenv("FACUT_VOICE_REQUIRE_CUDA", "1")
    server = VoiceServiceServer(
        _fake_provider(tmp_path, device="cpu"),
        state_path=tmp_path / "cpu-overlay.json",
        idle_timeout=30,
    )
    try:
        assert server.runtime.worker.device == "cpu"
        assert server.runtime.worker.ready["requested_device"] == "cpu"
        assert server.runtime.worker.ready["require_cuda_env"] == "0"
    finally:
        server.close()


def test_service_oneshot_fallback_preserves_requested_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from facut.voice.service import ProviderWorker

    captured = {}

    def fake_invoke(_executable, _payload, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "outputs": []}

    worker = ProviderWorker(tmp_path / "provider", device="cuda")
    worker.mode = "oneshot"
    monkeypatch.setattr("facut.voice.service.invoke_provider_request", fake_invoke)

    worker.request({"protocol": "test"}, timeout=12.5)

    assert captured == {"timeout": 12.5, "device": "cuda", "require_cuda": False}


def test_frozen_daemon_uses_an_independent_pyinstaller_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from facut.voice import service

    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    environment = service._daemon_environment()

    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_source_daemon_only_exports_facut_import_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from facut.voice import service

    monkeypatch.delattr(service.sys, "frozen", raising=False)
    environment = service._daemon_environment()

    assert environment["PYTHONPATH"] == str(Path(service.__file__).resolve().parents[2])
    assert os.pathsep not in environment["PYTHONPATH"]


def test_require_cuda_rejects_an_already_running_cpu_service(tmp_path: Path) -> None:
    state = tmp_path / "cpu-running.json"
    server = VoiceServiceServer(
        _fake_provider(tmp_path, device="cpu"), state_path=state, idle_timeout=30
    )
    thread = server.start_background()
    _wait_for_state(state)
    try:
        with pytest.raises(VoiceCudaRequiredError, match="CUDA"):
            start_voice_service(
                provider=_fake_provider(tmp_path),
                require_cuda=True,
                state_path=state,
            )
    finally:
        stop_voice_service(state_path=state)
        thread.join(timeout=5)


def test_explicit_device_rejects_an_already_running_service_on_another_device(
    tmp_path: Path,
) -> None:
    from facut.voice.service import VoiceServiceError

    state = tmp_path / "device-mismatch.json"
    server = VoiceServiceServer(
        _fake_provider(tmp_path, device="cpu"), state_path=state, idle_timeout=30
    )
    thread = server.start_background()
    _wait_for_state(state)
    try:
        with pytest.raises(VoiceServiceError, match="not the requested cuda"):
            start_voice_service(
                provider=_fake_provider(tmp_path),
                device="cuda",
                state_path=state,
            )
    finally:
        stop_voice_service(state_path=state)
        thread.join(timeout=5)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "GitHub hosted macOS runners do not support this nested long-lived daemon "
        "lifecycle; in-process server and provider protocol tests still run."
    ),
)
def test_start_status_stop_manage_a_hidden_daemon(tmp_path: Path) -> None:
    state = tmp_path / "daemon.json"
    try:
        started = start_voice_service(
            provider=_fake_provider(tmp_path),
            device="cuda",
            require_cuda=True,
            idle_timeout=30,
            state_path=state,
            startup_timeout=20,
        )
    except Exception as error:
        log = state.parent / "voice-service.log"
        diagnostics = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "<missing>"
        provider_log = state.parent / "voice-provider.log"
        provider_diagnostics = (
            provider_log.read_text(encoding="utf-8", errors="replace")
            if provider_log.is_file() else "<missing>"
        )
        state_diagnostics = (
            state.read_text(encoding="utf-8", errors="replace")
            if state.is_file() else "<missing>"
        )
        pytest.fail(
            f"{error}\nvoice-service.log:\n{diagnostics}"
            f"\nvoice-provider.log:\n{provider_diagnostics}"
            f"\ndaemon.json:\n{state_diagnostics}"
        )
    try:
        assert started["running"] is True
        assert started["already_running"] is False
        assert started["device"] == "cuda"
        assert voice_service_status(state_path=state)["pid"] == started["pid"]
    finally:
        stopped = stop_voice_service(state_path=state)
    assert stopped["stopped"] is True


def test_hidden_daemon_preserves_cuda_required_error_code(tmp_path: Path) -> None:
    with pytest.raises(VoiceCudaRequiredError, match="did not confirm GPU"):
        start_voice_service(
            provider=_fake_provider(tmp_path, device="cpu"),
            device="cuda",
            require_cuda=True,
            state_path=tmp_path / "daemon-cpu.json",
            startup_timeout=20,
        )


def test_stale_state_does_not_expose_its_token(tmp_path: Path) -> None:
    state = tmp_path / "stale.json"
    state.write_text(
        json.dumps({
            "protocol": "facut-voice-service/1.0", "host": "127.0.0.1",
            "port": 1, "pid": 123, "token": "s" * 40, "started_at": "now",
        }),
        encoding="utf-8",
    )
    status = voice_service_status(state_path=state)
    assert status == {
        "protocol": "facut-voice-service/1.0",
        "running": False,
        "state": "stale",
        "pid": 123,
    }
