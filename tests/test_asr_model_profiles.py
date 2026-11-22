from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli import analyze_commands
from facut.cli.main import app


def test_quality_profile_selects_only_linked_local_model_and_detects_replacement(
    tmp_path, monkeypatch
):
    runner = CliRunner()
    monkeypatch.setenv("FACUT_CONFIG", str(tmp_path / "settings.toml"))
    media = tmp_path / "sample.wav"
    media.write_bytes(b"fake test fixture")
    model = tmp_path / "quality"
    model.mkdir()

    def run(*args):
        return runner.invoke(app, ["--json", *map(str, args)])

    def analyze(*options):
        return run("analyze", "transcript", media, *options)

    seen = []

    def fake_transcribe(path, *, model_path, **kwargs):
        seen.append(model_path)
        return {"segments": []}

    captured_cache = []
    original_emit = analyze_commands._emit_analysis

    def capture_cache(*args, **kwargs):
        captured_cache.append(kwargs["cache_parameters"])
        return original_emit(*args, **kwargs)

    monkeypatch.setattr(analyze_commands, "transcribe_local", fake_transcribe)
    monkeypatch.setattr(analyze_commands, "_emit_analysis", capture_cache)

    missing = analyze("--model-profile", "quality")
    assert missing.exit_code != 0 and "No quality ASR model" in missing.output
    incomplete = run("models", "link", "srt_quality", model)
    assert incomplete.exit_code != 0 and "tokenizer.json" in incomplete.output
    assert not seen

    for name in ("config.json", "model.bin", "tokenizer.json"):
        (model / name).write_bytes(b"a")
    linked = run("models", "link", "srt_quality", model)
    assert linked.exit_code == 0, linked.output
    status = run("models", "status")
    assert status.exit_code == 0
    assert json.loads(status.stdout)["data"]["srt_quality"]["available"] is True
    both = analyze("--model", model, "--model-profile", "quality")
    assert both.exit_code != 0 and "not both" in both.output
    first = analyze("--model-profile", "quality")
    assert first.exit_code == 0, first.output
    assert seen[-1] == model.resolve()
    (model / "model.bin").write_bytes(b"replaced")
    second = analyze("--model-profile", "quality")
    assert second.exit_code == 0, second.output
    assert captured_cache[0]["model_stamp"] != captured_cache[1]["model_stamp"]
    assert analyze().exit_code == 0
    assert seen[-1] != model.resolve()
