from __future__ import annotations

import json
from pathlib import Path

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


def test_help_command_matches_root_help() -> None:
    invoke_options = {"env": {"COLUMNS": "140"}, "terminal_width": 140}
    root_help = runner.invoke(app, ["--help"], **invoke_options)
    help_command = runner.invoke(app, ["help"], **invoke_options)
    assert root_help.exit_code == 0
    assert help_command.exit_code == 0
    assert help_command.stdout == root_help.stdout


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
    data = payload["data"]
    if data["ffmpeg"]["available"]:
        assert Path(data["ffmpeg"]["executable"]).is_absolute()
    if data["ffprobe"]["available"]:
        assert Path(data["ffprobe"]["executable"]).is_absolute()
    hardware = data["encoders"]["hardware"]
    for backend in ("nvenc", "qsv", "amf"):
        assert {"detected", "usable", "implemented", "encoder", "test"} <= hardware[backend].keys()
        assert hardware[backend]["implemented"] is True
        if hardware[backend]["test"]["status"] == "success":
            assert hardware[backend]["test"]["elapsed_seconds"] >= 0
            assert hardware[backend]["test"]["frames_per_second"] > 0
        elif hardware[backend]["test"]["status"] == "failed":
            assert hardware[backend]["test"]["failure_summary"]


def test_global_json_before_command() -> None:
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["command"] == "doctor"
