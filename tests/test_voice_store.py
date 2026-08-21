from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import wave

import pytest
from pydantic import ValidationError

from facut.voice import VoiceProfileStore
from facut.voice import store as voice_store
from facut.voice.store import VoiceAliasConflictError, VoiceProfileAmbiguousError


def _wav(path: Path, *, seconds: float = 1.0, rate: int = 48000) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        frames = bytearray()
        for index in range(round(seconds * rate)):
            value = round(math.sin(2 * math.pi * 220 * index / rate) * 6000)
            frames.extend(struct.pack("<h", value))
        stream.writeframes(frames)
    return path


def test_multiple_profiles_import_deduplicate_and_recoverable_delete(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    first = store.create(
        "我的自然口播",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    second = store.create(
        "我的纪录片口播",
        speaker_id="self",
        style="travel-documentary",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    assert first.id != second.id
    source = _wav(tmp_path / "take.wav")
    imported = store.import_samples(
        first.id,
        [source, source],
        transcript="测试录音",
        category="friendly-chat",
        delivery="chat",
    )
    assert len(imported.samples) == 1
    assert not Path(imported.samples[0].stored_path).is_absolute()
    assert store.sample_paths(imported)[0].is_file()
    assert imported.samples[0].category == "friendly-chat"
    assert imported.samples[0].delivery == "chat"
    assert len(store.list()) == 2
    profile_json = tmp_path / "voices" / "profiles" / first.id / "profile.json"
    payload = json.loads(profile_json.read_text(encoding="utf-8"))
    assert payload["consent"]["relationship"] == "self"
    public = imported.public_dict()
    assert "statement" not in public["consent"]
    assert len(public["consent"]["statement_sha256"]) == 64
    assert not list(profile_json.parent.glob("*.tmp"))
    trashed = store.delete(first.id)
    assert trashed.is_dir()
    assert ".trash" in trashed.parts
    with pytest.raises(FileNotFoundError):
        store.get(first.id)
    restored = store.restore(trashed.name)
    assert restored.id == first.id
    assert store.sample_paths(restored)[0].is_file()


def test_profile_requires_explicit_consent_statement(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    with pytest.raises(ValidationError):
        store.create(
            "Invalid",
            speaker_id="self",
            consent_relationship="self",
            consent_statement="too short",
        )


def _fake_platform_data_path(base: Path):
    def fake_user_data_path(
        appname: str,
        appauthor: str | bool | None = None,
        **_kwargs: object,
    ) -> Path:
        assert appname == "facut"
        return base / "facut" if appauthor is False else base / "facut" / "facut"

    return fake_user_data_path


def test_default_home_uses_single_facut_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACUT_VOICE_HOME", raising=False)
    monkeypatch.setattr(
        voice_store, "user_data_path", _fake_platform_data_path(tmp_path / "local")
    )

    assert voice_store.default_voice_home() == (tmp_path / "local" / "facut" / "voices").resolve()


def test_default_store_non_destructively_migrates_legacy_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACUT_VOICE_HOME", raising=False)
    monkeypatch.setattr(
        voice_store, "user_data_path", _fake_platform_data_path(tmp_path / "local")
    )
    legacy = tmp_path / "local" / "facut" / "facut" / "voices"
    old_store = VoiceProfileStore(legacy)
    profile = old_store.create(
        "旧版自然口播",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    source = _wav(tmp_path / "legacy-take.wav")
    old_store.import_samples(profile.id, [source], transcript="旧版录音")

    store = VoiceProfileStore()

    canonical = (tmp_path / "local" / "facut" / "voices").resolve()
    assert store.root == canonical
    migrated = store.get(profile.id)
    assert store.sample_paths(migrated)[0].is_file()
    # Migration is a copy: rollback-compatible legacy data remains untouched.
    assert (legacy / "profiles" / profile.id / "profile.json").is_file()
    assert old_store.sample_paths(old_store.get(profile.id))[0].is_file()


def test_migration_does_not_overwrite_canonical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACUT_VOICE_HOME", raising=False)
    monkeypatch.setattr(
        voice_store, "user_data_path", _fake_platform_data_path(tmp_path / "local")
    )
    canonical = tmp_path / "local" / "facut" / "voices"
    legacy = tmp_path / "local" / "facut" / "facut" / "voices"
    relative = Path("profiles") / "voice_CONFLICT" / "profile.json"
    (canonical / relative).parent.mkdir(parents=True)
    (legacy / relative).parent.mkdir(parents=True)
    (canonical / relative).write_text("canonical", encoding="utf-8")
    (legacy / relative).write_text("legacy", encoding="utf-8")

    VoiceProfileStore()

    assert (canonical / relative).read_text(encoding="utf-8") == "canonical"
    assert (legacy / relative).read_text(encoding="utf-8") == "legacy"


def test_migration_failure_falls_back_to_legacy_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACUT_VOICE_HOME", raising=False)
    monkeypatch.setattr(
        voice_store, "user_data_path", _fake_platform_data_path(tmp_path / "local")
    )
    legacy = tmp_path / "local" / "facut" / "facut" / "voices"
    old_store = VoiceProfileStore(legacy)
    profile = old_store.create(
        "回退口播",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )

    def fail_migration(_legacy: Path, _canonical: Path) -> None:
        raise PermissionError("migration blocked")

    monkeypatch.setattr(voice_store, "_merge_legacy_voice_home", fail_migration)
    with pytest.warns(RuntimeWarning, match="continuing with the legacy store"):
        store = VoiceProfileStore()

    assert store.root == legacy.resolve()
    assert store.get(profile.id).display_name == "回退口播"


def test_voice_home_override_skips_platform_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-voices"
    monkeypatch.setenv("FACUT_VOICE_HOME", str(override))

    def unexpected_platform_lookup(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("platform voice paths must not be used with an explicit override")

    monkeypatch.setattr(voice_store, "user_data_path", unexpected_platform_lookup)
    store = VoiceProfileStore()

    assert store.root == override.resolve()


def test_selector_alias_rename_and_ambiguous_display_name(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    first = store.create(
        "旅行旁白",
        speaker_id="zion",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    second = store.create(
        "旅行旁白",
        speaker_id="family",
        consent_relationship="authorized",
        consent_statement="The speaker explicitly authorizes local FACUT voice synthesis.",
    )
    with pytest.raises(VoiceProfileAmbiguousError) as ambiguous:
        store.resolve("旅行旁白")
    assert ambiguous.value.code == "VOICE_AMBIGUOUS"

    store.set_alias(first.id, "zion")
    assert store.resolve("ZION").id == first.id
    renamed = store.rename("zion", "Zion 自然口播")
    assert renamed.id == first.id
    assert store.resolve("Zion 自然口播").id == first.id
    assert store.get(first.id).aliases == ["zion"]
    with pytest.raises(VoiceAliasConflictError):
        store.set_alias(second.id, "ZION")


def test_defaults_are_stable_ids_and_project_default_wins(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    global_profile = store.create(
        "默认声音",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    project_profile = store.create(
        "项目声音",
        speaker_id="self-project",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    project = tmp_path / "demo"
    project.mkdir()
    store.set_default(global_profile.id)
    store.set_default(project_profile.id, scope="project", project=project)

    assert store.get_default().id == global_profile.id
    assert store.get_default(project=project).id == project_profile.id
    payload = json.loads((project / ".facut" / "voice-default.json").read_text("utf-8"))
    assert payload["profile_id"] == project_profile.id
    assert "display_name" not in payload


def test_profile_metadata_updates_do_not_modify_raw_samples(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "原始声音",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    imported = store.import_samples(profile.id, [_wav(tmp_path / "raw.wav")])
    sample_path = store.sample_paths(imported)[0]
    before = sample_path.read_bytes()
    store.set_alias(profile.id, "raw-voice")
    store.rename("raw-voice", "改名后声音")
    store.set_default("raw-voice")

    assert sample_path.read_bytes() == before


def test_candidate_audio_is_quarantined_until_same_speaker_confirmation(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "Roger",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    source = _wav(tmp_path / "possible-chat.wav", seconds=3.2)
    original = source.read_bytes()
    candidate = store.propose_candidate(
        profile.id,
        source,
        transcript="其实这里还挺好看的。",
        category="conversation",
        delivery="daily-chat",
        source_media_id="media_01",
        source_start=12.0,
        source_end=15.2,
    )
    assert candidate.status == "pending"
    assert store.get(profile.id).samples == []
    assert candidate.public_dict()["confirmation_statement_recorded"] is False
    with pytest.raises(PermissionError):
        store.approve_candidate(
            candidate.id,
            speaker_confirmed=False,
            confirmation_statement="This has not actually been confirmed.",
        )
    approved, updated = store.approve_candidate(
        candidate.id,
        speaker_confirmed=True,
        confirmation_statement="I confirm this recording is the authorized speaker Roger.",
    )
    assert approved.status == "approved"
    assert approved.public_dict()["confirmation_statement_recorded"] is True
    assert "confirmation_statement" not in approved.public_dict()
    assert len(updated.samples) == 1
    assert updated.samples[0].delivery == "daily-chat"
    assert source.read_bytes() == original


def test_rejected_candidate_never_enters_profile_samples(tmp_path: Path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "Candidate review",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )
    candidate = store.propose_candidate(profile.id, _wav(tmp_path / "not-speaker.wav"))
    rejected = store.reject_candidate(candidate.id, reason="The speaker is another person.")
    assert rejected.status == "rejected"
    assert store.get(profile.id).samples == []
