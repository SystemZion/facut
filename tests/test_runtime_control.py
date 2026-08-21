from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from facut.cli.main import app
from facut.config import AppConfig, load_config, save_config
from facut.runtime_control import (
    clean_services,
    configure_autoload,
    normalize_services,
    schedule_default_warmup,
)


def test_cleanram_requires_explicit_service_and_preserves_disk_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "facut.voice.service.voice_service_status",
        lambda: {"running": True, "pid": 1234},
    )
    monkeypatch.setattr(
        "facut.voice.service.stop_voice_service",
        lambda: {"running": False, "stopped": True},
    )
    result = clean_services(["voice"])
    assert result["selected"] == ["voice"]
    assert result["result"]["voice"]["stopped"] is True
    assert result["persistent_data_deleted"] is False
    try:
        normalize_services([])
    except ValueError as error:
        assert "--service" in str(error)
    else:
        raise AssertionError("an explicit service selector must be required")


def test_autoload_configuration_is_persisted_as_a_toml_array(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("FACUT_CONFIG", str(config_path))
    save_config(AppConfig())
    disabled = configure_autoload("voice", enabled=False)
    assert disabled["autoload"] is False
    assert load_config().runtime.services == []
    assert "services = []" in config_path.read_text("utf-8")
    enabled = configure_autoload("voice", enabled=True)
    assert enabled["services"] == ["voice"]
    assert load_config().runtime.autoload is True


def test_default_autoload_schedules_a_detached_helper_without_waiting(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    voice_home = tmp_path / "voices"
    monkeypatch.setenv("FACUT_CONFIG", str(config_path))
    monkeypatch.setenv("FACUT_VOICE_HOME", str(voice_home))
    save_config(AppConfig())

    class FakeProcess:
        pid = 4321

    calls = []
    monkeypatch.setattr(
        "facut.runtime_control.subprocess.Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or FakeProcess(),
    )
    result = schedule_default_warmup()
    assert result["scheduled"] is True
    assert result["pid"] == 4321
    assert calls and "__runtime_warmup__" in calls[0][0]


def test_cleanram_cli_selects_voice_service(monkeypatch) -> None:
    monkeypatch.setattr(
        "facut.cli.runtime_commands.clean_services",
        lambda services, dry_run=False: {
            "selected": services,
            "dry_run": dry_run,
            "persistent_data_deleted": False,
        },
    )
    result = CliRunner().invoke(app, ["--json", "cleanram", "--service", "voice"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "runtime.cleanram"
    assert payload["data"]["selected"] == ["voice"]


def test_cleanram_cli_rejects_implicit_all() -> None:
    result = CliRunner().invoke(app, ["--json", "cleanram"])
    assert result.exit_code != 0
