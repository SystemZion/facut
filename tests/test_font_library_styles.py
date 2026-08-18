from __future__ import annotations

from types import SimpleNamespace

from facut.core.models import ProjectDocument, ProjectSettings
from facut.fonts import FontCatalog
from facut.library import MediaLibrary
from facut.styles import describe_style, list_styles, validate_style_usage


def test_font_catalog_matches_logical_role_and_preserves_external_file(tmp_path, monkeypatch) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font = font_dir / "TestSans.ttf"
    font.write_bytes(b"font-data")
    license_file = tmp_path / "LICENSE.txt"
    license_file.write_text("Authorized test font license", encoding="utf-8")

    def metadata(path):
        return {
            "family": "Source Han Sans SC",
            "style": "Regular",
            "glyph_count": 5000,
            "coverage": {"ascii": True, "cjk": True},
            "codepoints": list(range(128)) + [ord("中")],
            "readable": True,
            "error": None,
        }

    monkeypatch.setattr("facut.fonts.manager._font_directories", lambda: [font_dir])
    monkeypatch.setattr("facut.fonts.manager._font_metadata", metadata)
    catalog = FontCatalog(tmp_path / "catalog")
    registered = catalog.register(font, license_file=license_file)
    assert font.read_bytes() == b"font-data"
    assert registered["license_sha256"]
    match = catalog.match("caption-sans", language="zh-CN")
    assert match["selected_family"] == "Source Han Sans SC"
    assert match["source"] == "user-registered"


def test_media_library_requires_auditable_platform_license(tmp_path, monkeypatch) -> None:
    source = tmp_path / "playful.wav"
    source.write_bytes(b"audio")
    license_file = tmp_path / "music-license.txt"
    license_file.write_text("youtube and bilibili", encoding="utf-8")
    monkeypatch.setattr(
        "facut.library.catalog.probe_media",
        lambda path, ffprobe=None: SimpleNamespace(audio_codec="pcm_s16le", duration=12.0),
    )
    monkeypatch.setattr(
        "facut.library.catalog.analyze_beats",
        lambda path, ffmpeg=None: {"tempo_bpm": 120.0, "beats": [{"time": 0.5}]},
    )
    library = MediaLibrary(tmp_path / "library")
    record = library.add(
        source,
        kind="music",
        moods=["playful"],
        platforms=["youtube", "bilibili"],
        license_file=license_file,
    )
    assert record["tempo_bpm"] == 120.0
    assert library.search(mood="playful", platform="youtube")["count"] == 1
    assert library.audit("youtube")["status"] == "pass"
    assert library.audit("tiktok")["status"] == "fail"


def test_style_packs_include_comedy_restraint_and_density_review() -> None:
    styles = list_styles()
    assert set(styles) >= {
        "natural-vlog",
        "comedy-vlog",
        "cinematic-travel",
        "humanities-documentary",
        "family-trip",
        "food-walk",
        "relaxed-daily",
    }
    comedy = describe_style("comedy-vlog")
    assert comedy["restraint"]
    assert next(item for item in comedy["effects"] if item["name"] == "laser-eyes")["fallback"] == "punch-zoom"
    document = ProjectDocument(project=ProjectSettings(name="empty", duration=60))
    assert validate_style_usage(document, "natural-vlog")["status"] == "pass"
