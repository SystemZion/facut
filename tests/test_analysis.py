from __future__ import annotations

from pathlib import Path

from facut.analysis.engine import analyze_song_metadata


def test_song_analysis_returns_plugin_boundary_without_tags(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "audio.bin"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        "facut.analysis.engine.probe_raw",
        lambda *args, **kwargs: {
            "format": {},
            "streams": [{"codec_type": "audio", "tags": {}}],
        },
    )
    result = analyze_song_metadata(source)
    assert result["recognition_status"] == "plugin_required"
    assert len(result["fingerprint_seed"]) == 64


def test_song_analysis_uses_embedded_title(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "audio.bin"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        "facut.analysis.engine.probe_raw",
        lambda *args, **kwargs: {
            "format": {"tags": {"title": "Track", "artist": "Artist"}},
            "streams": [],
        },
    )
    result = analyze_song_metadata(source)
    assert result["candidates"][0]["title"] == "Track"
