"""Safe FFmpeg process backend with progress events and encoder fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import re
from typing import Any, Callable

from facut.core.models import ProjectDocument
from facut.exceptions import NotImplementedFacutError
from facut.render.graph_builder import FilterGraph, GraphBuilder
from facut.render.hardware import EncoderChoice, available_encoders, choose_h264_encoder
from facut.subtitles.compiler import SubtitleCompiler


class RenderError(RuntimeError):
    """A render process failed with a concise, user-facing diagnostic."""

    code = "RENDER_FAILED"
    exit_code = 6

    def __init__(
        self,
        message: str,
        *,
        command: list[str],
        detail: str = "",
        log_path: str | Path | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.detail = detail
        self.log_path = str(log_path) if log_path else None


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


def _progress_number(value: str | None, default: float = 0.0) -> float:
    """Parse FFmpeg progress values, including startup-time N/A markers."""

    if value is None or value.strip().upper() in {"", "N/A"}:
        return default
    try:
        return float(value)
    except ValueError:
        return default


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


def _validate_color_delivery(
    project: ProjectDocument, color_space: str | None
) -> None:
    """Prevent HDR/Log footage from being silently relabelled as BT.709 SDR."""

    if color_space != "bt709":
        return
    used_media = {
        clip.media_id
        for track in project.tracks
        if track.enabled
        for clip in track.clips
        if clip.enabled
    }
    unsafe: list[str] = []
    for asset in project.media:
        if asset.id not in used_media:
            continue
        technical = asset.technical
        wide_gamut = str(technical.color_primaries or "").casefold() in {
            "bt2020",
            "smpte431",
            "smpte432",
        } or str(technical.color_space or "").casefold().startswith("bt2020")
        if technical.dynamic_range in {"hdr-pq", "hdr-hlg", "log"} or wide_gamut:
            unsafe.append(f"{asset.id} ({technical.dynamic_range})")
    if unsafe:
        raise NotImplementedFacutError(
            "BT.709 SDR delivery was blocked because HDR/Log or wide-gamut source media "
            "requires a real tone-map and gamut conversion: " + ", ".join(unsafe),
            suggestion=(
                "Convert the source with a reviewed color-managed workflow or render without "
                "a BT.709 delivery preset. FACUT will not silently relabel HDR as SDR."
            ),
            details={"media": unsafe, "requested_color_space": color_space},
        )


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
        color_space: str | None = None,
        audio_sample_rate: int | None = None,
        overwrite: bool = False,
        preview: bool = False,
        range_from: float | None = None,
        range_to: float | None = None,
        progress: ProgressCallback | None = None,
        burn_subtitle: str | Path | None = None,
        loudness_target: float | None = None,
        true_peak: float = -1.0,
        loudness_range: float = 11.0,
    ) -> RenderResult:
        destination = Path(output)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{destination}" already exists; use overwrite to replace it.'
            )
        if codec not in {"h264", "libx264"}:
            raise NotImplementedError(
                f"NOT_IMPLEMENTED: the current backend renders H.264, not {codec}."
            )
        _validate_color_delivery(project, color_space)
        destination.parent.mkdir(parents=True, exist_ok=True)
        generated_subtitle = None
        if burn_subtitle is not None:
            subtitle_file = Path(burn_subtitle).expanduser().resolve()
            if not subtitle_file.is_file():
                raise FileNotFoundError(f'Subtitle file "{subtitle_file}" was not found.')
            if project.subtitle_cues or any(item.enabled for item in project.text_overlays):
                raise ValueError(
                    "Use either project subtitles/text or --burn-subtitle, not both."
                )
        else:
            subtitle_file = generated_subtitle = self._compile_subtitles(project)
        log_directory = Path(project_dir) / "logs"
        try:
            graph = GraphBuilder(project, project_dir).build(
                width=width,
                height=height,
                fps=fps,
                range_from=range_from,
                range_to=range_to,
                preview=preview,
                subtitle_file=subtitle_file,
            )
            if loudness_target is not None:
                targets = {
                    "target_lufs": float(loudness_target),
                    "true_peak_db": float(true_peak),
                    "loudness_range": float(loudness_range),
                }
                measured = self._measure_loudness(
                    graph,
                    targets,
                    log_directory=log_directory,
                    progress=progress,
                )
                graph = GraphBuilder(project, project_dir).build(
                    width=width,
                    height=height,
                    fps=fps,
                    range_from=range_from,
                    range_to=range_to,
                    preview=preview,
                    subtitle_file=subtitle_file,
                    master_loudness={**targets, **measured},
                )
        except Exception:
            if generated_subtitle is not None:
                generated_subtitle.unlink(missing_ok=True)
            raise
        choice = choose_h264_encoder(self.ffmpeg, hardware)
        warnings = list(choice.warnings)
        try:
            try:
                self._run(
                    graph,
                    destination,
                    choice,
                    audio_codec=audio_codec,
                    audio_bitrate=audio_bitrate,
                    bitrate=bitrate,
                    color_space=color_space,
                    audio_sample_rate=audio_sample_rate,
                    preview=preview,
                    overwrite=overwrite,
                    progress=progress,
                    log_directory=log_directory,
                )
            except RenderError:
                if choice.hardware == "none":
                    raise
                if "libx264" not in available_encoders(self.ffmpeg):
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
                    color_space=color_space,
                    audio_sample_rate=audio_sample_rate,
                    preview=preview,
                    overwrite=True,
                    progress=progress,
                    log_directory=log_directory,
                )
                choice = software
        finally:
            if generated_subtitle is not None:
                generated_subtitle.unlink(missing_ok=True)
        return RenderResult(
            output=destination.resolve(),
            duration=graph.duration,
            encoder=choice.encoder,
            hardware=choice.hardware,
            warnings=warnings,
        )

    def _measure_loudness(
        self,
        graph: FilterGraph,
        targets: dict[str, float],
        *,
        log_directory: Path,
        progress: ProgressCallback | None,
    ) -> dict[str, float]:
        """Run EBU R128 pass one and return measured values for linear pass two."""

        measurement_label = "facut_loudness_measure"
        loudnorm = (
            f"loudnorm=I={targets['target_lufs']}:TP={targets['true_peak_db']}:"
            f"LRA={targets['loudness_range']}:print_format=json"
        )
        filter_graph = (
            graph.filter_complex
            + f";[{graph.audio_label}]{loudnorm}[{measurement_label}]"
        )
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            *graph.input_args,
            "-filter_complex",
            filter_graph,
            "-map",
            f"[{graph.video_label}]",
            "-map",
            f"[{measurement_label}]",
            "-f",
            "null",
            "-",
        ]
        if progress:
            progress(
                {
                    "event": "progress",
                    "stage": "loudness_scan",
                    "progress": 0.0,
                    "out_time_seconds": 0.0,
                    "frame": 0,
                    "fps": 0.0,
                    "speed": None,
                    "eta_seconds": None,
                }
            )
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        log_path = log_directory / f"loudness-pass1-{stamp}.log"
        log_path.write_text(
            "command_json:\n"
            + json.dumps(args, ensure_ascii=False, indent=2)
            + "\nstderr:\n"
            + completed.stderr,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RenderError(
                "FFmpeg could not complete loudness analysis pass one.",
                command=args,
                detail=completed.stderr[-8000:],
                log_path=log_path,
            )
        matches = re.findall(r"\{\s*\"input_i\".*?\}", completed.stderr, re.DOTALL)
        if not matches:
            raise RenderError(
                "FFmpeg completed loudness analysis without measured EBU R128 data.",
                command=args,
                detail=completed.stderr[-8000:],
                log_path=log_path,
            )
        payload = json.loads(matches[-1])
        names = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        measured = {name: float(payload[name]) for name in names}
        if progress:
            progress(
                {
                    "event": "progress",
                    "stage": "loudness_scan",
                    "progress": 1.0,
                    "out_time_seconds": graph.duration,
                    "frame": 0,
                    "fps": 0.0,
                    "speed": None,
                    "eta_seconds": 0.0,
                }
            )
        return measured


    @staticmethod
    def _compile_subtitles(project: ProjectDocument) -> Path | None:
        """Create a temporary ASS document only when visible text is present."""

        if not project.subtitle_cues and not any(
            overlay.enabled for overlay in project.text_overlays
        ):
            return None
        plan = SubtitleCompiler().compile(project)
        with tempfile.NamedTemporaryFile(
            prefix="facut-subtitles-", suffix=".ass", delete=False
        ) as temporary:
            path = Path(temporary.name)
        plan.write(path, overwrite=True)
        return path

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
        color_space: str | None,
        audio_sample_rate: int | None,
        preview: bool,
        overwrite: bool,
        progress: ProgressCallback | None,
        log_directory: Path | None = None,
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
        # Keep every H.264 output broadly playable.  The filter graph also
        # normalizes to yuv420p, while this encoder option is a second boundary
        # against hardware/filter-specific pixel-format promotion.
        args.extend(["-pix_fmt", "yuv420p"])
        if color_space:
            args.extend(
                [
                    "-color_primaries", color_space,
                    "-color_trc", color_space,
                    "-colorspace", color_space,
                ]
            )
            if color_space == "bt709":
                args.extend(
                    [
                        "-bsf:v",
                        "h264_metadata=colour_primaries=1:"
                        "transfer_characteristics=1:matrix_coefficients=1",
                    ]
                )
        args.extend(
            [
                "-c:a",
                audio_codec,
                "-b:a",
                audio_bitrate,
                *(["-ar", str(audio_sample_rate)] if audio_sample_rate else []),
                "-t",
                f"{graph.duration:.9f}",
                "-shortest",
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
            started_at = time.monotonic()
            for raw_line in process.stdout:
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                event[key] = value
                if key == "progress" and progress:
                    finished = value == "end"
                    out_time_us = _progress_number(event.get("out_time_us"))
                    elapsed = graph.duration if finished else out_time_us / 1_000_000
                    ratio = (
                        1.0
                        if finished
                        else min(1.0, elapsed / graph.duration)
                        if graph.duration
                        else 1.0
                    )
                    wall_seconds = max(0.0, time.monotonic() - started_at)
                    eta_seconds = (
                        max(0.0, wall_seconds / ratio - wall_seconds)
                        if ratio > 0
                        else None
                    )
                    progress(
                        {
                            "event": "progress",
                            "stage": "render",
                            "progress": ratio,
                            "out_time_seconds": elapsed,
                            "frame": int(_progress_number(event.get("frame"))),
                            "fps": _progress_number(event.get("fps")),
                            "speed": event.get("speed", "").strip() or None,
                            "eta_seconds": eta_seconds,
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
        log_path = None
        if log_directory is not None:
            log_directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            log_path = log_directory / f"render-{stamp}.log"
            log_path.write_text(
                "FACUT FFmpeg render log\n"
                f"status: {'success' if return_code == 0 else 'failed'}\n"
                f"return_code: {return_code}\n"
                f"output: {output.resolve()}\n"
                "command_json:\n"
                + json.dumps(args, ensure_ascii=False, indent=2)
                + "\nfiltergraph:\n"
                + graph.filter_complex
                + "\nstderr:\n"
                + detail
                + "\n",
                encoding="utf-8",
            )
        if return_code != 0:
            output.unlink(missing_ok=True)
            raise RenderError(
                _friendly_failure(detail),
                command=args,
                detail=detail[-8000:],
                log_path=log_path,
            )
