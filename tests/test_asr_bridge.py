from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from facut.analysis import engine


def test_external_asr_bridge_is_used_when_frozen_package_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "audio.wav"
    source.write_bytes(b"RIFF")
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(engine.shutil, "which", lambda name: "python.exe")
    payload = {
        "source": str(source),
        "language": "zh",
        "device": "cuda",
        "segments": [],
    }
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps(payload), ""
        ),
    )

    result = engine.transcribe_local(source, model_path=model)

    assert result["device"] == "cuda"
