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
