"""Unit tests for bounded streaming and structured QC helpers."""

from __future__ import annotations

from pathlib import Path
import sys

from facut.qc.detectors import _parse_ebur128
from facut.qc.engine import _check_delivery_audio, _check_delivery_video
from facut.qc.models import CheckResult, FileQCResult, QCReport, QCStatus
from facut.qc.process import run_streaming
from facut.qc.report import render_markdown


def test_streaming_runner_retains_only_bounded_tail() -> None:
    result = run_streaming(
        [
            sys.executable,
            "-c",
            "import sys; [print(f'line-{i}', file=sys.stderr) for i in range(1000)]",
        ],
        tail_lines=7,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stderr_tail == [f"line-{index}" for index in range(993, 1000)]


def test_ebur128_summary_parser_ignores_running_measurements() -> None:
    metrics = _parse_ebur128(
        [
            "[Parsed_ebur128] t: 1.0 I: -40.0 LUFS LRA: 0.0 LU",
            "Summary:",
            "Integrated loudness:",
            "  I:         -14.2 LUFS",
            "Loudness range:",
            "  LRA:         3.4 LU",
            "True peak:",
            "  Peak:       -1.5 dBFS",
        ]
    )
    assert metrics == {
        "integrated_lufs": -14.2,
        "loudness_range_lu": 3.4,
        "true_peak_dbfs": -1.5,
    }


def test_markdown_report_is_deterministic() -> None:
    report = QCReport(
        scope={"type": "file", "path": "sample.mp4"},
        status=QCStatus.WARNING,
        summary={"total": 1, "passed": 0, "warnings": 1, "failed": 0},
        files=[
            FileQCResult(
                path="sample.mp4",
                status=QCStatus.WARNING,
                checks={
                    "decode": CheckResult(
                        status=QCStatus.PASS,
                        summary="Decoded.",
                    ),
                    "silence": CheckResult(
                        status=QCStatus.WARNING,
                        summary="Detected 1 silence segment.",
                    ),
                },
            )
        ],
    )
    markdown = render_markdown(report)
    assert markdown.startswith("# facut QC report\n")
    assert "| silence | warning | Detected 1 silence segment. |" in markdown
    assert "- Failed: 0" in markdown


def test_delivery_metadata_flags_444_vfr_and_surround() -> None:
    streams = [
        {
            "codec_type": "video",
            "pix_fmt": "yuv444p",
            "r_frame_rate": "30/1",
            "avg_frame_rate": "2997/100",
            "color_space": "bt2020nc",
            "color_transfer": "smpte2084",
            "color_primaries": "bt2020",
        },
        {
            "codec_type": "audio",
            "channels": 6,
            "channel_layout": "5.1",
            "sample_rate": "48000",
        },
    ]
    video = _check_delivery_video(streams)
    audio = _check_delivery_audio(streams)
    assert video.status == QCStatus.WARNING
    assert video.data["variable_frame_rate"] is True
    assert video.data["pixel_format"] == "yuv444p"
    assert audio.status == QCStatus.WARNING
    assert audio.data["channels"] == 6


def test_delivery_metadata_flags_missing_bt709_tags() -> None:
    result = _check_delivery_video(
        [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p",
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30/1",
            }
        ]
    )
    assert result.status == QCStatus.WARNING
    assert "incomplete" in result.errors[0]
