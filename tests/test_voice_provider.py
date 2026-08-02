from __future__ import annotations

import pytest

from facut.exceptions import NotImplementedFacutError
from facut.voice import VoiceProfileStore, synthesize_with_provider


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
