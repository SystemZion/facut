from __future__ import annotations

from pathlib import Path

import pytest

from facut.config import AppConfig, load_config, save_config
from facut.exceptions import InvalidArgumentError


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = AppConfig()
    original.render.codec = "h265"
    original.preview.height = 720
    save_config(original, path)

    loaded = load_config(path)
    assert loaded.render.codec == "h265"
    assert loaded.preview.height == 720
    assert loaded.models.directory == original.models.directory


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    loaded = load_config(tmp_path / "missing.toml")
    assert loaded.render.codec == "h264"
    assert loaded.preview.height == 540


def test_invalid_config_is_public_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[preview]\nheight = 2\n", encoding="utf-8")
    with pytest.raises(InvalidArgumentError) as captured:
        load_config(path)
    assert captured.value.code == "INVALID_ARGUMENT"


def test_auto_tool_paths_are_resolved_at_load_time(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[tools]\nffmpeg = "auto"\nffprobe = "auto"\n', encoding="utf-8")

    loaded = load_config(path)

    assert loaded.tools.ffmpeg != "auto"
    assert loaded.tools.ffprobe != "auto"


def test_explicit_model_link_survives_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    linked = tmp_path / "whisper-turbo"
    linked.mkdir()
    config = AppConfig()
    config.models.srt_model = linked
    save_config(config, path)

    loaded = load_config(path)

    assert loaded.models.resolve("srt_model") == linked.resolve()
    assert loaded.models.voice_model is None
