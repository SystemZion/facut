"""Safe clip-level incremental rendering for eligible timelines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from facut import __version__
from facut.core.models import Clip, ProjectDocument, TrackType

from .cache import RenderCache, cache_key
from .ffmpeg_backend import FFmpegBackend, RenderError


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class IncrementalRenderResult:
    output: Path
    duration: float
    encoder: str
    hardware: str
    warnings: list[str]
    cached: bool
    segments_total: int
    segments_reused: int
    loudness: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "status": "success",
            "output": str(self.output),
            "duration": self.duration,
            "encoder": self.encoder,
            "hardware": self.hardware,
            "cached": self.cached,
            "incremental": True,
            "segments_total": self.segments_total,
            "segments_reused": self.segments_reused,
            "warnings": self.warnings,
        }
        if self.loudness is not None:
            payload["loudness"] = self.loudness
        return payload


def incremental_eligibility(project: ProjectDocument) -> tuple[bool, str | None]:
    """Return whether clip-level stream-copy assembly is currently safe."""

    video_tracks = [
        track
        for track in project.tracks
        if track.enabled
        and not track.muted
        and track.type in {TrackType.VIDEO, TrackType.IMAGE}
        and any(clip.enabled for clip in track.clips)
    ]
    if not video_tracks:
        return False, "incremental cache requires an enabled base video track"
    video_tracks.sort(key=lambda item: item.order)
    clips = sorted(
        (clip for clip in video_tracks[0].clips if clip.enabled),
        key=lambda item: item.timeline_start,
    )
    transition_pairs = {
        (item.from_clip_id, item.to_clip_id): item
        for item in project.transitions
        if item.from_clip_id and item.to_clip_id
    }
    anchor_transitions = [
        item
        for item in project.transitions
        if item.from_clip_id is None and item.to_clip_id is None
    ]
    if len(transition_pairs) + len(anchor_transitions) != len(project.transitions):
        return False, "partially anchored transitions require full graph rendering"
    base_track_id = video_tracks[0].id
    if any(
        item.track_id != base_track_id
        or item.type not in {"fade-in", "fade-out"}
        or item.at is None
        for item in anchor_transitions
    ):
        return False, "unsupported timeline-anchored transitions require full graph rendering"
    adjacent_pairs = {(left.id, right.id) for left, right in zip(clips, clips[1:])}
    if any(pair not in adjacent_pairs for pair in transition_pairs):
        return False, "non-adjacent transitions require full graph rendering"
    if clips and abs(clips[0].timeline_start) > 1e-6:
        return False, "timeline gaps require full graph rendering"
    for previous, clip in zip(clips, clips[1:]):
        transition = transition_pairs.get((previous.id, clip.id))
        if transition is None:
            if abs(clip.timeline_start - previous.end) > 1e-6:
                return False, "timeline gaps or overlaps require full graph rendering"
        else:
            overlap = previous.end - clip.timeline_start
            if abs(overlap - transition.duration) > 1 / project.project.fps + 1e-6:
                return False, "transition overlap does not match its duration"
    return True, None


class IncrementalRenderer:
    """Cache independently rendered clips and stream-copy unchanged segments."""

    def __init__(self, backend: FFmpegBackend, cache_root: str | Path) -> None:
        self.backend = backend
        self.cache = RenderCache(Path(cache_root) / "segments")
        version = subprocess.run(
            [backend.ffmpeg, "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.ffmpeg_version = (version.stdout.splitlines() or ["unknown"])[0]

    @staticmethod
    def _slice_clip(clip: Clip, start: float, end: float) -> Clip | None:
        overlap_start = max(start, clip.timeline_start)
        overlap_end = min(end, clip.end)
        if overlap_end <= overlap_start + 1e-9:
            return None
        candidate = clip.model_copy(deep=True)
        trim_start = overlap_start - clip.timeline_start
        trim_end = clip.end - overlap_end
        rate = abs(clip.speed)
        if not clip.loop and clip.freeze_frame is None:
            if clip.speed > 0:
                candidate.source_in += trim_start * rate
                candidate.source_out -= trim_end * rate
            else:
                candidate.source_out -= trim_start * rate
                candidate.source_in += trim_end * rate
        candidate.timeline_start = overlap_start - start
        if clip.timeline_duration is not None or clip.loop or clip.freeze_frame is not None:
            candidate.timeline_duration = overlap_end - overlap_start
        candidate.keyframes = [
            item.model_copy(update={"time": item.time - trim_start})
            for item in candidate.keyframes
            if trim_start - 1e-9 <= item.time <= trim_start + (overlap_end - overlap_start) + 1e-9
        ]
        audio = candidate.audio.model_dump()
        if trim_start > 1e-9:
            audio["fade_in"] = 0.0
            candidate.audio_fade_in = 0.0
        if trim_end > 1e-9:
            audio["fade_out"] = 0.0
            candidate.audio_fade_out = 0.0
        candidate.audio = candidate.audio.__class__.model_validate(audio)
        return candidate

    def _segment_document(
        self, project: ProjectDocument, clip_ids: list[str]
    ) -> ProjectDocument:
        candidate = project.model_copy(deep=True)
        base_tracks = sorted(
            (
                track
                for track in candidate.tracks
                if track.enabled
                and not track.muted
                and track.type in {TrackType.VIDEO, TrackType.IMAGE}
                and any(item.enabled for item in track.clips)
            ),
            key=lambda item: item.order,
        )
        base_clips = [
            clip for clip in base_tracks[0].clips if clip.id in set(clip_ids)
        ]
        segment_start = min(clip.timeline_start for clip in base_clips)
        segment_end = max(clip.end for clip in base_clips)
        selected_ids = set(clip_ids)
        candidate.transitions = [
            transition.model_copy(
                update={
                    "at": (
                        None
                        if transition.at is None
                        else transition.at - segment_start
                    )
                }
            )
            for transition in candidate.transitions
            if (
                transition.from_clip_id in selected_ids
                and transition.to_clip_id in selected_ids
            )
            or (
                transition.from_clip_id is None
                and transition.to_clip_id is None
                and transition.at is not None
                and segment_start <= transition.at < segment_end
            )
        ]
        selected_tracks = []
        for track in candidate.tracks:
            if track.type in {TrackType.SUBTITLE, TrackType.MASK}:
                selected_tracks.append(track)
                continue
            if track.type == TrackType.ADJUSTMENT:
                adjustments = []
                for item in track.metadata.get("adjustments", []):
                    item_start = float(item["at"])
                    item_end = item_start + float(item["duration"])
                    overlap_start = max(segment_start, item_start)
                    overlap_end = min(segment_end, item_end)
                    if overlap_end <= overlap_start:
                        continue
                    adjusted = dict(item)
                    adjusted["at"] = overlap_start - segment_start
                    adjusted["duration"] = overlap_end - overlap_start
                    adjustments.append(adjusted)
                track.metadata["adjustments"] = adjustments
                selected_tracks.append(track)
                continue
            if track.type not in {TrackType.VIDEO, TrackType.IMAGE, TrackType.AUDIO}:
                continue
            if track is base_tracks[0]:
                selected = [item for item in track.clips if item.id in selected_ids]
                for item in selected:
                    item.timeline_start -= segment_start
            else:
                selected = [
                    sliced
                    for item in track.clips
                    if item.enabled
                    for sliced in [self._slice_clip(item, segment_start, segment_end)]
                    if sliced is not None
                ]
            track.clips = selected
            if selected or track is base_tracks[0]:
                selected_tracks.append(track)
        candidate.tracks = selected_tracks
        candidate.subtitle_cues = [
            cue.model_copy(
                update={
                    "start": max(segment_start, cue.start) - segment_start,
                    "end": min(segment_end, cue.end) - segment_start,
                }
            )
            for cue in candidate.subtitle_cues
            if cue.end > segment_start and cue.start < segment_end
        ]
        candidate.text_overlays = [
            overlay.model_copy(
                update={
                    "at": max(segment_start, overlay.at) - segment_start,
                    "duration": min(segment_end, overlay.end) - max(segment_start, overlay.at),
                }
            )
            for overlay in candidate.text_overlays
            if overlay.end > segment_start and overlay.at < segment_end
        ]
        candidate.markers = [
            marker.model_copy(update={"at": marker.at - segment_start})
            for marker in candidate.markers
            if segment_start <= marker.at <= segment_end
        ]
        ducking = []
        for item in candidate.settings.get("audio_ducking", []):
            overlap_start = max(segment_start, float(item["start"]))
            overlap_end = min(segment_end, float(item["end"]))
            if overlap_end <= overlap_start:
                continue
            adjusted = dict(item)
            adjusted["start"] = overlap_start - segment_start
            adjusted["end"] = overlap_end - segment_start
            ducking.append(adjusted)
        if "audio_ducking" in candidate.settings:
            candidate.settings["audio_ducking"] = ducking
        candidate.recompute_duration()
        return candidate

    def render(
        self,
        project: ProjectDocument,
        project_dir: str | Path,
        output: str | Path,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        codec: str = "h264",
        audio_codec: str = "aac",
        audio_bitrate: str = "320k",
        bitrate: str | None = None,
        hardware: str = "auto",
        color_space: str | None = None,
        audio_sample_rate: int | None = None,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> IncrementalRenderResult:
        eligible, reason = incremental_eligibility(project)
        if not eligible:
            raise ValueError(reason or "timeline is not eligible for incremental render")
        destination = Path(output)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{destination}" already exists; use overwrite to replace it.'
            )
        track = sorted(
            (
                track
                for track in project.tracks
                if track.enabled
                and not track.muted
                and track.type in {TrackType.VIDEO, TrackType.IMAGE}
                and any(clip.enabled for clip in track.clips)
            ),
            key=lambda item: item.order,
        )[0]
        clips = sorted(
            (clip for clip in track.clips if clip.enabled),
            key=lambda item: item.timeline_start,
        )
        transition_pairs = {
            (item.from_clip_id, item.to_clip_id)
            for item in project.transitions
            if item.from_clip_id and item.to_clip_id
        }
        units: list[list[Clip]] = []
        current_unit: list[Clip] = []
        for clip in clips:
            if not current_unit:
                current_unit = [clip]
                continue
            if (current_unit[-1].id, clip.id) in transition_pairs:
                current_unit.append(clip)
            else:
                units.append(current_unit)
                current_unit = [clip]
        if current_unit:
            units.append(current_unit)
        segment_paths: list[Path] = []
        warnings: list[str] = []
        reused = 0
        encoder = "copy"
        selected_hardware = "cache"
        for index, unit in enumerate(units):
            unit_start = min(clip.timeline_start for clip in unit)
            unit_end = max(clip.end for clip in unit)
            clip_ids = [clip.id for clip in unit]
            segment_document = self._segment_document(project, clip_ids)
            parameters = {
                "segment": {
                    "tracks": [item.model_dump(mode="json") for item in segment_document.tracks],
                    "transitions": [
                        item.model_dump(mode="json") for item in segment_document.transitions
                    ],
                    "subtitle_cues": [item.model_dump(mode="json") for item in segment_document.subtitle_cues],
                    "text_overlays": [item.model_dump(mode="json") for item in segment_document.text_overlays],
                    "settings": segment_document.settings,
                },
                "project": {
                    "width": width or project.project.width,
                    "height": height or project.project.height,
                    "fps": fps or project.project.fps,
                    "sample_rate": project.project.sample_rate,
                    "background": project.project.background,
                },
                "codec": codec,
                "audio_codec": audio_codec,
                "audio_bitrate": audio_bitrate,
                "bitrate": bitrate,
                "hardware": hardware,
                "color_space": color_space,
                "audio_sample_rate": audio_sample_rate,
            }
            segment_sources: list[Path] = []
            for segment_track in segment_document.tracks:
                for segment_clip in segment_track.clips:
                    segment_asset = segment_document.find_media(segment_clip.media_id)
                    if segment_asset is None:
                        continue
                    segment_source = Path(segment_asset.path)
                    if not segment_source.is_absolute():
                        segment_source = (Path(project_dir) / segment_source).resolve()
                    segment_sources.append(segment_source)
            key = cache_key(
                inputs=sorted(set(segment_sources), key=lambda item: str(item).casefold()),
                parameters=parameters,
                software_version=__version__,
                ffmpeg_version=self.ffmpeg_version,
            )
            # Lossless PCM intermediates avoid AAC encoder-delay timestamps at
            # segment joins. The final mux copies video and encodes audio once.
            cached = self.cache.lookup(key, suffix=".mov")
            if cached is not None:
                reused += 1
                segment = cached
                if progress:
                    progress(
                        {
                            "event": "progress",
                            "stage": "segment_cache",
                            "progress": (index + 1) / len(units),
                            "out_time_seconds": unit_end,
                            "frame": 0,
                            "fps": 0.0,
                            "speed": None,
                            "eta_seconds": None,
                            "segment_index": index,
                            "segment_cached": True,
                            "clip_id": clip_ids[0],
                            "clip_ids": clip_ids,
                        }
                    )
            else:
                segment = self.cache.path_for(key, suffix=".mov")
                segment.parent.mkdir(parents=True, exist_ok=True)

                def segment_progress(event: dict[str, Any]) -> None:
                    if not progress:
                        return
                    local = float(event.get("progress", 0.0) or 0.0)
                    progress(
                        {
                            **event,
                            "stage": "segment_render",
                            "progress": (index + local) / len(units),
                            "out_time_seconds": unit_start
                            + float(event.get("out_time_seconds", 0.0) or 0.0),
                            "segment_index": index,
                            "segment_cached": False,
                            "clip_id": clip_ids[0],
                            "clip_ids": clip_ids,
                        }
                    )

                rendered = self.backend.render(
                    segment_document,
                    project_dir,
                    segment,
                    width=width,
                    height=height,
                    fps=fps,
                    codec=codec,
                    audio_codec="pcm_s16le",
                    audio_bitrate=audio_bitrate,
                    bitrate=bitrate,
                    hardware=hardware,
                    color_space=color_space,
                    audio_sample_rate=audio_sample_rate,
                    overwrite=False,
                    progress=segment_progress,
                )
                self.cache.commit(segment)
                encoder = rendered.encoder
                selected_hardware = rendered.hardware
                warnings.extend(rendered.warnings)
            segment_paths.append(segment)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="facut-concat-",
            suffix=".txt",
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
        ) as concat_file:
            concat_path = Path(concat_file.name)
            for segment in segment_paths:
                safe = segment.resolve().as_posix().replace("'", r"'\''")
                concat_file.write(f"file '{safe}'\n")
        args = [
            self.backend.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "copy",
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            *(["-ar", str(audio_sample_rate)] if audio_sample_rate else []),
            *(
                [
                    "-color_primaries", color_space,
                    "-color_trc", color_space,
                    "-colorspace", color_space,
                ]
                if color_space
                else []
            ),
            *(
                [
                    "-bsf:v",
                    "h264_metadata=colour_primaries=1:"
                    "transfer_characteristics=1:matrix_coefficients=1",
                ]
                if color_space == "bt709"
                else []
            ),
            "-t",
            f"{project.project.duration:.9f}",
            "-movflags",
            "+faststart",
            str(destination),
        ]
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
                "FFmpeg could not assemble cached render segments.",
                command=args,
                detail=completed.stderr[-8000:],
            )
        if progress:
            progress(
                {
                    "event": "progress",
                    "stage": "mux",
                    "progress": 1.0,
                    "out_time_seconds": project.project.duration,
                    "frame": 0,
                    "fps": 0.0,
                    "speed": None,
                    "eta_seconds": 0.0,
                }
            )
        return IncrementalRenderResult(
            output=destination.resolve(),
            duration=project.project.duration,
            encoder=encoder,
            hardware=selected_hardware,
            warnings=warnings,
            cached=reused == len(units),
            segments_total=len(units),
            segments_reused=reused,
        )
