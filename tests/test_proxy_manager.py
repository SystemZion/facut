from __future__ import annotations

from pathlib import Path

import pytest

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager
from facut.media.proxy_manager import ProxyError, ProxyManager
from facut.core.timeline_engine import TimelineEngine
from facut.render.graph_builder import GraphBuilder


def _manager(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project")
    source = tmp_path / "camera.mp4"
    source.write_bytes(b"source")
    manager.document.media.append(
        MediaAsset(
            id="camera",
            kind=MediaKind.VIDEO,
            path=str(source),
            original_name=source.name,
            size=source.stat().st_size,
            sha256="1" * 64,
            technical=MediaTechnicalInfo(
                duration=10,
                video_codec="h264",
                width=3840,
                height=2160,
                frame_rate=25,
            ),
        )
    )
    manager.save()
    return manager


def test_proxy_link_and_status(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proxy = tmp_path / "camera_LRF.mp4"
    proxy.write_bytes(b"proxy")
    monkeypatch.setattr(
        "facut.media.proxy_manager.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(
            duration=10, video_codec="h264", width=960, height=540
        ),
    )
    service = ProxyManager(manager)
    asset, state = service.link("camera", proxy)
    assert state.revision == 1
    assert asset.proxy_path == str(proxy.resolve())
    status = service.status("camera")[0]
    assert status["proxy_online"] is True
    assert status["preview_source"] == str(proxy.resolve())
    assert status["final_source"].endswith("camera.mp4")


def test_proxy_relink_finds_lrf(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proxy = tmp_path / "camera_LRF.mp4"
    proxy.write_bytes(b"proxy")
    monkeypatch.setattr(
        "facut.media.proxy_manager.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(
            duration=10, video_codec="h264", width=960, height=540
        ),
    )
    _, _, matched = ProxyManager(manager).relink("camera", tmp_path)
    assert matched == proxy.resolve()


def test_proxy_rejects_duration_mismatch(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proxy = tmp_path / "camera_LRF.mp4"
    proxy.write_bytes(b"proxy")
    monkeypatch.setattr(
        "facut.media.proxy_manager.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(
            duration=8, video_codec="h264", width=960, height=540
        ),
    )
    with pytest.raises(ProxyError, match="does not match"):
        ProxyManager(manager).link("camera", proxy)


def test_preview_uses_proxy_and_final_uses_original(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proxy = tmp_path / "camera_LRF.mp4"
    proxy.write_bytes(b"proxy")
    monkeypatch.setattr(
        "facut.media.proxy_manager.probe_media",
        lambda *args, **kwargs: MediaTechnicalInfo(
            duration=10, video_codec="h264", width=960, height=540
        ),
    )
    ProxyManager(manager).link("camera", proxy)
    document = manager.require_document()
    timeline = TimelineEngine(document)
    timeline.add_track("video", "V1")
    timeline.add_clip("camera", "V1", source_out=10)
    preview = GraphBuilder(document, manager.project_dir).build(preview=True)
    final = GraphBuilder(document, manager.project_dir).build(preview=False)
    assert preview.source_paths[0] == proxy.resolve()
    assert final.source_paths[0].name == "camera.mp4"
