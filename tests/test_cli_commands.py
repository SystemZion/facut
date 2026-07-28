"""CLI integration tests for project-aware edit commands."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app


runner = CliRunner()


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_init_track_dry_run_and_undo(tmp_path) -> None:
    project = tmp_path / "demo"
    created = _json(runner.invoke(app, ["--json", "init", str(project)]))
    assert created["status"] == "success"
    assert created["project_revision"] == 0

    added = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "timeline",
                "track",
                "add",
                "--type",
                "video",
                "--name",
                "V1",
            ],
        )
    )
    assert added["data"]["result"]["id"] == "V1"
    assert added["project_revision"] == 1

    dry_run = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "timeline",
                "track",
                "add",
                "--type",
                "audio",
                "--name",
                "A1",
                "--dry-run",
            ],
        )
    )
    assert dry_run["data"]["dry_run"] is True
    assert dry_run["project_revision"] == 2

    shown = _json(
        runner.invoke(
            app, ["--project", str(project), "--json", "timeline", "show"]
        )
    )
    assert [track["id"] for track in shown["data"]["tracks"]] == ["V1"]

    undone = _json(
        runner.invoke(app, ["--project", str(project), "--json", "undo"])
    )
    assert undone["project_revision"] == 0


def test_atomic_batch_commits_one_revision(tmp_path) -> None:
    project = tmp_path / "batch"
    _json(runner.invoke(app, ["--json", "init", str(project)]))
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "version": "1.0",
                "atomic": True,
                "commands": [
                    {"action": "timeline.track.add", "type": "video", "name": "V1"},
                    {"action": "timeline.track.add", "type": "audio", "name": "A1"},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = _json(
        runner.invoke(
            app, ["--project", str(project), "--json", "run", str(plan)]
        )
    )
    assert result["project_revision"] == 1
    shown = _json(
        runner.invoke(
            app, ["--project", str(project), "--json", "timeline", "show"]
        )
    )
    assert [track["id"] for track in shown["data"]["tracks"]] == ["V1", "A1"]
