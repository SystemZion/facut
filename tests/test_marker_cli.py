from __future__ import annotations

import json

from openpyxl import load_workbook
from typer.testing import CliRunner

from facut.cli.main import app


runner = CliRunner()


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_marker_range_filter_and_xlsx_export(tmp_path) -> None:
    project = tmp_path / "project"
    _json(runner.invoke(app, ["--json", "init", str(project)]))
    added = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "marker",
                "add",
                "--at",
                "10s",
                "--end",
                "15s",
                "--label",
                "副歌",
                "--category",
                "chorus",
                "--rating",
                "5",
                "--recommended",
                "--tags",
                "music,highlight",
            ],
        )
    )
    assert added["data"]["marker"]["duration"] == 5
    listed = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "marker",
                "list",
                "--recommended-only",
                "--min-rating",
                "4",
            ],
        )
    )
    assert listed["data"][0]["label"] == "副歌"
    output = tmp_path / "markers.xlsx"
    exported = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "marker",
                "export",
                "--output",
                str(output),
            ],
        )
    )
    assert exported["data"]["count"] == 1
    workbook = load_workbook(output, read_only=True)
    assert workbook.active["E2"].value == "副歌"
