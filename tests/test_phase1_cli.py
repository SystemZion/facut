from __future__ import annotations

import json

from typer.testing import CliRunner
from typer.main import get_command

from facut.cli.main import app

runner = CliRunner()


def test_help_lists_doctor_and_global_options() -> None:
    result = runner.invoke(
        app,
        ["--help"],
        env={"COLUMNS": "140"},
        terminal_width=140,
    )
    assert result.exit_code == 0
    assert "doctor" in result.stdout
    registered_options = {
        option
        for parameter in get_command(app).params
        for option in getattr(parameter, "opts", ())
    }
    assert "--project" in registered_options
    assert "--json" in registered_options


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip().startswith("facut ")


def test_doctor_json_is_single_response() -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["command"] == "doctor"
    assert "ffmpeg" in payload["data"]
    assert "hardware" in payload["data"]["encoders"]


def test_global_json_before_command() -> None:
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["command"] == "doctor"
