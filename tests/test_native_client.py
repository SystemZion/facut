from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from facut.native.client import NativeClient, NativeProtocolError, discover_native


class _CaptureStringIO(StringIO):
    def close(self) -> None:
        self.flush()


class _FakeProcess:
    def __init__(self, lines: list[dict], returncode: int = 0) -> None:
        self.stdin = _CaptureStringIO()
        self.stdout = StringIO("".join(json.dumps(line) + "\n" for line in lines))
        self.stderr = StringIO("")
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def test_discover_native_prefers_explicit_environment(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    monkeypatch.setenv("FACUT_NATIVE", str(executable))
    assert discover_native() == executable.resolve()


def test_native_client_validates_ready_and_collects_complete(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    task_id = "task-fixed"
    process = _FakeProcess(
        [
            {"event": "ready", "protocol": 1, "version": "0.8.2"},
            {"event": "progress", "protocol": 1, "task_id": task_id, "progress": 1.0},
            {"event": "complete", "protocol": 1, "task_id": task_id, "data": {"results": []}},
        ]
    )
    monkeypatch.setattr("facut.native.client.subprocess.Popen", lambda *args, **kwargs: process)
    result = NativeClient(executable)._run({"task_id": task_id, "action": "media.batch_scan"})
    assert result["task_id"] == task_id
    assert result["results"] == []
    written = json.loads(process.stdin.getvalue())
    assert written["protocol"] == 1


def test_native_client_rejects_incompatible_protocol(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    process = _FakeProcess([{"event": "ready", "protocol": 2}])
    monkeypatch.setattr("facut.native.client.subprocess.Popen", lambda *args, **kwargs: process)
    with pytest.raises(NativeProtocolError, match="incompatible"):
        NativeClient(executable).doctor()
    assert process.killed is True


def test_batch_scan_restarts_native_once(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    client = NativeClient(executable)
    calls = 0

    def flaky_run(_request, *, progress=None):
        nonlocal calls
        del progress
        calls += 1
        if calls == 1:
            raise NativeProtocolError("decoder crashed")
        return {"results": [], "elapsed_seconds": 0.01}

    monkeypatch.setattr(client, "_run", flaky_run)
    result = client.batch_scan([], output_directory=tmp_path / "results")
    assert calls == 2
    assert Path(result["result_file"]).is_file()
