from __future__ import annotations

import hashlib
import io
from pathlib import Path

from rich.text import Text
from typer.testing import CliRunner

from facut.cli.main import app
from facut.downloads.catalog import DownloadSource, ModelFile, ModelPackage, get_model_package
from facut.downloads.downloader import ModelDownloader


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, status: int = 200, headers: dict[str, str] | None = None):
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def package_for(payload: bytes, *sources: str) -> ModelPackage:
    return ModelPackage(
        name="test_model",
        directory_name="test-model",
        display_name="Test Model",
        purpose="tests",
        files=(ModelFile("model.bin", len(payload), hashlib.sha256(payload).hexdigest()),),
        sources=tuple(
            DownloadSource(name, "owner/model", "main", f"https://{name}.example/{{path}}")
            for name in sources
        ),
        benchmark_file="model.bin",
        license="MIT",
    )


def test_catalog_exposes_external_voice_and_srt_models() -> None:
    voice = get_model_package("voice_model")
    srt = get_model_package("srt-model")
    assert voice.total_size > 5_000_000_000
    assert voice.license == "Apache-2.0"
    assert srt.total_size > 1_500_000_000
    assert any(item.path == "model.bin" for item in srt.files)


def test_download_resumes_existing_part_file(monkeypatch, tmp_path: Path) -> None:
    payload = b"0123456789abcdef"
    package = package_for(payload, "primary")
    partial = tmp_path / "test-model" / "model.bin.part"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(payload[:6])
    observed_ranges: list[str | None] = []

    def fake_urlopen(request, timeout):
        observed_ranges.append(request.get_header("Range"))
        return FakeResponse(
            payload[6:],
            status=206,
            headers={"Content-Range": f"bytes 6-{len(payload) - 1}/{len(payload)}"},
        )

    monkeypatch.setattr("facut.downloads.downloader.urlopen", fake_urlopen)
    result = ModelDownloader(package, tmp_path).download(source_name="primary")
    assert observed_ranges == ["bytes=6-"]
    assert (tmp_path / "test-model" / "model.bin").read_bytes() == payload
    assert not partial.exists()
    assert result["verified"] is True
    assert result["resumable"] is True


def test_zero_byte_proxy_falls_back_to_second_source(monkeypatch, tmp_path: Path) -> None:
    payload = b"valid model bytes"
    package = package_for(payload, "empty-proxy", "working")
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if "empty-proxy" in request.full_url:
            return FakeResponse(b"")
        return FakeResponse(payload)

    monkeypatch.setattr("facut.downloads.downloader.urlopen", fake_urlopen)
    result = ModelDownloader(package, tmp_path)._download_one(
        package.files[0], list(package.sources)
    )
    assert calls == [
        "https://empty-proxy.example/model.bin",
        "https://working.example/model.bin",
    ]
    assert result["source"] == "working"


def test_early_eof_keeps_bytes_and_resumes_on_next_source(monkeypatch, tmp_path: Path) -> None:
    payload = b"abcdefghijklmnop"
    package = package_for(payload, "flaky", "backup")
    requests: list[tuple[str, str | None]] = []

    def fake_urlopen(request, timeout):
        range_header = request.get_header("Range")
        requests.append((request.full_url, range_header))
        if "flaky" in request.full_url:
            return FakeResponse(payload[:5])
        return FakeResponse(
            payload[5:],
            status=206,
            headers={"Content-Range": f"bytes 5-{len(payload) - 1}/{len(payload)}"},
        )

    monkeypatch.setattr("facut.downloads.downloader.urlopen", fake_urlopen)
    downloader = ModelDownloader(package, tmp_path)
    result = downloader._download_one(package.files[0], list(package.sources))
    assert requests == [
        ("https://flaky.example/model.bin", None),
        ("https://backup.example/model.bin", "bytes=5-"),
    ]
    assert (tmp_path / "test-model" / "model.bin").read_bytes() == payload
    assert result["source"] == "backup"


def test_invalid_existing_file_is_preserved_as_quarantine(monkeypatch, tmp_path: Path) -> None:
    payload = b"correct"
    package = package_for(payload, "primary")
    target = tmp_path / "test-model" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"wrong")

    monkeypatch.setattr(
        "facut.downloads.downloader.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    result = ModelDownloader(package, tmp_path).download(source_name="primary")
    quarantined = list(target.parent.glob("model.bin.invalid-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"wrong"
    assert target.read_bytes() == payload
    assert result["verified"] is True


def test_verified_receipt_skips_network_and_rehash(monkeypatch, tmp_path: Path) -> None:
    payload = b"receipt-backed-model"
    package = package_for(payload, "primary")
    monkeypatch.setattr(
        "facut.downloads.downloader.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    downloader = ModelDownloader(package, tmp_path)
    first = downloader.download(source_name="primary")
    assert first["receipt_cached"] is False
    assert downloader.receipt_path.is_file()

    monkeypatch.setattr(
        "facut.downloads.downloader.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(AssertionError("network used")),
    )
    second = downloader.download(source_name="auto")
    assert second["receipt_cached"] is True
    assert second["benchmarks"] == []


def test_download_command_is_listed_in_help() -> None:
    result = CliRunner().invoke(app, ["download", "--help"])
    assert result.exit_code == 0
    # GitHub Actions forces ANSI colors on Windows. Strip styling before
    # validating option names so the test checks visible help text.
    help_text = Text.from_ansi(result.stdout).plain
    assert "voice_model" in help_text
    assert "srt_model" in help_text
    assert "--jsonl" in help_text
