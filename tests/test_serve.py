from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app


def test_stdio_json_rpc_reuses_loaded_project(tmp_path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    assert runner.invoke(app, ["--json", "init", str(project)]).exit_code == 0
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "project.snapshot"}
            ),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "shutdown"}),
        ]
    )
    result = runner.invoke(
        app,
        ["--project", str(project), "serve"],
        input=requests + "\n",
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["method"] == "facut.ready"
    assert lines[1]["result"]["status"] == "ok"
    assert lines[2]["result"]["project"]["name"] == "project"
    assert lines[3]["result"]["status"] == "shutdown"
