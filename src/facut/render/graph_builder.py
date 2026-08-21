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


def _keyframe_expr(
    clip: Clip, property_name: str, default: float, *, time_var: str = "t"
) -> str:
    """Compile linear numeric keyframes to an FFmpeg expression."""

    points = sorted(
        (item for item in clip.keyframes if item.property == property_name),
        key=lambda item: item.time,
    )
    if not points:
        return _fmt(default)
    expression = _fmt(float(points[-1].value))
    for left, right in reversed(list(zip(points, points[1:]))):
        start = _fmt(left.time)
        end = _fmt(right.time)
        left_value = _fmt(float(left.value))
        delta = _fmt(float(right.value) - float(left.value))
        progress = f"({time_var}-{start})/({end}-{start})"
        eased = _easing_expr(progress, left.easing)
        segment = (
            f"{left_value}+({delta})*({eased})"
        )
        expression = f"if(lt({time_var}\\,{end})\\,{segment}\\,{expression})"
    first = points[0]
    return (
        f"if(lt({time_var}\\,{_fmt(first.time)})\\,"
        f"{_fmt(float(first.value))}\\,{expression})"
    )


def _easing_expr(progress: str, easing: str) -> str:
    """Compile supported deterministic easing names to FFmpeg expressions."""

    if easing == "ease-in":
        return f"pow({progress},2)"
    if easing == "ease-out":
        return f"1-pow(1-({progress}),2)"
    if easing in {"ease-in-out", "cubic"}:
        # Smoothstep is cubic, continuous, and avoids another nested if().
        return f"({progress})*({progress})*(3-2*({progress}))"
    return progress


def _filter_path(path: str | Path) -> str:
    """Escape an absolute path for an FFmpeg filter option on every platform."""

    normalized = Path(path).resolve().as_posix()
    return (
        normalized.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(",", r"\,")
        .replace(";", r"\;")
    )


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


def _linear_from_db(value: float) -> float:
    return 10 ** (value / 20)


