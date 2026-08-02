"""Lossless concat-demuxer fast path for unchanged sequential media."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable

from facut.core.models import AudioProcessing, ProjectDocument, TrackType, Transform

from .ffmpeg_backend import RenderError, RenderResult, _friendly_failure


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class DirectCopyPlan:
    eligible: bool
    reason: str | None
    clips: list[tuple[Path, float, float]]
    duration: float
    warnings: list[str]


def _codec_family(value: str | None) -> str:
    normalized = str(value or "").casefold()
    if normalized in {"h264", "avc", "avc1", "libx264"}:
        return "h264"
    if normalized in {"h265", "hevc", "hev1", "hvc1", "libx265"}:
        return "h265"
    return normalized


def direct_copy_plan(
    project: ProjectDocument,
    project_dir: str | Path,
    *,
    codec: str,
    width: int | None,
    height: int | None,
    fps: float | None,
    audio_sample_rate: int | None,
    color_space: str | None,
    allow_trimmed: bool = False,
) -> DirectCopyPlan:
    """Prove whether concat stream-copy preserves the requested result."""

    def reject(reason: str) -> DirectCopyPlan:
        return DirectCopyPlan(False, reason, [], 0.0, [])

    tracks = [
        track
        for track in project.tracks
        if track.enabled
        and not track.muted
        and track.type == TrackType.VIDEO
        and any(clip.enabled for clip in track.clips)
    ]
    if len(tracks) != 1:
        return reject("stream-copy requires exactly one enabled video track")
    if project.transitions:
        return reject("transitions require rendered frames")
    if project.subtitle_cues or any(item.enabled for item in project.text_overlays):
        return reject("subtitles or text overlays require rendered frames")
    if any(
        track.enabled
        and not track.muted
        and track.type in {TrackType.AUDIO, TrackType.IMAGE, TrackType.ADJUSTMENT, TrackType.MASK}
        and any(clip.enabled for clip in track.clips)
        for track in project.tracks
    ):
        return reject("additional audio, image, adjustment, or mask tracks require mixing")

    clips = sorted(
        (clip for clip in tracks[0].clips if clip.enabled),
        key=lambda item: item.timeline_start,
    )
    if not clips:
        return reject("the video track is empty")
    requested_width = width or project.project.width
    requested_height = height or project.project.height
    requested_fps = fps or project.project.fps
    tolerance = 1.0 / max(requested_fps, 1.0)
    expected = 0.0
    signature: tuple[Any, ...] | None = None
    planned: list[tuple[Path, float, float]] = []
    warnings: list[str] = []
    for clip in clips:
        if abs(clip.timeline_start - expected) > tolerance:
            return reject("timeline gaps or overlaps exceed one output frame")
        if abs(clip.timeline_start - expected) > 1e-7:
            warnings.append(
                f"Sub-frame boundary at {clip.id} was snapped during stream-copy assembly."
            )
        if (
            clip.speed != 1.0
            or clip.freeze_frame is not None
            or clip.loop
            or clip.timeline_duration is not None
            or clip.muted
            or clip.volume_db != 0.0
            or clip.audio_fade_in != 0.0
            or clip.audio_fade_out != 0.0
            or clip.audio != AudioProcessing()
            or clip.transform != Transform()
            or clip.effects
            or clip.keyframes
        ):
            return reject(f"clip {clip.id} has processing that requires rendered frames")
        asset = project.find_media(clip.media_id)
        if asset is None or asset.kind.value != "video":
            return reject(f"clip {clip.id} does not reference video media")
        technical = asset.technical
        if technical.variable_frame_rate or technical.rotation:
            return reject(f"media {asset.id} requires VFR or rotation handling")
        if (
            technical.width != requested_width
            or technical.height != requested_height
            or technical.frame_rate is None
            or abs(technical.frame_rate - requested_fps) > 0.01
        ):
            return reject("source resolution or frame rate differs from the requested output")
        if _codec_family(technical.video_codec) != _codec_family(codec):
            return reject("source video codec differs from the requested output codec")
        if audio_sample_rate and technical.sample_rate != audio_sample_rate:
            return reject("source audio sample rate differs from the requested output")
        if color_space is not None:
            return reject("delivery color conversion or relabelling requires the render graph")
        current_signature = (
            _codec_family(technical.video_codec),
            technical.width,
            technical.height,
            round(technical.frame_rate or 0.0, 4),
            technical.pixel_format,
            _codec_family(technical.audio_codec),
            technical.audio_channels,
            technical.sample_rate,
        )
        if signature is None:
            signature = current_signature
        elif current_signature != signature:
            return reject("source stream parameters are not identical")
        duration = technical.duration
        if duration is None:
            return reject(f"media {asset.id} has no probed duration")
        full_source = clip.source_in <= tolerance and abs(clip.source_out - duration) <= tolerance
        if not full_source and not allow_trimmed:
            return reject("trimmed clips require frame-accurate decoding")
        if clip.source_out > duration + tolerance:
            return reject(f"clip {clip.id} exceeds its source duration")
        source = Path(asset.path)
        if not source.is_absolute():
            source = (Path(project_dir) / source).resolve()
        if not source.is_file():
            return reject(f"source media is missing: {source}")
        planned.append((source, max(0.0, clip.source_in), min(duration, clip.source_out)))
        expected += min(duration, clip.source_out) - max(0.0, clip.source_in)
    if allow_trimmed and any(start > tolerance or abs(end - project.find_media(clip.media_id).technical.duration) > tolerance for clip, (_, start, end) in zip(clips, planned)):
        warnings.append(
            "Forced stream-copy uses keyframe-aligned trim points; exact source frames are not guaranteed."
        )
    return DirectCopyPlan(True, None, planned, expected, warnings)


def render_direct_copy(
    ffmpeg: str,
    plan: DirectCopyPlan,
    output: str | Path,
    *,
    overwrite: bool,
    progress: ProgressCallback | None = None,
) -> RenderResult:
    """Assemble an eligible plan without decoding or re-encoding streams."""

    if not plan.eligible:
        raise ValueError(plan.reason or "timeline is not eligible for stream-copy")
    destination = Path(output)
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f'Output "{destination}" already exists; use overwrite to replace it.'
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="facut-direct-copy-",
        suffix=".ffconcat",
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
    ) as concat_file:
        concat_path = Path(concat_file.name)
        concat_file.write("ffconcat version 1.0\n")
        for source, source_in, source_out in plan.clips:
            safe = source.resolve().as_posix().replace("'", r"'\''")
            concat_file.write(f"file '{safe}'\n")
            if source_in > 1e-7:
                concat_file.write(f"inpoint {source_in:.9f}\n")
            concat_file.write(f"outpoint {source_out:.9f}\n")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-t",
        f"{plan.duration:.9f}",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(destination),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        concat_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        raise RenderError(
            _friendly_failure(completed.stderr),
            command=args,
            detail=completed.stderr[-8000:],
        )
    if progress:
        elapsed = max(time.monotonic() - started, 0.000001)
        progress(
            {
                "event": "progress",
                "stage": "stream_copy",
                "progress": 1.0,
                "out_time_seconds": plan.duration,
                "frame": 0,
                "fps": 0.0,
                "speed": f"{plan.duration / elapsed:.2f}x",
                "eta_seconds": 0.0,
            }
        )
    return RenderResult(
        output=destination.resolve(),
        duration=plan.duration,
        encoder="copy",
        hardware="stream-copy",
        warnings=[
            "Lossless stream-copy fast path used; requested video/audio bitrates were not applied.",
            *plan.warnings,
        ],
    )
