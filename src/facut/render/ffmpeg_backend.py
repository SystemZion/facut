"""Safe FFmpeg process backend with progress events and encoder fallback."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable

from facut.core.models import ProjectDocument
from facut.render.graph_builder import FilterGraph, GraphBuilder
from facut.render.hardware import EncoderChoice, choose_h264_encoder


class RenderError(RuntimeError):
    """A render process failed with a concise, user-facing diagnostic."""

    code = "RENDER_FAILED"
    exit_code = 6

    def __init__(self, message: str, *, command: list[str], detail: str = "") -> None:
        super().__init__(message)
        self.command = command
        self.detail = detail


@dataclass(slots=True)
class RenderResult:
    output: Path
    duration: float
    encoder: str
    hardware: str
    warnings: list[str]
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "output": str(self.output),
            "duration": self.duration,
            "encoder": self.encoder,
            "hardware": self.hardware,
            "cached": self.cached,
            "warnings": self.warnings,
        }


ProgressCallback = Callable[[dict[str, Any]], None]


def find_ffmpeg(explicit: str | Path | None = None) -> str:
    """Resolve FFmpeg without invoking a shell."""

    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(str(explicit))
        if resolved:
            return resolved
        raise FileNotFoundError(f"FFmpeg executable was not found: {explicit}")
    resolved = shutil.which("ffmpeg")
    if not resolved:
        raise FileNotFoundError(
            "FFmpeg is required. Install it or configure the ffmpeg path."
        )
    return resolved


def _friendly_failure(detail: str) -> str:
    lowered = detail.lower()
    if "no such file or directory" in lowered:
        return "An input file does not exist or cannot be opened."
    if "unknown encoder" in lowered or "encoder not found" in lowered:
        return "The requested encoder is unavailable in this FFmpeg build."
    if "error initializing output stream" in lowered or "cannot load" in lowered:
        return "The selected hardware encoder could not be initialized."
    if "invalid argument" in lowered:
        return "FFmpeg rejected a media, filter, or output parameter."
    if "permission denied" in lowered:
        return "The output file is in use or its directory is not writable."
    if "no space left" in lowered:
        return "There is not enough free disk space for the render."
    return "FFmpeg could not render the timeline."


class FFmpegBackend:
    """Compile and render projects using FFmpeg filter graphs."""

    def __init__(self, ffmpeg: str | Path | None = None) -> None:
        self.ffmpeg = find_ffmpeg(ffmpeg)

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
        audio_bitrate: str = "192k",
        bitrate: str | None = None,
        hardware: str = "auto",
        overwrite: bool = False,
        preview: bool = False,
        range_from: float | None = None,
        range_to: float | None = None,
        progress: ProgressCallback | None = None,
    ) -> RenderResult:
        destination = Path(output)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{destination}" already exists; use overwrite to replace it.'
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        graph = GraphBuilder(project, project_dir).build(
            width=width,
            height=height,
            fps=fps,
            range_from=range_from,
            range_to=range_to,
            preview=preview,
        )
        if codec not in {"h264", "libx264"}:
            raise NotImplementedError(
                f"NOT_IMPLEMENTED: the v1 backend currently renders H.264, not {codec}."
            )
        choice = choose_h264_encoder(self.ffmpeg, hardware)
        warnings = list(choice.warnings)
        try:
            self._run(
                graph,
                destination,
                choice,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                bitrate=bitrate,
                preview=preview,
                overwrite=overwrite,
                progress=progress,
            )
        except RenderError:
            if choice.hardware == "none":
                raise
            warnings.append(
                f"{choice.encoder} was detected but failed to initialize; "
                "rendered with libx264 instead."
            )
            software = EncoderChoice("libx264", "none")
            self._run(
                graph,
                destination,
                software,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                bitrate=bitrate,
                preview=preview,
                overwrite=True,
                progress=progress,
            )
            choice = software
        return RenderResult(
            output=destination.resolve(),
            duration=graph.duration,
            encoder=choice.encoder,
            hardware=choice.hardware,
            warnings=warnings,
        )

    def preview_range(
        self,
        project: ProjectDocument,
        project_dir: str | Path,
        output: str | Path,
        *,
        start: float,
        end: float,
        height: int = 540,
        fps: float = 24,
        hardware: str = "auto",
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> RenderResult:
        source_width = project.project.width
        source_height = project.project.height
        width = max(2, round(source_width * height / source_height / 2) * 2)
        return self.render(
            project,
            project_dir,
            output,
            width=width,
            height=height,
            fps=fps,
            hardware=hardware,
            overwrite=overwrite,
            preview=True,
            range_from=start,
            range_to=end,
            progress=progress,
        )

    def _run(
        self,
        graph: FilterGraph,
        output: Path,
        choice: EncoderChoice,
        *,
        audio_codec: str,
        audio_bitrate: str,
        bitrate: str | None,
        preview: bool,
        overwrite: bool,
        progress: ProgressCallback | None,
    ) -> None:
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y" if overwrite else "-n",
            *graph.input_args,
            "-filter_complex",
            graph.filter_complex,
            "-map",
            f"[{graph.video_label}]",
            "-map",
            f"[{graph.audio_label}]",
            "-c:v",
            choice.encoder,
        ]
        if choice.encoder == "libx264":
            args.extend(["-preset", "ultrafast" if preview else "medium", "-crf", "28" if preview else "18"])
        elif choice.encoder == "h264_nvenc":
            args.extend(["-preset", "p4" if preview else "p5", "-cq", "28" if preview else "20", "-b:v", "0"])
        elif choice.encoder == "h264_qsv":
            args.extend(["-preset", "veryfast" if preview else "medium", "-global_quality", "28" if preview else "20"])
        elif choice.encoder == "h264_amf":
            args.extend(["-quality", "speed" if preview else "quality", "-rc", "cqp", "-qp_i", "28" if preview else "20"])
        elif choice.encoder == "h264_videotoolbox":
            args.extend(["-b:v", bitrate or ("4M" if preview else "12M")])
        if bitrate and choice.encoder != "h264_videotoolbox":
            args.extend(["-maxrate", bitrate, "-bufsize", bitrate])
        args.extend(
            [
                "-c:a",
                audio_codec,
                "-b:a",
                audio_bitrate,
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-nostats",
                str(output),
            ]
        )
        stderr_path: Path
        with tempfile.NamedTemporaryFile(
            prefix="facut-ffmpeg-", suffix=".log", delete=False
        ) as stderr_file:
            stderr_path = Path(stderr_file.name)
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            event: dict[str, str] = {}
            for raw_line in process.stdout:
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                event[key] = value
                if key == "progress" and progress:
                    out_time_us = int(event.get("out_time_us", "0") or 0)
                    elapsed = out_time_us / 1_000_000
                    progress(
                        {
                            "event": "progress",
                            "stage": "render",
                            "progress": min(1.0, elapsed / graph.duration)
                            if graph.duration
                            else 1.0,
                            "frame": int(event.get("frame", "0") or 0),
                            "fps": float(event.get("fps", "0") or 0),
                            "eta_seconds": None,
                        }
                    )
                    event = {}
            return_code = process.wait()
        try:
            detail = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        finally:
            stderr_path.unlink(missing_ok=True)
        if return_code != 0:
            output.unlink(missing_ok=True)
            raise RenderError(
                _friendly_failure(detail), command=args, detail=detail[-8000:]
            )
