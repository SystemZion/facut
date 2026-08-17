from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from facut.exceptions import NotImplementedFacutError
from facut.voice import VoiceProfileStore, synthesize_with_provider
from facut.voice.providers import invoke_provider_request


def test_synthesis_requires_explicit_local_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FACUT_VOICE_PROVIDER", raising=False)
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "isolated-voices"))
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "My voice",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    with pytest.raises(NotImplementedFacutError, match="No local"):
        synthesize_with_provider(
            profile,
            store.profile_directory(profile.id),
            [{"id": "line_1", "draft_text": "你好"}],
            tmp_path / "previews",
        )


def test_one_shot_provider_auto_uses_configured_cpu_overlay(monkeypatch) -> None:
    captured = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "success", "outputs": []}),
            stderr="",
        )

    monkeypatch.setenv("FACUT_CPU_TORCH_OVERLAY", "C:/facut/cpu")
    monkeypatch.setattr("facut.voice.providers.subprocess.run", fake_run)
    invoke_provider_request("provider", {"protocol": "test"}, device="auto")

    assert captured["FACUT_VOICE_DEVICE"] == "cpu"
    assert captured["FACUT_VOICE_REQUIRE_CUDA"] == "0"


def test_provider_crash_without_stderr_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(
        "facut.voice.providers.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=-1073741819, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="runtime may have crashed"):
        invoke_provider_request("provider", {}, device="cpu")
