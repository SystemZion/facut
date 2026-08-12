from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from facut.core.project_manager import ProjectManager
from facut.core.models import MediaTechnicalInfo
from facut.media.importer import MediaImporter, hash_file
from facut.media.thumbnail import generate_contact_sheet, generate_thumbnail


@pytest.fixture()
def sample_video(tmp_path):
    ffmpeg = os.environ.get("FACUT_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    capability = subprocess.run(
        [ffmpeg, "-hide_banner", "-version"], capture_output=True, check=False
    )
    if capability.returncode:
        pytest.skip("Installed FFmpeg is too old for the integration test")
    output = tmp_path / "input.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=160x90:rate=10:duration=2",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
    )
    return output


def test_import_is_content_addressed_and_deduplicated(tmp_path, sample_video) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    importer = MediaImporter(manager)
    first = importer.import_paths([sample_video])[0]
    second = importer.import_paths([sample_video])[0]
    assert first.id == f"media_{hash_file(sample_video)[:16].upper()}"
    assert second.id == first.id
    assert len(manager.require_document().media) == 1
    # Both commands are real, inspectable revisions even when the second is a no-op import.
    assert manager.require_document().revision == 2


def test_import_can_exclude_sibling_lrf_proxy(monkeypatch, tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    source_dir = tmp_path / "camera"
    source_dir.mkdir()
    (source_dir / "shot001.mp4").write_bytes(b"original")
    (source_dir / "shot001_LRF.mp4").write_bytes(b"proxy")
    monkeypatch.setattr(
        "facut.media.importer.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(
            duration=10, video_codec="h264", width=3840, height=2160
        ),
    )
    assets = MediaImporter(manager).import_paths(
        [source_dir], recursive=True, exclude_proxy_candidates=True
    )
    assert [asset.original_name for asset in assets] == ["shot001.mp4"]


def test_thumbnail_and_contact_sheet_are_real_images(tmp_path, sample_video) -> None:
    thumb = generate_thumbnail(sample_video, tmp_path / "thumb.jpg", at=0.5)
    sheet = generate_contact_sheet(
        sample_video, tmp_path / "sheet.jpg", interval=0.5, columns=2, width=120
    )
    assert thumb.stat().st_size > 100
    assert sheet.stat().st_size > 100
