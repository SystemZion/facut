from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from facut.core.models import Clip, MediaAsset, MediaTechnicalInfo, Track, TrackType
from facut.core.command_engine import CommandEngine, CommandEngineError
from facut.core.project_manager import ProjectManager
from facut.director.music import MusicCatalog
from facut.director.review_room import _handler, apply_patch, capture_request, create_patch, preview_patch
from facut.director.taste import TasteStore
from facut.exceptions import ReviewRequiredError


def _manager(tmp_path) -> ProjectManager:
    manager = ProjectManager.create(tmp_path / "project", name="director-studio")
    def seed(document):
        document.media.append(MediaAsset(
            id="media_a", kind="video", path="a.mp4", original_name="a.mp4",
            size=10, sha256="a" * 64,
            technical=MediaTechnicalInfo(duration=20, video_codec="h264", audio_codec="aac"),
        ))
        document.tracks.append(Track(
            id="V1", type=TrackType.VIDEO, name="V1",
            clips=[Clip(id="clip_a", media_id="media_a", track_id="V1", source_out=5)],
        ))
    manager.mutate("test.seed", "seed", seed)
    evidence = manager.project_dir / "cache" / "vlog" / "observations.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"version":"2.0","observations":[]}', encoding="utf-8")
    manager.load()
    return manager


def test_music_v2_migrates_and_hard_filters_license_duration_and_platform(tmp_path, monkeypatch) -> None:
    source = tmp_path / "music.wav"
    source.write_bytes(b"audio")
    license_file = tmp_path / "license.txt"
    license_file.write_text("youtube and bilibili", encoding="utf-8")
    monkeypatch.setattr(
        "facut.director.music.probe_media",
        lambda path, ffprobe=None: SimpleNamespace(
            audio_codec="pcm_s16le", duration=12.0, sample_rate=48000, audio_channels=2
        ),
    )
    monkeypatch.setattr(
        "facut.director.music.analyze_beats",
        lambda path, ffmpeg=None: {"tempo_bpm": 120.0, "beats": [{"time": 0.5}]},
    )
    catalog = MusicCatalog(tmp_path / "library")
    item = catalog.ingest_file(
        source, tags={"mood": ["playful"], "scene_role": ["comedy-failure"]},
        platforms=["youtube", "bilibili"], license_file=license_file,
    )
    assert item["license_status"] == "verified"
    found = catalog.find("playful", scene="comedy-failure", duration=10, platforms=["youtube"])
    assert found["count"] == 1
    assert "platform_license_verified" in found["results"][0]["match_reasons"]
    assert catalog.find("playful", duration=13, platforms=["youtube"])["count"] == 0
    assert catalog.find("playful", duration=10, platforms=["tiktok"])["count"] == 0
    assert catalog.audit(["youtube"])["status"] == "pass"


def test_source_download_requires_captured_license_evidence(tmp_path, monkeypatch) -> None:
    catalog = MusicCatalog(tmp_path / "library")
    session = catalog.create_source_session("pixabay", "cinematic space")
    source = tmp_path / "download.mp3"
    source.write_bytes(b"audio")
    with pytest.raises(ReviewRequiredError):
        catalog.import_source_download(source, session["id"])


