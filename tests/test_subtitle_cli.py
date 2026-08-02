"""End-to-end subtitle and text CLI tests."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app


runner = CliRunner()


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_import_shift_export_text_and_compile(tmp_path) -> None:
    project = tmp_path / "demo"
    _json(runner.invoke(app, ["--json", "init", str(project)]))
    _json(
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
                "subtitle",
                "--name",
                "S1",
            ],
        )
    )
    source = tmp_path / "captions.srt"
    source.write_text(
        "1\n00:00:00,500 --> 00:00:01,500\n你好\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n世界\n",
        encoding="utf-8",
    )
    imported = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "subtitle",
                "import",
                str(source),
                "--track",
                "S1",
                "--offset",
                "+500ms",
            ],
        )
    )
    assert len(imported["data"]["result"]) == 2
    assert imported["project_revision"] == 2

    shifted = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "subtitle",
                "shift",
                "S1",
                "--offset",
                "+250ms",
                "--dry-run",
            ],
        )
    )
    assert shifted["data"]["dry_run"] is True

    listed = _json(
        runner.invoke(
            app,
            ["--project", str(project), "--json", "subtitle", "list", "--track", "S1"],
        )
    )
    assert listed["data"][0]["start"] == 1.0

    output = tmp_path / "captions.vtt"
    exported = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "subtitle",
                "export",
                "S1",
                "--output",
                str(output),
            ],
        )
    )
    assert exported["data"]["cue_count"] == 2
    assert output.read_text(encoding="utf-8").startswith("WEBVTT")

    overlay = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "text",
                "add",
                "--text",
                "临港两日",
                "--at",
                "0",
                "--duration",
                "2s",
                "--x",
                "center",
                "--y",
                "20%",
                "--font-size",
                "72",
            ],
        )
    )
    assert overlay["data"]["result"]["style"]["font_size"] == 72.0

    ass = tmp_path / "captions.ass"
    compiled = _json(
        runner.invoke(
            app,
            [
                "--project",
                str(project),
                "--json",
                "subtitle",
                "compile-ass",
                "--output",
                str(ass),
            ],
        )
    )
    assert compiled["data"]["cue_count"] == 2
    assert compiled["data"]["text_overlay_count"] == 1
    assert "[Events]" in ass.read_text(encoding="utf-8-sig")


def test_lingang_template_is_discoverable_and_persisted(tmp_path) -> None:
    project = tmp_path / "travel"
    _json(runner.invoke(app, ["--json", "init", str(project), "--width", "3840", "--height", "2160"]))
    presets = _json(runner.invoke(app, ["--json", "text", "presets"]))
    template = presets["data"]["lingang-cinematic-panel"]
    assert template["renderer"] == "lingang-panel"
    assert template["parameters"]["accent_color"] == "#66E1FF"

    result = _json(
        runner.invoke(
            app,
            [
                "--project", str(project), "--json", "text", "add",
                "--text", "再来", "--subtitle", "同一条雪道，重新滑下",
                "--at", "0", "--duration", "2s",
                "--template", "lingang-cinematic-mint",
            ],
        )
    )
    overlay = result["data"]["result"]
    assert overlay["template"] == "lingang-cinematic-mint"
    assert overlay["subtitle"] == "同一条雪道，重新滑下"
    assert overlay["template_parameters"]["accent_color"] == "#74EFCB"
