from __future__ import annotations

from pathlib import Path
import struct
import io
import wave

import pytest

from facut.core.models import (
    MediaAsset,
    MediaKind,
    MediaTechnicalInfo,
    ProjectDocument,
    ProjectSettings,
)
from facut.core.timeline_engine import TimelineEngine, TimelineError
from facut.render.graph_builder import GraphBuilder
from facut.voice.models import ConsentRecord, VoiceProfile
from facut.voice.providers import build_provider_request, validate_provider_response
from facut.voice.service import ProviderWorker
from facut.voice.store import VoiceProfileStore, VoiceSpeakerGuardError
from facut.voice.verification import factual_numbers, verify_transcript


def _document(tmp_path: Path) -> ProjectDocument:
    source = tmp_path / "original.mp4"
    source.write_bytes(b"original")
    return ProjectDocument(
        project=ProjectSettings(name="p0", fps=30),
        media=[
            MediaAsset(
                id="media_01",
                kind=MediaKind.VIDEO,
                path=source.name,
                original_name=source.name,
                size=source.stat().st_size,
                sha256="a" * 64,
                technical=MediaTechnicalInfo(duration=10, audio_codec="aac"),
                metadata={
                    "protected_spans": [
                        {"id": "speech_1", "type": "speech", "start": 2, "end": 5}
                    ]
                },
            )
        ],
    )


def _wav(path: Path, value: int = 100) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(struct.pack("<h", value) * 1600)
    return path


def test_protected_speech_span_blocks_split_and_trim_without_mutating(tmp_path: Path) -> None:
    document = _document(tmp_path)
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    clip = engine.add_clip("media_01", "V1", source_in=0, source_out=10)

    with pytest.raises(TimelineError, match="protected speech"):
        engine.split_clip(clip.id, 3)
    assert clip.source_out == 10
    assert len(document.find_track("V1").clips) == 1

    with pytest.raises(TimelineError, match="protected speech"):
        engine.trim_clip(clip.id, source_out=4)
    assert clip.source_in == 0
    assert clip.source_out == 10

    left, right = engine.split_clip(clip.id, 3, allow_protected_cut=True)
    assert (left.source_out, right.source_in) == (3, 3)


def test_preview_proxy_is_video_only_and_original_supplies_audio(tmp_path: Path) -> None:
    document = _document(tmp_path)
    proxy = tmp_path / "original_LRF.mp4"
    proxy.write_bytes(b"silent-proxy")
    document.media[0].proxy_path = proxy.name
    document.media[0].metadata["proxy"] = {"audio_untrusted": True}
    engine = TimelineEngine(document)
    engine.add_track("video", "V1")
    engine.add_clip("media_01", "V1", source_in=0, source_out=2)

    graph = GraphBuilder(document, tmp_path).build(preview=True)

    assert graph.source_paths == (proxy.resolve(), (tmp_path / "original.mp4").resolve())
    assert "[0:v:0]trim" in graph.filter_complex
    assert "[1:a:0]atrim" in graph.filter_complex


def test_voice_import_requires_identity_guard_and_sample_remove_is_recoverable(
    tmp_path: Path,
) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile = store.create(
        "Roger", speaker_id="roger", consent_relationship="self",
        consent_statement="I authorize this local voice profile for testing.",
    )
    first = store.import_samples(profile.id, [_wav(tmp_path / "first.wav")])
    assert first.samples[0].identity_verification["basis"] == "initial_sample"

    with pytest.raises(VoiceSpeakerGuardError, match="was blocked"):
        store.import_samples(profile.id, [_wav(tmp_path / "other.wav", 200)])

    updated = store.import_samples(
        profile.id, [_wav(tmp_path / "same.wav", 300)], speaker_similarity=0.91
    )
    sample = updated.samples[-1]
    removed, trash_name = store.remove_sample(profile.id, sample.id)
    assert removed.id == sample.id
    assert not any(item.id == sample.id for item in store.get(profile.id).samples)
    assert (store.profile_directory(profile.id) / ".trash" / "samples" / trash_name).is_file()


def test_tts_verification_detects_factually_wrong_chinese_number() -> None:
    assert "6009" in factual_numbers("海拔六千零九米")
    report = verify_transcript("海拔六千零九米", "海拔609米")
    assert report["passed"] is False
    assert report["missing_numbers"] == ["6009"]


def test_voice_provider_requests_are_output_isolated_and_text_bound(tmp_path: Path) -> None:
    profile = VoiceProfile(
        display_name="Roger",
        speaker_id="roger",
        consent=ConsentRecord(
            relationship="self",
            statement="I authorize this local voice profile for testing.",
        ),
    )
    first, first_dir = build_provider_request(
        profile, tmp_path / "profile", [{"text": "第一句"}], tmp_path / "output"
    )
    second, second_dir = build_provider_request(
        profile, tmp_path / "profile", [{"text": "第二句"}], tmp_path / "output"
    )

    assert first["request_id"] != second["request_id"]
    assert first_dir != second_dir
    assert first_dir.parent == second_dir.parent


def test_provider_response_for_another_request_is_rejected(tmp_path: Path) -> None:
    profile = VoiceProfile(
        display_name="Roger", speaker_id="roger",
        consent=ConsentRecord(
            relationship="self",
            statement="I authorize this local voice profile for testing.",
        ),
    )
    request, output_dir = build_provider_request(
        profile, tmp_path / "profile", [{"text": "本次请求"}], tmp_path / "output"
    )
    wav = _wav(output_dir / "take.wav")
    with pytest.raises(RuntimeError, match="another request"):
        validate_provider_response(
            {"status": "success", "request_id": "voice_request_wrong",
             "outputs": [{"output": str(wav)}]},
            output_dir,
            profile.id,
            request=request,
        )


def test_persistent_voice_timeout_restarts_before_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        stdin = io.StringIO()

        @staticmethod
        def poll():
            return None

    worker = ProviderWorker(tmp_path / "provider")
    worker.mode = "persistent"
    worker.process = Process()  # type: ignore[assignment]
    events: list[str] = []
    monkeypatch.setattr(
        worker, "_read_payload",
        lambda _timeout: (_ for _ in ()).throw(TimeoutError("late")),
    )
    monkeypatch.setattr(worker, "close", lambda: events.append("closed"))
    monkeypatch.setattr(worker, "start", lambda: events.append("restarted") or {})

    with pytest.raises(TimeoutError, match="late output discarded"):
        worker.request({"action": "synthesize", "request_id": "r1"}, timeout=0.01)
    assert events == ["closed", "restarted"]


def test_release_gate_requires_version_commit_and_archive_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from facut import installation

    monkeypatch.setattr(
        installation,
        "latest_release",
        lambda _repository: {
            "latest_version": "0.9.2",
            "asset_url": "https://example.invalid/facut.zip",
            "asset_digest": "sha256:" + "a" * 64,
            "release_manifest": {
                "version": "0.9.2", "commit": "b" * 40,
                "archive_sha256": "a" * 64,
            },
        },
    )
    result = installation.release_publish_check(
        {"version": "0.9.2", "commit": "b" * 40}, "owner/repo"
    )
    assert result["status"] == "pass"
    assert all(result["checks"].values())
