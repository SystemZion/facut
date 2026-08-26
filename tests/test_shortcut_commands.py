from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app
from facut.core.project_manager import ProjectManager


def test_explain_is_read_only_and_accepts_unparsed_shortcut_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_CONFIG", str(tmp_path / "config.toml"))
    result = CliRunner().invoke(
        app,
        ["--json", "explain", "cut", "--style", "comedy", "-l", "8m", "--4k"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "explain"
    assert payload["data"]["canonical_action"] == "vlog.run"
    assert payload["data"]["executed"] is False
    assert not (tmp_path / "config.toml").exists()


def test_next_reports_alias_and_canonical_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_CONFIG", str(tmp_path / "config.toml"))
    project = tmp_path / "project"
    ProjectManager.create(project, name="trip")
    result = CliRunner().invoke(app, ["--json", "next", "-p", str(project)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["requested_command"] == "next"
    assert payload["canonical_action"] == "vlog.status"
    assert payload["data"]["stage"] == "not_prepared"


def test_cut_rejects_4k_draft_with_structured_alias_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_CONFIG", str(tmp_path / "config.toml"))
    source = tmp_path / "media"
    source.mkdir()
    result = CliRunner().invoke(
        app,
        ["--json", "cut", str(source), "-o", str(tmp_path / "out.mp4"), "--4k"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["requested_command"] == "cut"
    assert payload["canonical_action"] == "vlog.run"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


def test_defaults_round_trip_and_style_alias(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    monkeypatch.setenv("FACUT_CONFIG", str(config))
    runner = CliRunner()
    saved = runner.invoke(
        app,
        ["--json", "defaults", "set", "--style", "comedy", "--length", "8m"],
    )
    assert saved.exit_code == 0, saved.output
    shown = runner.invoke(app, ["--json", "defaults", "show"])
    data = json.loads(shown.stdout)["data"]["effective"]
    assert data["style"] == "comedy-vlog"
    assert data["target_duration"] == 480.0


def test_project_defaults_override_user_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_CONFIG", str(tmp_path / "config.toml"))
    project = tmp_path / "project"
    ProjectManager.create(project, name="trip")
    runner = CliRunner()
    assert runner.invoke(
        app, ["defaults", "set", "--style", "natural", "--scope", "user"]
    ).exit_code == 0
    project_result = runner.invoke(
        app,
        [
            "--json",
            "--project",
            str(project),
            "defaults",
            "set",
            "--style",
            "comedy",
            "--scope",
            "project",
        ],
    )
    assert project_result.exit_code == 0, project_result.output
    shown = runner.invoke(
        app, ["--json", "--project", str(project), "defaults", "show"]
    )
    assert json.loads(shown.stdout)["data"]["effective"]["style"] == "comedy-vlog"
