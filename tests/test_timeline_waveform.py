from __future__ import annotations

from pathlib import Path

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.core.timeline_engine import TimelineEngine
from facut.media.timeline_waveform import timeline_waveforms, waveform_text


def test_timeline_waveform_projects_cached_source_peaks(monkeypatch, tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "waveform-project")
    source = tmp_path / "voice.wav"
    source.write_bytes(b"audio")
    manager.document.media.append(
        MediaAsset(
            id="voice",
            kind=MediaKind.AUDIO,
            path=str(source),
            original_name=source.name,
            size=source.stat().st_size,
            sha256="f" * 64,
            technical=MediaTechnicalInfo(
                duration=2,
                audio_codec="pcm_s16le",
                sample_rate=48000,
            ),
        )
    )
    timeline = TimelineEngine(manager.document)
    timeline.add_track("audio", "A1")
    timeline.add_audio_clip("voice", "A1", source_out=2)
    manager.save()
    monkeypatch.setattr(
        "facut.media.timeline_waveform.peak_envelope",
        lambda *args, **kwargs: {
            "peaks": [0.0, 0.25, 0.5, 0.75, 1.0] * 40,
            "sample_rate": 100,
            "cached": True,
        },
    )
    payload = timeline_waveforms(
        manager, manager.require_document(), width=40, from_time=0, to_time=2
    )
    assert payload["tracks"][0]["track_id"] == "A1"
    assert len(payload["tracks"][0]["samples"]) == 40
    assert "A1" in waveform_text(payload)
    assert payload["cache"]["hits"] == 1
