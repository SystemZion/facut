from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app


def test_ingest_creates_4k_trip_project_and_verifies_hashes(tmp_path) -> None:
    source = tmp_path / "JapanTrip"
    source.mkdir()
    (source / "notes.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nTokyo\n", encoding="utf-8"
    )
    output = tmp_path / "project"
    result = CliRunner().invoke(
        app,
        [
            "--json", "ingest", str(source), "--trip", "日本旅行2026",
            "--output", str(output), "--verify", "sha256", "--proxy", "none",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["verification"]["asset_count"] == 1
    project = json.loads((output / "facut.json").read_text(encoding="utf-8"))
    assert project["project"]["width"] == 3840
    assert project["project"]["height"] == 2160
    assert project["settings"]["trip"]["name"] == "日本旅行2026"
    assert len(project["media"][0]["sha256"]) == 64
