from __future__ import annotations

import subprocess

from facut.cli import doctor


def test_hardware_probe_reports_success_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor,
        "_run",
        lambda arguments, timeout=10.0: subprocess.CompletedProcess(arguments, 0, "", ""),
    )

    result = doctor._probe_hardware_encoder("ffmpeg", "nvenc", {"h264_nvenc"})

    assert result["detected"] is True
    assert result["usable"] is True
    assert result["implemented"] is True
    assert result["encoder"] == "h264_nvenc"
    assert result["test"]["status"] == "success"
    assert result["test"]["elapsed_seconds"] >= 0
    assert result["test"]["frames"] == 6
    assert result["test"]["frames_per_second"] > 0


def test_hardware_probe_reports_bounded_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor,
        "_run",
        lambda arguments, timeout=10.0: subprocess.CompletedProcess(
            arguments,
            1,
            "",
            "driver initialization failed\nencoder unavailable\n",
        ),
    )

    result = doctor._probe_hardware_encoder("ffmpeg", "qsv", {"h264_qsv"})

    assert result["detected"] is True
    assert result["usable"] is False
    assert result["test"]["status"] == "failed"
    assert result["test"]["elapsed_seconds"] >= 0
    assert "encoder unavailable" in result["test"]["failure_summary"]


def test_hardware_probe_skips_undetected_encoder(monkeypatch) -> None:
    def unexpected_run(arguments, timeout=10.0):
        raise AssertionError("FFmpeg should not run for an undetected encoder")

    monkeypatch.setattr(doctor, "_run", unexpected_run)
    result = doctor._probe_hardware_encoder("ffmpeg", "amf", set())

    assert result["detected"] is False
    assert result["usable"] is False
    assert result["implemented"] is True
    assert result["test"]["status"] == "not_run"
