from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from facut.native.client import (
    NativeClient,
    NativeProtocolError,
    batch_failure_warnings,
    collect_batch_inputs,
    discover_native,
)
from facut.exceptions import MediaProbeError


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


def test_discover_native_prefers_explicit_environment(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    monkeypatch.setenv("FACUT_NATIVE", str(executable))
    assert discover_native() == executable.resolve()


def test_native_client_validates_ready_and_collects_complete(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    task_id = "task-fixed"
    process = _FakeProcess(
        [
            {"event": "ready", "protocol": 1, "version": "0.8.2"},
            {"event": "progress", "protocol": 1, "task_id": task_id, "progress": 1.0},
            {
                "event": "complete",
                "protocol": 1,
                "task_id": task_id,
                "data": {"results": []},
            },
        ]
    )
    monkeypatch.setattr(
        "facut.native.client.subprocess.Popen", lambda *args, **kwargs: process
    )
    result = NativeClient(executable)._run(
        {"task_id": task_id, "action": "media.batch_scan"}
    )
    assert result["task_id"] == task_id
    assert result["results"] == []
    written = json.loads(process.stdin.getvalue())
    assert written["protocol"] == 1


def test_native_client_rejects_incompatible_protocol(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    process = _FakeProcess([{"event": "ready", "protocol": 2}])
    monkeypatch.setattr(
        "facut.native.client.subprocess.Popen", lambda *args, **kwargs: process
    )
    with pytest.raises(NativeProtocolError, match="incompatible"):
        NativeClient(executable).doctor()
    assert process.killed is True


def test_batch_scan_restarts_native_once(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "facut-native.exe"
    executable.write_bytes(b"fake")
    client = NativeClient(executable)
    calls = 0

    def flaky_run(_request, *, progress=None, timeout=None):
        nonlocal calls
        del progress, timeout
        calls += 1
        if calls == 1:
            raise NativeProtocolError("decoder crashed")
        return {"results": [], "elapsed_seconds": 0.01}

    monkeypatch.setattr(client, "_run", flaky_run)
    result = client.batch_scan(
        [{"media_id": "media_01", "path": str(tmp_path / "one.mp4")}],
        output_directory=tmp_path / "results",
    )
    assert calls == 2
    assert Path(result["result_file"]).is_file()


def test_batch_failure_contract_warns_for_partial_results() -> None:
    warnings = batch_failure_warnings(
        {
            "succeeded": 1,
            "failed": 1,
            "failures": [{"media_id": "media_bad", "error": "invalid"}],
        }
    )

    assert warnings == [
        "1 of 2 media assets failed analysis; inspect `data.failures` before treating the batch as complete."
    ]


def test_batch_failure_contract_rejects_all_failed_results() -> None:
    with pytest.raises(MediaProbeError, match="All 2 media assets failed") as captured:
        batch_failure_warnings(
            {
                "succeeded": 0,
                "failed": 2,
                "failures": [{"media_id": "media_bad", "error": "invalid"}],
            }
        )

    assert captured.value.details["failures"][0]["media_id"] == "media_bad"


def test_collect_batch_inputs_links_lrf_and_excludes_generated_output(
    tmp_path: Path,
) -> None:
    original = tmp_path / "DJI_0001_D.MP4"
    proxy = tmp_path / "DJI_0001_D.LRF"
    photo = tmp_path / "DJI_0002_D.JPG"
    output = tmp_path / "成片" / "project" / "analysis"
    output.mkdir(parents=True)
    rendered = tmp_path / "成片" / "trip-final.mp4"
    for path in (original, proxy, photo, output / "frame_01.jpg", rendered):
        path.write_bytes(b"media")

    inputs, summary = collect_batch_inputs(tmp_path, output_directory=output)

    assert len(inputs) == 2
    video = next(
        item for item in inputs if item["original_path"] == str(original.resolve())
    )
    assert video["path"] == str(proxy.resolve())
    assert video["used_proxy"] is True
    assert summary["proxy_linked"] == 1
    assert summary["proxy_candidates_excluded"] == 1
    assert summary["generated_files_excluded"] == 2


def test_collect_batch_inputs_excludes_versioned_analysis_and_project_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "成片" / "project" / "analysis-v4-cold"
    output.mkdir(parents=True)
    (output / "frame_01.jpg").write_bytes(b"generated")
    old = tmp_path / "analysis-v3-cold"
    old.mkdir()
    (old / "frame_02.jpg").write_bytes(b"generated")
    (tmp_path / "captions.srt").write_text("subtitle", encoding="utf-8")
    (tmp_path / "music.mp3").write_bytes(b"audio")

    inputs, summary = collect_batch_inputs(tmp_path, output_directory=output)

    assert [Path(item["original_path"]).name for item in inputs] == ["clip.mp4"]
    assert summary["generated_files_excluded"] == 2