def _audio_processing_filters(clip: Clip, duration: float, sample_rate: int) -> list[str]:
    """Compile the shared non-destructive audio model to FFmpeg filters."""

    processing = clip.audio
    result = [f"aresample={sample_rate}"]
    if processing.channel_mode == "mono":
        result.extend(
            [
                "aformat=sample_fmts=fltp:channel_layouts=mono",
                "pan=stereo|c0=c0|c1=c0",
            ]
        )
    else:
        result.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        if processing.pan is not None:
            left_gain = 1.0 - max(0.0, processing.pan)
            right_gain = 1.0 + min(0.0, processing.pan)
            result.append(
                f"pan=stereo|c0={_fmt(left_gain)}*c0|"
                f"c1={_fmt(right_gain)}*c1"
            )
    if processing.highpass_hz is not None:
        result.append(f"highpass=f={_fmt(processing.highpass_hz)}")
    if processing.denoise_strength is not None:
        noise_reduction = 3.0 + processing.denoise_strength * 27.0
        result.append(f"afftdn=nr={_fmt(noise_reduction)}:nf=-50")
    if processing.compressor is not None:
        compressor = processing.compressor
        result.append(
            "acompressor="
            f"threshold={_fmt(_linear_from_db(compressor.threshold_db))}:"
            f"ratio={_fmt(compressor.ratio)}:"
            f"attack={_fmt(compressor.attack_ms)}:"
            f"release={_fmt(compressor.release_ms)}:"
            f"makeup={_fmt(_linear_from_db(compressor.makeup_db))}"
        )
    gain_db = clip.volume_db + processing.gain_db
    if gain_db:
        result.append(f"volume={_fmt(gain_db)}dB")
    if processing.loudness is not None:
        loudness = processing.loudness
        result.append(
            f"loudnorm=I={_fmt(loudness.target_lufs)}:"
            f"TP={_fmt(loudness.true_peak_db)}:"
            f"LRA={_fmt(loudness.loudness_range)}"
        )
    if processing.limiter is not None:
        limiter = processing.limiter
        result.append(
            f"alimiter=limit={_fmt(_linear_from_db(limiter.ceiling_db))}:"
            f"attack={_fmt(limiter.attack_ms)}:"
            f"release={_fmt(limiter.release_ms)}:level=false"
        )
    fade_in = processing.fade_in or clip.audio_fade_in
    fade_out = processing.fade_out or clip.audio_fade_out
    if fade_in:
        result.append(f"afade=t=in:st=0:d={_fmt(fade_in)}")
    if fade_out:
        result.append(
            f"afade=t=out:st={_fmt(max(0.0, duration - fade_out))}:"
            f"d={_fmt(fade_out)}"
        )
    return result


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
        subtitle_file: str | Path | None = None,
        master_loudness: dict[str, float] | None = None,
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
        video_tracks.sort(key=lambda item: item.order)
        track = video_tracks[0]
        overlay_tracks = video_tracks[1:]
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
            if preview and asset.proxy_path:
                proxy = self._source(asset.proxy_path)
                if proxy.is_file():
                    source = proxy
            if not source.is_file():
                raise FileNotFoundError(f"Media file is offline: {asset.original_name}")
            paths.append(source)
            transform = clip.transform
            if asset.kind == MediaKind.IMAGE:
                inputs.extend(
                    ["-loop", "1", "-t", _fmt(clip.duration), "-i", str(source)]
                )
            else:
                if not transform.autorotate:
                    inputs.append("-noautorotate")
                inputs.extend(["-i", str(source)])
            speed = abs(clip.speed)
            if clip.freeze_frame is not None:
                video_chain = [
                    f"[{index}:v:0]trim=start={_fmt(clip.freeze_frame)}:"
                    f"end={_fmt(clip.freeze_frame + 1 / fps)}",
                    "setpts=PTS-STARTPTS",
                    f"tpad=stop_mode=clone:stop_duration={_fmt(clip.duration)}",
                    f"trim=duration={_fmt(clip.duration)}",
                ]
            else:
                video_chain = [
                    f"[{index}:v:0]trim=start={_fmt(clip.source_in)}:end={_fmt(clip.source_out)}",
                    "setpts=PTS-STARTPTS",
                ]
            if clip.speed < 0:
                video_chain.append("reverse")
            if abs(speed - 1) > 1e-9:
                video_chain.append(f"setpts=PTS/{_fmt(speed)}")
            if transform.stabilize:
                video_chain.append("deshake")
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
            rotation = _keyframe_expr(clip, "rotation", transform.rotation)
            if rotation != "0":
                video_chain.append(
                    f"rotate=({rotation})*PI/180:ow=rotw(iw):oh=roth(ih)"
                )
            if not preview:
                for effect in clip.effects:
                    if effect.enabled:
                        video_chain.append(
                            effect_registry.get(effect.type).compile(effect.parameters)
                        )
            if transform.fit == "stretch":
                video_chain.append(f"scale={width}:{height}")
            elif transform.fit == "cover":
                video_chain.append(
                    f"scale={width}:{height}:force_original_aspect_ratio=increase"
                )
            elif transform.fit == "contain":
                video_chain.append(
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease"
                )
            scale_x = _keyframe_expr(clip, "scale_x", transform.scale_x)
            scale_y = _keyframe_expr(clip, "scale_y", transform.scale_y)
            if scale_x != "1" or scale_y != "1":
                video_chain.append(
                    f"scale='iw*({scale_x})':'ih*({scale_y})':eval=frame"
                )
            x = _keyframe_expr(clip, "x", transform.x)
            y = _keyframe_expr(clip, "y", transform.y)
            video_chain.extend(
                [
                    f"fps={_fmt(fps)}",
                    "setsar=1",
                ]
            )
            if transform.opacity < 1:
                video_chain.extend(
                    ["format=rgba", f"colorchannelmixer=aa={_fmt(transform.opacity)}"]
                )
            else:
                video_chain.append("format=yuv420p")
            filters.append(",".join(video_chain) + f"[vcontent{index}]")
            filters.append(
                f"color=c={self.project.project.background}:s={width}x{height}:"
                f"r={_fmt(fps)}:d={_fmt(clip.duration)}[vbase{index}]"
            )
            # Overlay, unlike pad, evaluates x/y once per frame and exposes t.
            # It also handles contain (content smaller than canvas), cover
            # (content larger than canvas) and static positioning consistently.
            filters.append(
                f"[vbase{index}][vcontent{index}]overlay="
                f"x=(W-w)/2+({x}):y=(H-h)/2+({y}):"
                f"eval=frame:eof_action=pass:format=auto,format=yuv420p[v{index}]"
            )
            video_labels.append(f"v{index}")
            if (
                asset.technical.audio_codec
                and clip.freeze_frame is None
                and not clip.muted
                and not clip.audio.muted
            ):
                audio_chain = [
                    f"[{index}:a:0]atrim=start={_fmt(clip.source_in)}:end={_fmt(clip.source_out)}",
                    "asetpts=PTS-STARTPTS",
                ]
                if clip.speed < 0:
                    audio_chain.append("areverse")
                if abs(speed - 1) > 1e-9:
                    audio_chain.append(_atempo(speed))
                audio_chain.extend(
                    _audio_processing_filters(
                        clip, clip.duration, self.project.project.sample_rate
                    )
                )
                # Stateful filters such as loudnorm and alimiter can shorten a
                # short stream on older FFmpeg releases because of their look-
                # ahead latency. Keep native camera audio frame-aligned with
                # its video clip after processing, just like independent
                # timeline audio below.
                audio_chain.extend(
                    [
                        f"apad=whole_dur={_fmt(clip.duration)}",
                        f"atrim=duration={_fmt(clip.duration)}",
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

        # Higher video/image tracks are composited as true picture-in-picture
        # layers. Their audio remains explicit: detach/add it to an audio track
        # when it should be heard.
        overlay_clip_ids = {
            clip.id
            for overlay_track in overlay_tracks
            for clip in overlay_track.clips
            if clip.enabled
        }
        if any(
            transition.from_clip_id in overlay_clip_ids
            or transition.to_clip_id in overlay_clip_ids
            for transition in self.project.transitions
        ):
            raise NotImplementedError(
                "NOT_IMPLEMENTED: transitions on overlay video tracks are not yet supported."
            )
        overlay_number = 0
        for overlay_track in overlay_tracks:
            for clip in sorted(
                (item for item in overlay_track.clips if item.enabled),
                key=lambda item: item.timeline_start,
            ):
                if clip.end > current_duration + 1e-6:
                    raise ValueError(
                        "An overlay clip cannot extend beyond the base video track."
                    )
                asset = self.project.find_media(clip.media_id)
                if asset is None:
                    raise ValueError(
                        f"Overlay clip {clip.id} references missing media {clip.media_id}."
                    )
                source = self._source(asset.path)
                if preview and asset.proxy_path:
                    proxy = self._source(asset.proxy_path)
                    if proxy.is_file():
                        source = proxy
                if not source.is_file():
                    raise FileNotFoundError(
                        f"Media file is offline: {asset.original_name}"
                    )
                input_index = len(paths)
                paths.append(source)
                transform = clip.transform
                blend_mode = str(clip.metadata.get("blend_mode", "normal"))
                mask_path = clip.metadata.get("mask_path")
                if blend_mode != "normal" and mask_path:
                    raise NotImplementedError(
                        "NOT_IMPLEMENTED: custom masks with non-normal blend modes "
                        "cannot be combined yet."
                    )
                if asset.kind == MediaKind.IMAGE:
                    inputs.extend(
                        ["-loop", "1", "-t", _fmt(clip.duration), "-i", str(source)]
                    )
                else:
                    if not transform.autorotate:
                        inputs.append("-noautorotate")
                    inputs.extend(["-i", str(source)])
                if clip.freeze_frame is not None:
                    chain = [
                        f"[{input_index}:v:0]trim=start={_fmt(clip.freeze_frame)}:"
                        f"end={_fmt(clip.freeze_frame + 1 / fps)}",
                        "setpts=PTS-STARTPTS",
                        f"tpad=stop_mode=clone:stop_duration={_fmt(clip.duration)}",
                        f"trim=duration={_fmt(clip.duration)}",
                    ]
                else:
                    chain = [
                        f"[{input_index}:v:0]trim=start={_fmt(clip.source_in)}:"
                        f"end={_fmt(clip.source_out)}",
                        "setpts=PTS-STARTPTS",
                    ]
                if transform.stabilize:
                    chain.append("deshake")
                if transform.crop_left or transform.crop_right or transform.crop_top or transform.crop_bottom:
                    chain.append(
                        f"crop=iw-{_fmt(transform.crop_left + transform.crop_right)}:"
                        f"ih-{_fmt(transform.crop_top + transform.crop_bottom)}:"
                        f"{_fmt(transform.crop_left)}:{_fmt(transform.crop_top)}"
                    )
                rotation = _keyframe_expr(clip, "rotation", transform.rotation)
                if rotation != "0":
                    chain.append(
                        f"rotate=({rotation})*PI/180:ow=rotw(iw):oh=roth(ih)"
                    )
                if not preview:
                    for effect in clip.effects:
                        if effect.enabled:
                            chain.append(
                                effect_registry.get(effect.type).compile(
                                    effect.parameters
                                )
                            )
                chain.append(
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease"
                )
                scale_x = _keyframe_expr(clip, "scale_x", transform.scale_x)
                scale_y = _keyframe_expr(clip, "scale_y", transform.scale_y)
                chain.append(f"scale='iw*({scale_x})':'ih*({scale_y})':eval=frame")
                if blend_mode == "normal":
                    chain.extend(
                        [
                            "format=rgba",
                            f"colorchannelmixer=aa={_fmt(transform.opacity)}",
                            f"setpts=PTS+{_fmt(clip.timeline_start)}/TB",
                        ]
                    )
                else:
                    chain.append(f"setpts=PTS+{_fmt(clip.timeline_start)}/TB")
                overlay_label = f"overlay{overlay_number}"
                filters.append(",".join(chain) + f"[{overlay_label}]")
                if mask_path:
                    mask_source = self._source(str(mask_path))
                    if not mask_source.is_file():
                        raise FileNotFoundError(f"Composite mask is offline: {mask_source}")
                    mask_index = len(paths)
                    paths.append(mask_source)
                    if mask_source.suffix.lower() in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".bmp",
                    }:
                        inputs.extend(
                            ["-loop", "1", "-t", _fmt(clip.duration), "-i", str(mask_source)]
                        )
                    else:
                        inputs.extend(["-i", str(mask_source)])
                    mask_label = f"mask{overlay_number}"
                    filters.append(
                        f"[{mask_index}:v:0]trim=duration={_fmt(clip.duration)},"
                        "setpts=PTS-STARTPTS,format=gray"
                        f"[{mask_label}]"
                    )
                    mask_scaled = f"maskscaled{overlay_number}"
                    overlay_ref = f"overlayref{overlay_number}"
                    filters.append(
                        f"[{mask_label}][{overlay_label}]scale2ref="
                        # ``iw``/``ih`` resolve to the reference input here on
                        # FFmpeg 5 through 8.  Newer ``rw``/``rh`` variables
                        # are unavailable on older release builds.
                        f"w=iw:h=ih[{mask_scaled}][{overlay_ref}]"
                    )
                    masked_label = f"masked{overlay_number}"
                    filters.append(
                        f"[{overlay_ref}][{mask_scaled}]alphamerge[{masked_label}]"
                    )
                    overlay_label = masked_label
                local_time = f"(t-{_fmt(clip.timeline_start)})"
                x = _keyframe_expr(clip, "x", transform.x, time_var=local_time)
                y = _keyframe_expr(clip, "y", transform.y, time_var=local_time)
                output_label = f"composite{overlay_number}"
                if blend_mode == "normal":
                    filters.append(
                        f"[{current_v}][{overlay_label}]overlay="
                        f"x=(W-w)/2+({x}):y=(H-h)/2+({y}):"
                        f"enable='between(t\\,{_fmt(clip.timeline_start)}\\,{_fmt(clip.end)})':"
                        f"eof_action=pass:format=auto[{output_label}]"
                    )
                else:
                    if x != "0" or y != "0":
                        raise NotImplementedError(
                            "NOT_IMPLEMENTED: positioned overlays with a non-normal "
                            "blend mode are not supported; use x=0 and y=0."
                        )
                    neutral = "white" if blend_mode == "multiply" else "black"
                    full_label = f"blendfull{overlay_number}"
                    filters.append(
                        f"[{overlay_label}]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:"
                        f"color={neutral},crop={width}:{height},format=yuv420p[{full_label}]"
                    )
                    filters.append(
                        f"[{current_v}][{full_label}]blend=all_mode={blend_mode}:"
                        f"all_opacity={_fmt(transform.opacity)}:"
                        f"enable='between(t\\,{_fmt(clip.timeline_start)}\\,{_fmt(clip.end)})'"
                        f"[{output_label}]"
                    )
                current_v = output_label
                overlay_number += 1

        # Adjustment layers apply validated effects to the complete composite
        # only inside their declared timeline interval.
        adjustment_number = 0
        for adjustment_track in sorted(
            (
                item
                for item in self.project.tracks
                if item.enabled and not item.muted and item.type == TrackType.ADJUSTMENT
            ),
            key=lambda item: item.order,
        ):
            for adjustment in adjustment_track.metadata.get("adjustments", []):
                if not adjustment.get("enabled", True):
                    continue
                definition = effect_registry.get(str(adjustment["type"]))
                compiled = definition.compile(adjustment.get("parameters", {}))
                if "," in compiled:
                    raise NotImplementedError(
                        "NOT_IMPLEMENTED: multi-filter effects cannot be used on an "
                        "adjustment layer yet."
                    )
                start = float(adjustment["at"])
                end = start + float(adjustment["duration"])
                output_label = f"adjusted{adjustment_number}"
                separator = ":" if "=" in compiled else "="
                filters.append(
                    f"[{current_v}]{compiled}{separator}"
                    f"enable='between(t\\,{_fmt(start)}\\,{_fmt(end)})'[{output_label}]"
                )
                current_v = output_label
                adjustment_number += 1

        # Independent audio tracks are layered after the video track's native
        # sound has been assembled.  This keeps camera/dialogue audio intact
        # while allowing music and effects to overlap freely.
        mix_labels = [current_a]
        audio_clips = [
            (audio_track, clip)
            for audio_track in self.project.tracks
            if audio_track.enabled
            and not audio_track.muted
            and audio_track.type == TrackType.AUDIO
            for clip in sorted(
                (
                    item
                    for item in audio_track.clips
                    if item.enabled and not item.muted and not item.audio.muted
                ),
                key=lambda item: (item.timeline_start, item.id),
            )
        ]
        ducking_regions = list(self.project.settings.get("audio_ducking") or [])
        for audio_index, (audio_track, clip) in enumerate(audio_clips):
            asset = self.project.find_media(clip.media_id)
            if asset is None:
                raise ValueError(
                    f"Audio clip {clip.id} references missing media {clip.media_id}."
                )
            if not asset.technical.audio_codec:
                raise ValueError(f"Media {asset.original_name} has no audio stream.")
            source = self._source(asset.path)
            if not source.is_file():
                raise FileNotFoundError(f"Media file is offline: {asset.original_name}")
            input_index = len(paths)
            paths.append(source)
            inputs.extend(["-i", str(source)])
            speed = abs(clip.speed)
            audio_chain = [
                f"[{input_index}:a:0]atrim=start={_fmt(clip.source_in)}:"
                f"end={_fmt(clip.source_out)}",
                "asetpts=PTS-STARTPTS",
            ]
            if clip.speed < 0:
                audio_chain.append("areverse")
            if abs(speed - 1) > 1e-9:
                audio_chain.append(_atempo(speed))
            audio_chain.append(f"aresample={self.project.project.sample_rate}")
            natural_duration = (clip.source_out - clip.source_in) / speed
            if clip.loop:
                loop_samples = max(
                    1, round(natural_duration * self.project.project.sample_rate)
                )
                audio_chain.append(f"aloop=loop=-1:size={loop_samples}")
            audio_chain.extend(
                [
                    f"atrim=duration={_fmt(clip.duration)}",
                    "asetpts=PTS-STARTPTS",
                ]
            )
            audio_chain.extend(
                _audio_processing_filters(
                    clip, clip.duration, self.project.project.sample_rate
                )
            )
            pre_delay_label = f"bgmpredelay{audio_index}"
            filters.append(",".join(audio_chain) + f"[{pre_delay_label}]")
            aligned_label = pre_delay_label
            if clip.timeline_start:
                # Materialize the timeline gap as real samples. Very long
                # ``adelay`` chains could lose the tail of an independent
                # track once camera audio ended and the timeline continued
                # with still images. A silence-prefix concat is deterministic
                # for music, narration and SFX at any timeline position.
                prefix_label = f"bgmprefix{audio_index}"
                aligned_label = f"bgmaligned{audio_index}"
                filters.append(
                    f"anullsrc=r={self.project.project.sample_rate}:cl=stereo,"
                    f"atrim=duration={_fmt(clip.timeline_start)}[{prefix_label}]"
                )
                filters.append(
                    f"[{prefix_label}][{pre_delay_label}]"
                    f"concat=n=2:v=0:a=1[{aligned_label}]"
                )
            post_chain: list[str] = []
            for duck in ducking_regions:
                if audio_track.id not in set(duck.get("target_tracks") or []):
                    continue
                start = max(0.0, float(duck["start"]))
                end = min(current_duration, float(duck["end"]))
                if end <= start:
                    continue
                reduction_db = min(0.0, float(duck.get("reduction_db", -12.0)))
                gain = 10 ** (reduction_db / 20.0)
                attack = max(0.001, float(duck.get("attack_ms", 100.0)) / 1000.0)
                release = max(0.001, float(duck.get("release_ms", 500.0)) / 1000.0)
                expression = (
                    f"if(lt(t\\,{_fmt(max(0.0, start - attack))})\\,1\\,"
                    f"if(lt(t\\,{_fmt(start)})\\,"
                    f"1-(1-{_fmt(gain)})*(t-{_fmt(max(0.0, start - attack))})/{_fmt(attack)}\\,"
                    f"if(lte(t\\,{_fmt(end)})\\,{_fmt(gain)}\\,"
                    f"if(lt(t\\,{_fmt(end + release)})\\,"
                    f"{_fmt(gain)}+(1-{_fmt(gain)})*(t-{_fmt(end)})/{_fmt(release)}\\,1))))"
                )
                post_chain.append(f"volume='{expression}':eval=frame")
            post_chain.extend(
                [
                    f"apad=whole_dur={_fmt(current_duration)}",
                    f"atrim=duration={_fmt(current_duration)}",
                ]
            )
            label = f"bgm{audio_index}"
            filters.append(
                f"[{aligned_label}]" + ",".join(post_chain) + f"[{label}]"
            )
            mix_labels.append(label)
        if len(mix_labels) > 1:
            filters.append(
                "".join(f"[{label}]" for label in mix_labels)
                + f"amix=inputs={len(mix_labels)}:duration=first:"
                "dropout_transition=0:normalize=0[amixed]"
            )
            current_a = "amixed"

        if master_loudness is not None:
            options = [
                f"I={_fmt(master_loudness['target_lufs'])}",
                f"TP={_fmt(master_loudness['true_peak_db'])}",
                f"LRA={_fmt(master_loudness['loudness_range'])}",
            ]
            measured_names = {
                "input_i": "measured_I",
                "input_tp": "measured_TP",
                "input_lra": "measured_LRA",
                "input_thresh": "measured_thresh",
                "target_offset": "offset",
            }
            for source_name, filter_name in measured_names.items():
                if source_name in master_loudness:
                    options.append(
                        f"{filter_name}={_fmt(master_loudness[source_name])}"
                    )
            if "input_i" in master_loudness:
                options.extend(["linear=true", "print_format=summary"])
            filters.append(
                f"[{current_a}]loudnorm={':'.join(options)}[amaster]"
            )
            current_a = "amaster"

        if subtitle_file is not None:
            filters.append(
                f"[{current_v}]subtitles=filename='{_filter_path(subtitle_file)}'[vtext]"
            )
            current_v = "vtext"

        # Timeline-anchored fades do not have a neighbouring clip and therefore
        # cannot be represented by xfade. Apply them to the completed composite
        # (including subtitles/text) so a terminal fade-out really reaches the
        # project background instead of being silently ignored.
        anchor_number = 0
        for transition in sorted(
            (
                item
                for item in self.project.transitions
                if item.from_clip_id is None
                and item.to_clip_id is None
                and item.track_id == track.id
                and item.type in {"fade-in", "fade-out"}
            ),
            key=lambda item: (item.at or 0.0, item.id),
        ):
            start = float(transition.at or 0.0)
            fade_type = "in" if transition.type == "fade-in" else "out"
            output_label = f"vanchorfade{anchor_number}"
            filters.append(
                f"[{current_v}]fade=t={fade_type}:st={_fmt(start)}:"
                f"d={_fmt(transition.duration)}:color={self.project.project.background}"
                f"[{output_label}]"
            )
            current_v = output_label
            anchor_number += 1

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
        # Filters such as xfade, overlay and subtitles may promote the graph to
        # 4:4:4/RGBA.  Delivery encoders must always receive a predictable
        # 4:2:0 stream, including when no named delivery preset was selected.
        filters.append(f"[{current_v}]format=yuv420p[vdelivery]")
        current_v = "vdelivery"
        return FilterGraph(
            input_args=tuple(inputs),
            filter_complex=";".join(filters),
            video_label=current_v,
            audio_label=current_a,
            duration=output_duration,
            source_paths=tuple(paths),
        )
