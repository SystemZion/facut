"""Bounded, original-audio segment evidence; never edits or relinks project media."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import math
import os
import tempfile
import shutil
from pathlib import Path
import uuid

from facut.core.models import MediaKind
from facut.core.project_manager import ProjectManager
from facut.media.probe import probe_media, probe_raw
from facut.media.tools import find_executable, run_tool


def create_segment_evidence(manager: ProjectManager, media_id: str, *, start: float,
                            end: float, output: str | Path | None = None,
                            height: int = 540, ffmpeg=None, ffprobe=None,
                            timings: bool = False) -> dict:
    """Export [start,end) in seconds to a NEW bundle directory (max 60 seconds).

    Always reads asset.path, not a possibly silent LRF/proxy. No source fallback,
    output overwrite, project mutation, ASR, semantic ranking or remote upload.
    """
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise ValueError("Require finite 0 <= start < end")
    if end - start > 60:
        raise ValueError("Evidence is limited to 60 seconds; request smaller ranges")
    if not 120 <= height <= 1080 or height % 2:
        raise ValueError("height must be an even integer between 120 and 1080")
    timer = None
    if timings:
        from facut.core.performance import StageTimer
        timer = StageTimer()
    def stage(name):
        return timer.stage(name) if timer else nullcontext()

    document = manager.require_document()
    asset = document.find_media(media_id)
    if asset is None:
        raise ValueError(f'Media ID "{media_id}" was not found')
    if asset.kind != MediaKind.VIDEO:
        raise ValueError("Segment evidence requires a video asset")
    source = manager.resolve_path(asset.path).expanduser().resolve(strict=True)
    before = source.stat()
    with stage("probe"):
        technical = probe_media(source, ffprobe=ffprobe)
        if not technical.video_codec or not technical.duration:
            raise ValueError("Original has no readable video stream/duration")
        raw = probe_raw(source, ffprobe=ffprobe)
        video = next(stream for stream in raw.get("streams", []) if stream.get("codec_type") == "video")
        # Container duration can be longer than its video (e.g. a trailing song).
        # Fail closed when stream timing cannot establish continuous video coverage.
        video_duration = float(video.get("duration") or 0)
        if not math.isfinite(video_duration) or video_duration <= 0:
            raise ValueError("Video-stream duration unavailable; cannot verify segment coverage")
        origin = float(raw.get("format", {}).get("start_time") or 0)
        video_offset = float(video.get("start_time") or 0) - origin
        if abs(video_offset) > .001:
            raise ValueError("Offset video streams require explicit alignment; unsupported in v1")
        if end > min(technical.duration, video_duration):
            raise ValueError("Requested end exceeds original duration; no silent clamp")
        fps = technical.frame_rate or technical.average_frame_rate or 25.0
        if end - start < 1 / fps and not math.isclose(end - start, 1 / fps, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError("Requested interval is shorter than one source frame")
        audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
        audio_status = "absent_in_source"
        if audio is not None:
            audio_start = float(audio.get("start_time") or 0) - origin
            audio_duration = float(audio.get("duration") or 0)
            if not math.isfinite(audio_start) or not math.isfinite(audio_duration) or audio_duration <= 0:
                raise ValueError("Audio-stream timing unavailable; cannot verify selected audio")
            audio_status = ("included" if max(start, audio_start) < min(end, audio_start + audio_duration)
                            else "outside_selected_range")
        executable = find_executable("ffmpeg", ffmpeg)

    bundle = (Path(output).expanduser() if output is not None else
              manager.project_dir / "evidence" / ("segment-" + uuid.uuid4().hex)).resolve()
    # Exclusive directory creation protects files, symlinks, source paths and
    # existing bundles; there is deliberately no --overwrite switch.
    if bundle.exists():
        raise FileExistsError(f'Evidence destination already exists: "{bundle}"')
    bundle.parent.mkdir(parents=True, exist_ok=True)
    # Encode only in an unpredictable private sibling. Never clean up public paths:
    # another process could create/replace them between any two operations.
    staging = Path(tempfile.mkdtemp(prefix=".facut-evidence-", dir=bundle.parent))
    clip = staging / "clip.mp4"
    manifest = staging / "manifest.json"
    try:
        with stage("proxy_encode"):
            run_tool([executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                      "-ss", str(start), "-i", source, "-t", str(end-start),
                      "-map", "0:v:0",
                      *(["-map", "0:a:0", "-af", "aresample=async=1:first_pts=0"] if audio_status == "included" else ["-an"]),
                      "-map_metadata", "-1",
                      "-vf", f"scale=-2:trunc(min(ih\\,{height})/2)*2", "-c:v", "libx264",
                      "-preset", "veryfast", "-crf", "26", "-threads", "2",
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                      "-movflags", "+faststart", clip], timeout=300)
        with stage("validate"):
            actual = probe_media(clip, ffprobe=ffprobe)
            if not actual.video_codec or not actual.duration:
                raise ValueError("Evidence output has no video/duration")
            output_raw = probe_raw(clip, ffprobe=ffprobe)
            output_video = next(s for s in output_raw.get("streams", []) if s.get("codec_type") == "video")
            output_video_duration = float(output_video.get("duration") or 0)
            if abs(output_video_duration - (end-start)) > max(.1, 2 / fps):
                raise ValueError("Evidence video stream does not cover requested interval")
            if abs(actual.duration - (end-start)) > max(.1, 2 / fps):
                raise ValueError("Evidence duration differs from requested interval")
            if bool(actual.audio_codec) != (audio_status == "included"):
                raise ValueError("Original/output audio stream presence differs")
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Original changed while evidence was being generated")
            digest = hashlib.sha256(clip.read_bytes()).hexdigest()
        result = {
            "schema": "facut.segment-evidence.v1", "media_id": media_id,
            "source": {"path": str(source), "kind": "registered_original",
                       "registered_sha256": asset.sha256, "hash_reverified": False,
                       "size": before.st_size, "mtime_ns": before.st_mtime_ns},
            "range": {"start": start, "end": end, "duration": end-start,
                      "unit": "seconds", "interval": "half-open"},
            "audio": {"status": audio_status,
                      "source": str(source) if actual.audio_codec else None,
                      "stream": "0:a:0" if actual.audio_codec else None,
                      "transcoded": bool(actual.audio_codec)},
            "output": {"video": str(bundle / "clip.mp4"), "manifest": str(bundle / "manifest.json"), "sha256": digest,
                       "technical": actual.model_dump(mode="json")},
            "warnings": ["Review proxy, not delivery master; cuts are quantized to source frames."]
        }
        with stage("manifest_write"):
            manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # mkdir and hard-link creation are exclusive. A competing destination or
        # filename is an error, never an overwrite. On publication failure retain
        # any destination contents, since ownership may have changed concurrently.
        bundle.mkdir(exist_ok=False)
        os.link(clip, bundle / "clip.mp4")
        os.link(manifest, bundle / "manifest.json")
        if timer:
            timer.mark("audio_extract", "not_applicable", reason="Muxed with video in proxy_encode")
            timer.mark("frame_extract", "not_applicable", reason="Continuous clip, not still frames")
            result["timings"] = timer.report()
            # Timings belong to this CLI call, not the persistent media evidence.
        return result
    finally:
        # Only the private staging area belongs to this invocation.
        shutil.rmtree(staging)
