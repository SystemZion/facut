"""Real FFmpeg/FFprobe acceptance tests for automated QC."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import json
import logging

import pytest
import typer
from typer.testing import CliRunner

from facut.cli.main import CliState
from facut.cli.qc_commands import qc_command
from facut.config import AppConfig, ToolConfig
from facut.core.project_manager import ProjectManager
from facut.media.importer import MediaImporter
from facut.qc.engine import QCEngine, QCSource, resolve_qc_scope
from facut.qc.models import QCStatus
from facut.qc.report import write_markdown


def _qc_tools() -> tuple[str, str, str] | None:
    candidates = [
        os.environ.get("FACUT_TEST_FFMPEG"),
        os.environ.get("FACUT_FFMPEG"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        filters = subprocess.run(
            [candidate, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if not all(name in filters.stdout for name in ("blackdetect", "silencedetect", "ebur128", "tile")):
            continue
        encoders = subprocess.run(
            [candidate, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        ).stdout
        encoder = "libx264" if " libx264 " in encoders else "mpeg4" if " mpeg4 " in encoders else ""
        probe = os.environ.get("FACUT_TEST_FFPROBE") or os.environ.get("FACUT_FFPROBE")
        adjacent = Path(candidate).with_name("ffprobe.exe")
        if not probe and adjacent.is_file():
            probe = str(adjacent)
        probe = probe or shutil.which("ffprobe")
        if encoder and probe:
            return str(candidate), str(probe), encoder
    return None


@pytest.fixture()
def qc_media(tmp_path: Path) -> tuple[Path, str, str]:
    tools = _qc_tools()
    if tools is None:
        pytest.skip("No FFmpeg with QC filters is available")
    ffmpeg, ffprobe, encoder = tools
    output = tmp_path / "qc-sample.mkv"
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=10:d=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "pcm_s16le",
            "-y",
            output,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode:
        pytest.skip(f"Could not generate QC fixture: {result.stderr[-500:]}")
    return output, ffmpeg, ffprobe


def test_real_qc_detects_black_silence_loudness_and_generates_artifacts(
    tmp_path: Path, qc_media: tuple[Path, str, str]
) -> None:
    source, ffmpeg, ffprobe = qc_media
    engine = QCEngine(ffmpeg=ffmpeg, ffprobe=ffprobe, timeout=30)
    sheet = tmp_path / "contact.jpg"
    report = engine.run(
        {"type": "file", "path": str(source)},
        [QCSource(source)],
        contact_sheet=sheet,
    )
    item = report.files[0]
    assert item.checks["probe"].status == QCStatus.PASS
    assert item.checks["decode"].status == QCStatus.PASS
    assert item.checks["black_frames"].status == QCStatus.WARNING
    assert item.checks["black_frames"].data["segments"][0]["duration"] >= 0.9
    assert item.checks["silence"].status == QCStatus.WARNING
    assert item.checks["silence"].data["segments"][0]["duration"] >= 0.9
    assert "integrated_lufs" in item.checks["loudness"].data
    assert isinstance(item.checks["loudness"].data["integrated_lufs"], float)
    assert sheet.stat().st_size > 100

    markdown = write_markdown(report, tmp_path / "qc.md")
    assert markdown.read_text(encoding="utf-8").startswith("# facut QC report")


def test_project_scope_resolves_imported_media(
    tmp_path: Path, qc_media: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, ffprobe = qc_media
    monkeypatch.setenv("FACUT_FFPROBE", ffprobe)
    manager = ProjectManager.create(tmp_path / "project")
    imported = MediaImporter(manager).import_paths([source])[0]
    scope, sources = resolve_qc_scope(manager.project_dir)
    assert scope["type"] == "project"
    assert sources == [
        QCSource(
            path=source.resolve(),
            media_id=imported.id,
            kind="video",
        )
    ]


def test_qc_command_emits_structured_json(qc_media: tuple[Path, str, str]) -> None:
    source, ffmpeg, ffprobe = qc_media
    app = typer.Typer()

    @app.callback()
    def root(ctx: typer.Context) -> None:
        ctx.obj = CliState(
            json_output=True,
            quiet=False,
            verbose=False,
            project=None,
            config=AppConfig(tools=ToolConfig(ffmpeg=ffmpeg, ffprobe=ffprobe)),
            logger=logging.getLogger("facut-qc-test"),
        )

    app.command("qc")(qc_command)
    result = CliRunner().invoke(app, ["qc", str(source), "--timeout", "30"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["command"] == "qc"
    assert payload["data"]["scope"]["type"] == "file"
    assert payload["data"]["files"][0]["checks"]["decode"]["status"] == "pass"
