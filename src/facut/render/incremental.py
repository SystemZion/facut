"""Safe clip-level incremental rendering for eligible timelines."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from facut import __version__
from facut.core.models import ProjectDocument, TrackType

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

    def as_dict(self) -> dict[str, Any]:
        return {
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
    if len(video_tracks) != 1:
        return False, "incremental cache requires exactly one enabled video track"
    if project.transitions:
        return False, "transitions cross segment boundaries"
    if project.subtitle_cues or any(item.enabled for item in project.text_overlays):
        return False, "timed subtitles or text cross segment boundaries"
    if any(
        track.enabled and track.type == TrackType.AUDIO and any(c.enabled for c in track.clips)
        for track in project.tracks
    ):
        return False, "independent audio tracks cross segment boundaries"
    clips = sorted(
        (clip for clip in video_tracks[0].clips if clip.enabled),
        key=lambda item: item.timeline_start,
    )
    expected = 0.0
    for clip in clips:
        if abs(clip.timeline_start - expected) > 1e-6:
            return False, "timeline gaps require full graph rendering"
        expected = clip.end
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

    def _segment_document(
        self, project: ProjectDocument, clip_id: str
    ) -> ProjectDocument:
        candidate = project.model_copy(deep=True)
        candidate.transitions = []
        candidate.subtitle_cues = []
        candidate.text_overlays = []
        candidate.markers = []
        selected_tracks = []
        for track in candidate.tracks:
            if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
                continue
            selected = [clip for clip in track.clips if clip.id == clip_id]
            if not selected:
                continue
            selected[0].timeline_start = 0.0
            track.clips = selected
            selected_tracks.append(track)
        candidate.tracks = selected_tracks
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
        track = next(
            track
            for track in project.tracks
            if track.enabled
            and track.type in {TrackType.VIDEO, TrackType.IMAGE}
            and any(clip.enabled for clip in track.clips)
        )
        clips = sorted(
            (clip for clip in track.clips if clip.enabled),
            key=lambda item: item.timeline_start,
        )
        segment_paths: list[Path] = []
        warnings: list[str] = []
        reused = 0
        encoder = "copy"
        selected_hardware = "cache"
        for index, clip in enumerate(clips):
            asset = project.find_media(clip.media_id)
            assert asset is not None
            source = Path(asset.path)
            if not source.is_absolute():
                source = (Path(project_dir) / source).resolve()
            parameters = {
                "clip": clip.model_dump(mode="json"),
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
            }
            key = cache_key(
                inputs=[source],
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
                            "progress": (index + 1) / len(clips),
                            "out_time_seconds": clip.end,
                            "frame": 0,
                            "fps": 0.0,
                            "speed": None,
                            "eta_seconds": None,
                            "segment_index": index,
                            "segment_cached": True,
                            "clip_id": clip.id,
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
                            "progress": (index + local) / len(clips),
                            "out_time_seconds": clip.timeline_start
                            + float(event.get("out_time_seconds", 0.0) or 0.0),
                            "segment_index": index,
                            "segment_cached": False,
                            "clip_id": clip.id,
                        }
                    )

                rendered = self.backend.render(
                    self._segment_document(project, clip.id),
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
                    overwrite=False,
                    progress=segment_progress,
                )
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
            cached=reused == len(clips),
            segments_total=len(clips),
            segments_reused=reused,
        )