def test_timeline_patch_dry_run_then_atomic_apply_and_undo(tmp_path) -> None:
    manager = _manager(tmp_path)
    document = manager.require_document()
    payload = {
        "request": "Move the opening after the title.",
        "project_id": document.project.id,
        "project_revision": document.revision,
        "project_sha256": __import__("hashlib").sha256(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "evidence_sha256": __import__("hashlib").sha256(
            (manager.project_dir / "cache" / "vlog" / "observations.json").read_bytes()
        ).hexdigest(),
        "operations": [{
            "action": "clip.move", "parameters": {"clip_id": "clip_a", "to": 2},
            "reason": "Leave room for the approved title.", "evidence": ["clip_a"],
            "confidence": 0.95, "approved": True,
        }],
    }
    planned = create_patch(manager, payload)
    before = manager.project_file.read_bytes()
    preview = preview_patch(manager, planned, approved_only=True)
    assert preview["diff"]["summary"]["modified"] >= 1
    assert manager.project_file.read_bytes() == before
    result = apply_patch(manager, planned, approved_only=True)
    assert result["status"] == "applied"
    assert manager.require_document().find_clip("clip_a").timeline_start == 2
    assert manager.undo().find_clip("clip_a").timeline_start == 0


def test_natural_language_request_is_saved_but_not_executed(tmp_path) -> None:
    manager = _manager(tmp_path)
    before = manager.project_file.read_bytes()
    result = capture_request(manager, "Make the museum feel grander.")
    assert result["status"] == "review_required"
    assert result["required_schema"] == "timeline_patch.v1"
    assert manager.project_file.read_bytes() == before


def test_taste_writes_only_on_explicit_remember(tmp_path) -> None:
    store = TasteStore(tmp_path / "taste")
    assert store.show()["count"] == 0
    item = store.remember(
        "feedback_1", category="transition", value="restrained",
        original_feedback="Use fewer visible transitions.", applies_to=["travel-vlog"],
    )
    assert store.show()["count"] == 1
    assert store.forget(item["id"])["remaining"] == 0


def test_review_room_requires_token_and_streams_media_ranges(tmp_path) -> None:
    from http.server import ThreadingHTTPServer

    manager = _manager(tmp_path)
    preview = manager.project_dir / "previews" / "candidate.mp4"
    preview.write_bytes(b"0123456789")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(manager.project_dir, "secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(f"http://127.0.0.1:{server.server_port}/api/session", timeout=2)
        assert denied.value.code == 401
        request = Request(
            f"http://127.0.0.1:{server.server_port}/media?token=secret&path=previews%2Fcandidate.mp4",
            headers={"Range": "bytes=2-5"},
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 206
            assert response.read() == b"2345"
            assert response.headers["Content-Range"] == "bytes 2-5/10"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_music_catalog_pages_ten_thousand_prevalidated_records(tmp_path) -> None:
    catalog = MusicCatalog(tmp_path / "library")
    source = tmp_path / "shared-test-source.wav"
    source.write_bytes(b"prevalidated-audio")
    stat = source.stat()
    tags = json.dumps({
        "mood": ["playful"], "genre": [], "energy": [], "pacing": [],
        "scene_role": ["comedy-failure"], "edit_behavior": [],
        "cultural_tone": [], "instrument": [], "vocal_type": [],
    })
    rows = [(
        f"music_{index:05d}", f"{index:064x}", str(source), stat.st_size, stat.st_mtime_ns,
        f"Track {index:05d}", "Test", "stress-fixture", 60.0,
        "[]", "[]", "[]", "[]", tags, "verified", '["youtube"]', "now", "now",
    ) for index in range(10_000)]
    with catalog.connect() as db:
        db.executemany(
            """INSERT INTO music_assets(
            id,sha256,path,file_size,file_mtime_ns,title,author,source,duration,
            beats_json,energy_json,sections_json,loops_json,tags_json,license_status,
            platforms_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        db.executemany(
            "INSERT INTO music_fts(id,title,author,tags,source) VALUES(?,?,?,?,?)",
            [(row[0], row[5], row[6], tags, row[7]) for row in rows],
        )
    first = catalog.find("playful", scene="comedy-failure", duration=30, platforms=["youtube"], top=3)
    second = catalog.find("playful", scene="comedy-failure", duration=30, platforms=["youtube"], top=3, offset=3)
    assert first["total_eligible"] == 10_000
    assert first["count"] == second["count"] == 3
    assert first["next_offset"] == 3 and second["offset"] == 3
    assert {item["id"] for item in first["results"]}.isdisjoint({item["id"] for item in second["results"]})


def test_music_plan_apply_is_available_to_atomic_command_batches(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / "approved.wav"
    source.write_bytes(b"approved-audio")

    def seed(document):
        document.media.append(MediaAsset(
            id="media_music", kind="audio", path=str(source),
            original_name=source.name, size=source.stat().st_size, sha256="b" * 64,
            technical=MediaTechnicalInfo(duration=8, audio_codec="pcm_s16le"),
        ))
        document.tracks.append(Track(
            id="A_MUSIC", type=TrackType.AUDIO, name="Music", metadata={"role": "music"},
        ))

    manager.mutate("test.music", "seed music", seed)
    asset = {
        "id": "music_approved", "path": str(source), "duration": 8.0,
        "license_status": "verified",
    }
    monkeypatch.setattr("facut.director.music.MusicCatalog", lambda: SimpleNamespace(get=lambda _: asset))
    plan = {
        "version": "music_cue_plan.v1", "id": "plan_approved", "asset_id": asset["id"],
        "timeline_start": 0, "duration": 4, "source_in": 0, "approved": True,
        "segments": [{"timeline_start": 0, "source_in": 0, "source_out": 4}],
    }
    result = CommandEngine(manager).run_batch({
        "atomic": True,
        "commands": [{"action": "library.music.apply", "plan": plan}],
    })
    assert result["status"] == "success"
    assert len(manager.require_document().find_track("A_MUSIC").clips) == 1

    manager.undo()
    assert len(manager.require_document().find_track("A_MUSIC").clips) == 0
    before = manager.project_file.read_bytes()
    with pytest.raises(CommandEngineError):
        CommandEngine(manager).run_batch({
            "atomic": True,
            "commands": [
                {"action": "library.music.apply", "plan": plan},
                {"action": "unsupported.operation"},
            ],
        })
    assert manager.project_file.read_bytes() == before
