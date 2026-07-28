"""Compile a facut timeline into one deterministic FFmpeg filter graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from facut.core.models import Clip, MediaKind, ProjectDocument, TrackType
from facut.core.timeline_engine import TimelineEngine
from facut.effects import registry as effect_registry
from facut.transitions import registry as transition_registry


@dataclass(frozen=True, slots=True)
class FilterGraph:
    input_args: tuple[str, ...]
    filter_complex: str
    video_label: str
    audio_label: str
    duration: float
    source_paths: tuple[Path, ...]


def _fmt(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".")


def _atempo(rate: float) -> str:
    filters: list[str] = []
    remaining = rate
    while remaining > 2:
        filters.append("atempo=2")
        remaining /= 2
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={_fmt(remaining)}")
    return ",".join(filters)


def _transition_filter(name: str, default: str, parameters: dict[str, Any]) -> str:
    """Resolve direction-sensitive FFmpeg xfade variants."""

    direction = parameters.get("direction", "left")
    if name == "slide":
        return {
            "left": "slideleft",
            "right": "slideright",
            "up": "slideup",
            "down": "slidedown",
        }.get(direction, default)
    if name == "wipe":
        return {
            "left": "wipeleft",
            "right": "wiperight",
            "up": "wipeup",
            "down": "wipedown",
        }.get(direction, default)
    return default


class GraphBuilder:
    """Build the v1 single-video-track render graph.

    Multiple source files, audio, gaps, speed, effects and registered
    transitions are handled. Compositing multiple enabled video tracks is
    deliberately rejected until the overlay renderer is selected, rather than
    silently producing a misleading output.
    """

    def __init__(self, project: ProjectDocument, project_dir: str | Path) -> None:
        self.project = project
        self.project_dir = Path(project_dir)

    def _source(self, stored_path: str) -> Path:
        path = Path(stored_path)
        return path if path.is_absolute() else (self.project_dir / path).resolve()

    def build(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        range_from: float | None = None,
        range_to: float | None = None,
        preview: bool = False,
    ) -> FilterGraph:
        TimelineEngine(self.project).validate()
        width = width or self.project.project.width
        height = height or self.project.project.height
        fps = fps or self.project.project.fps
        video_tracks = [
            track
            for track in self.project.tracks
            if track.enabled
            and not track.muted
            and track.type in {TrackType.VIDEO, TrackType.IMAGE}
            and any(clip.enabled for clip in track.clips)
        ]
        if not video_tracks:
            raise ValueError("The timeline has no enabled video clips.")
        if len(video_tracks) > 1:
            raise NotImplementedError(
                "NOT_IMPLEMENTED: v1 rendering currently supports one enabled "
                "video/image track; disable other video tracks before rendering."
            )
        track = video_tracks[0]
        clips = sorted(
            (clip for clip in track.clips if clip.enabled),
            key=lambda clip: clip.timeline_start,
        )
        inputs: list[str] = []
        paths: list[Path] = []
        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, clip in enumerate(clips):
            asset = self.project.find_media(clip.media_id)
            if asset is None:
                raise ValueError(f"Clip {clip.id} references missing media {clip.media_id}.")
            source = self._source(asset.path)
            if not source.is_file():
                raise FileNotFoundError(f"Media file is offline: {asset.original_name}")
            paths.append(source)
            if asset.kind == MediaKind.IMAGE:
                inputs.extend(
                    ["-loop", "1", "-t", _fmt(clip.source_out - clip.source_in), "-i", str(source)]
                )
            else:
                inputs.extend(["-i", str(source)])
            speed = abs(clip.speed)
            video_chain = [
                f"[{index}:v:0]trim=start={_fmt(clip.source_in)}:end={_fmt(clip.source_out)}",
                "setpts=PTS-STARTPTS",
            ]
            if clip.speed < 0:
                video_chain.append("reverse")
            if abs(speed - 1) > 1e-9:
                video_chain.append(f"setpts=PTS/{_fmt(speed)}")
            transform = clip.transform
            if transform.crop_left or transform.crop_right or transform.crop_top or transform.crop_bottom:
                crop_w = f"iw-{_fmt(transform.crop_left + transform.crop_right)}"
                crop_h = f"ih-{_fmt(transform.crop_top + transform.crop_bottom)}"
                video_chain.append(
                    f"crop={crop_w}:{crop_h}:{_fmt(transform.crop_left)}:{_fmt(transform.crop_top)}"
                )
            if transform.flip_x:
                video_chain.append("hflip")
            if transform.flip_y:
                video_chain.append("vflip")
            if transform.rotation:
                video_chain.append(f"rotate={_fmt(transform.rotation)}*PI/180:ow=rotw(iw):oh=roth(ih)")
            if not preview:
                for effect in clip.effects:
                    if effect.enabled:
                        video_chain.append(
                            effect_registry.get(effect.type).compile(effect.parameters)
                        )
            video_chain.extend(
                [
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={self.project.project.background}",
                    f"fps={_fmt(fps)}",
                    "setsar=1",
                    "format=yuv420p",
                ]
            )
            filters.append(",".join(video_chain) + f"[v{index}]")
            video_labels.append(f"v{index}")
            if asset.technical.audio_codec and not clip.muted:
                audio_chain = [
                    f"[{index}:a:0]atrim=start={_fmt(clip.source_in)}:end={_fmt(clip.source_out)}",
                    "asetpts=PTS-STARTPTS",
                ]
                if clip.speed < 0:
                    audio_chain.append("areverse")
                if abs(speed - 1) > 1e-9:
                    audio_chain.append(_atempo(speed))
                if clip.volume_db:
                    audio_chain.append(f"volume={_fmt(clip.volume_db)}dB")
                audio_chain.extend(
                    [
                        f"aresample={self.project.project.sample_rate}",
                        "aformat=sample_fmts=fltp:channel_layouts=stereo",
                    ]
                )
                filters.append(",".join(audio_chain) + f"[a{index}]")
            else:
                filters.append(
                    "anullsrc=r={rate}:cl=stereo,atrim=duration={duration}[a{index}]".format(
                        rate=self.project.project.sample_rate,
                        duration=_fmt(clip.duration),
                        index=index,
                    )
                )
            audio_labels.append(f"a{index}")

        transitions = {
            (item.from_clip_id, item.to_clip_id): item
            for item in self.project.transitions
            if item.from_clip_id and item.to_clip_id
        }
        current_v = video_labels[0]
        current_a = audio_labels[0]
        current_duration = clips[0].duration
        if clips[0].timeline_start > 1e-9:
            gap = clips[0].timeline_start
            filters.append(
                f"color=c={self.project.project.background}:s={width}x{height}:r={_fmt(fps)}:d={_fmt(gap)}[vg0]"
            )
            filters.append(
                f"anullsrc=r={self.project.project.sample_rate}:cl=stereo,atrim=duration={_fmt(gap)}[ag0]"
            )
            filters.append(f"[vg0][{current_v}]concat=n=2:v=1:a=0[vp0]")
            filters.append(f"[ag0][{current_a}]concat=n=2:v=0:a=1[ap0]")
            current_v, current_a = "vp0", "ap0"
            current_duration += gap

        for index in range(1, len(clips)):
            previous, clip = clips[index - 1], clips[index]
            transition = transitions.get((previous.id, clip.id))
            overlap = previous.end - clip.timeline_start
            out_v, out_a = f"vc{index}", f"ac{index}"
            if transition is not None:
                definition = transition_registry.get(transition.type)
                xfade_name = _transition_filter(
                    definition.name, definition.xfade_name, transition.parameters
                )
                offset = clip.timeline_start
                filters.append(
                    f"[{current_v}][{video_labels[index]}]xfade="
                    f"transition={xfade_name}:duration={_fmt(transition.duration)}:"
                    f"offset={_fmt(offset)}[{out_v}]"
                )
                filters.append(
                    f"[{current_a}][{audio_labels[index]}]acrossfade="
                    f"d={_fmt(transition.duration)}:c1=tri:c2=tri[{out_a}]"
                )
                current_duration = max(current_duration, clip.timeline_start + clip.duration)
            else:
                gap = clip.timeline_start - current_duration
                nodes_v = [f"[{current_v}]"]
                nodes_a = [f"[{current_a}]"]
                count = 2
                if gap > 1e-9:
                    filters.append(
                        f"color=c={self.project.project.background}:s={width}x{height}:"
                        f"r={_fmt(fps)}:d={_fmt(gap)}[vg{index}]"
                    )
                    filters.append(
                        f"anullsrc=r={self.project.project.sample_rate}:cl=stereo,"
                        f"atrim=duration={_fmt(gap)}[ag{index}]"
                    )
                    nodes_v.append(f"[vg{index}]")
                    nodes_a.append(f"[ag{index}]")
                    count += 1
                nodes_v.append(f"[{video_labels[index]}]")
                nodes_a.append(f"[{audio_labels[index]}]")
                filters.append(
                    "".join(nodes_v) + f"concat=n={count}:v=1:a=0[{out_v}]"
                )
                filters.append(
                    "".join(nodes_a) + f"concat=n={count}:v=0:a=1[{out_a}]"
                )
                current_duration += max(0.0, gap) + clip.duration
            current_v, current_a = out_v, out_a

        output_duration = current_duration
        if range_from is not None or range_to is not None:
            start = max(0.0, range_from or 0.0)
            end = min(current_duration, range_to or current_duration)
            if end <= start:
                raise ValueError("Preview/render range end must be after its start.")
            filters.append(
                f"[{current_v}]trim=start={_fmt(start)}:end={_fmt(end)},"
                f"setpts=PTS-STARTPTS[vout]"
            )
            filters.append(
                f"[{current_a}]atrim=start={_fmt(start)}:end={_fmt(end)},"
                f"asetpts=PTS-STARTPTS[aout]"
            )
            current_v, current_a = "vout", "aout"
            output_duration = end - start
        return FilterGraph(
            input_args=tuple(inputs),
            filter_complex=";".join(filters),
            video_label=current_v,
            audio_label=current_a,
            duration=output_duration,
            source_paths=tuple(paths),
        )
