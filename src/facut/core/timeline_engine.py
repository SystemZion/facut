"""Frame-aware, non-destructive timeline editing operations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from facut.core.models import (
    AudioCompressorSettings,
    AudioLimiterSettings,
    AudioLoudnessSettings,
    AudioProcessing,
    Clip,
    Effect,
    Keyframe,
    MediaKind,
    ProjectDocument,
    Track,
    TrackType,
    Transform,
    Transition,
    new_id,
)
from facut.transitions import registry as transition_registry
from facut.effects import registry as effect_registry


_TIMECODE = re.compile(
    r"^(?P<sign>[+-]?)(?P<h>\d+):(?P<m>[0-5]\d):(?P<s>[0-5]\d(?:\.\d+)?)$"
)
_UNIT_TIME = re.compile(r"^(?P<sign>[+-]?)(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|f)?$")


class TimelineError(ValueError):
    """Base error for timeline edit failures."""


class TimelineConflictError(TimelineError):
    """Raised when clips overlap without a valid transition."""


class TimelineItemNotFound(TimelineError):
    """Raised when a requested track, clip, or media item does not exist."""


@dataclass(frozen=True, slots=True)
class TimeValue:
    """Normalized time with both timestamp and frame representation."""

    seconds: Decimal
    fps: Decimal

    @property
    def frame(self) -> int:
        return int((self.seconds * self.fps).to_integral_value(rounding=ROUND_HALF_UP))

    @property
    def timecode(self) -> str:
        total_ms = int((self.seconds * 1000).to_integral_value(rounding=ROUND_HALF_UP))
        sign = "-" if total_ms < 0 else ""
        total_ms = abs(total_ms)
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "seconds": float(self.seconds),
            "timecode": self.timecode,
            "frame": self.frame,
        }


def parse_time(value: str | int | float | Decimal, fps: float = 30.0) -> TimeValue:
    """Parse seconds, milliseconds, timecode, or frame syntax.

    Plain numeric values use seconds. Signed values are accepted so the same
    parser can be used for delta edits.
    """

    fps_decimal = Decimal(str(fps))
    if fps_decimal <= 0:
        raise ValueError("fps must be greater than zero")
    if isinstance(value, Decimal):
        seconds = value
    elif isinstance(value, (int, float)):
        seconds = Decimal(str(value))
    else:
        text = value.strip()
        timecode_match = _TIMECODE.fullmatch(text)
        if timecode_match:
            sign = -1 if timecode_match["sign"] == "-" else 1
            seconds = Decimal(timecode_match["h"]) * 3600
            seconds += Decimal(timecode_match["m"]) * 60
            seconds += Decimal(timecode_match["s"])
            seconds *= sign
        else:
            unit_match = _UNIT_TIME.fullmatch(text)
            if not unit_match:
                raise ValueError(
                    f"Invalid time {value!r}; use seconds, ms, HH:MM:SS.mmm, or frames."
                )
            try:
                amount = Decimal(unit_match["value"])
            except InvalidOperation as exc:
                raise ValueError(f"Invalid time value: {value!r}") from exc
            if unit_match["sign"] == "-":
                amount = -amount
            unit = unit_match["unit"] or "s"
            if unit == "ms":
                seconds = amount / 1000
            elif unit == "f":
                seconds = amount / fps_decimal
            else:
                seconds = amount
    return TimeValue(seconds=seconds, fps=fps_decimal)


def seconds(value: str | int | float | Decimal, fps: float = 30.0) -> float:
    """Return a parsed time as float seconds."""

    return float(parse_time(value, fps).seconds)


class TimelineEngine:
    """Mutate a :class:`ProjectDocument` using validated edit operations."""

    def __init__(self, project: ProjectDocument) -> None:
        self.project = project

    @property
    def fps(self) -> float:
        return self.project.project.fps

    def clone(self) -> "TimelineEngine":
        return TimelineEngine(deepcopy(self.project))

    def _assert_protected_boundaries(
        self,
        media_id: str,
        source_in: float,
        source_out: float,
        *,
        allow_protected_cut: bool = False,
    ) -> None:
        """Reject cuts through protected speech or atomic-event spans.

        Analysis/Agent integrations store source-relative spans in
        ``asset.metadata.protected_spans``. Exact span boundaries are legal;
        a boundary inside a span is a destructive content cut and is P0.
        """

        if allow_protected_cut:
            return
        asset = self.project.find_media(media_id)
        if asset is None:
            raise TimelineItemNotFound(f'Media "{media_id}" was not found.')
        raw_spans = asset.metadata.get("protected_spans", [])
        if not isinstance(raw_spans, list):
            raise TimelineError(
                f'Media "{media_id}" has invalid protected_spans metadata.'
            )
        epsilon = 1 / self.fps / 2
        for index, item in enumerate(raw_spans, start=1):
            if not isinstance(item, dict):
                raise TimelineError(
                    f'Media "{media_id}" protected span {index} must be an object.'
                )
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError) as error:
                raise TimelineError(
                    f'Media "{media_id}" protected span {index} requires numeric start/end.'
                ) from error
            if start < 0 or end <= start:
                raise TimelineError(
                    f'Media "{media_id}" protected span {index} has an invalid range.'
                )
            kind = str(item.get("type") or item.get("kind") or "content")
            label = str(item.get("id") or item.get("label") or index)
            for boundary_name, boundary in (("in", source_in), ("out", source_out)):
                if start + epsilon < boundary < end - epsilon:
                    raise TimelineError(
                        f'Source {boundary_name} {boundary:.3f}s cuts protected {kind} '
                        f'span "{label}" ({start:.3f}-{end:.3f}s). Use '
                        "--allow-protected-cut only after explicit review."
                    )

    def _track(self, track_id: str) -> Track:
        track = self.project.find_track(track_id)
        if track is None:
            raise TimelineItemNotFound(f'Track "{track_id}" was not found.')
        return track

    def _clip_and_track(self, clip_id: str) -> tuple[Clip, Track]:
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
        raise TimelineItemNotFound(f'Clip "{clip_id}" was not found.')

    def add_track(
        self,
        track_type: str | TrackType,
        name: str,
        track_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Track:
        parsed_type = TrackType(track_type)
        candidate = track_id or name
        if any(track.id == candidate for track in self.project.tracks):
            prefix = {
                TrackType.VIDEO: "V",
                TrackType.AUDIO: "A",
                TrackType.IMAGE: "I",
                TrackType.SUBTITLE: "S",
                TrackType.ADJUSTMENT: "ADJ",
                TrackType.MASK: "M",
            }[parsed_type]
            index = 1
            while any(track.id == f"{prefix}{index}" for track in self.project.tracks):
                index += 1
            candidate = f"{prefix}{index}"
        track = Track(
            id=candidate,
            type=parsed_type,
            name=name,
            order=len(self.project.tracks),
            metadata=dict(metadata or {}),
        )
        self.project.tracks.append(track)
        return track

    def add_clip(
        self,
        media_id: str,
        track_id: str,
        at: str | float = 0,
        source_in: str | float = 0,
        source_out: str | float | None = None,
        duration: str | float | None = None,
        clip_id: str | None = None,
        append: bool = False,
        allow_protected_cut: bool = False,
    ) -> Clip:
        track = self._track(track_id)
        if track.locked:
            raise TimelineError(f'Track "{track_id}" is locked.')
        asset = self.project.find_media(media_id)
        if asset is None:
            raise TimelineItemNotFound(f'Media "{media_id}" was not found.')
        in_seconds = seconds(source_in, self.fps)
        media_duration = asset.technical.duration
        if source_out is not None and duration is not None:
            raise TimelineError("Specify either source out or duration, not both.")
        requested_duration = seconds(duration, self.fps) if duration is not None else None
        if requested_duration is not None and requested_duration <= 0:
            raise TimelineError("Clip duration must be positive.")
        if requested_duration is not None:
            out_seconds = in_seconds + requested_duration
        elif source_out is not None:
            out_seconds = seconds(source_out, self.fps)
        elif asset.kind == MediaKind.IMAGE:
            out_seconds = in_seconds + 5.0
        else:
            out_seconds = media_duration
        if out_seconds is None:
            raise TimelineError(
                f'Media "{media_id}" has no duration; specify an explicit out point.'
            )
        timeline_warnings: list[str] = []
        if asset.kind != MediaKind.IMAGE and media_duration is not None and out_seconds > media_duration:
            excess = out_seconds - media_duration
            if excess <= 1 / self.fps + 1e-9:
                timeline_warnings.append(
                    f"Source out was clamped by {excess:.6f}s to the probed media duration."
                )
                out_seconds = media_duration
            else:
                raise TimelineError(
                    f"Source out {out_seconds:.3f}s exceeds media duration {media_duration:.3f}s."
                )
        self._assert_protected_boundaries(
            media_id,
            in_seconds,
            out_seconds,
            allow_protected_cut=allow_protected_cut,
        )
        timeline_start = (
            max((item.end for item in track.clips if item.enabled), default=0.0)
            if append
            else seconds(at, self.fps)
        )
        clip = Clip(
            id=clip_id or new_id("clip"),
            media_id=media_id,
            track_id=track_id,
            timeline_start=timeline_start,
            source_in=in_seconds,
            source_out=out_seconds,
        )
        if timeline_warnings:
            clip.metadata["timeline_warnings"] = timeline_warnings
        track.clips.append(clip)
        track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.snap_subframe_boundaries(track.id)
        self.project.recompute_duration()
        return clip

    def snap_subframe_boundaries(self, track_id: str | None = None) -> list[dict[str, Any]]:
        """Close accidental sub-frame gaps/overlaps while preserving real transitions."""

        transition_pairs = {
            (item.from_clip_id, item.to_clip_id)
            for item in self.project.transitions
            if item.from_clip_id and item.to_clip_id
        }
        tolerance = 1 / self.fps + 1e-9
        changes: list[dict[str, Any]] = []
        tracks = [self._track(track_id)] if track_id else self.project.tracks
        for track in tracks:
            if track.type == TrackType.AUDIO:
                continue
            clips = sorted(
                (item for item in track.clips if item.enabled),
                key=lambda item: item.timeline_start,
            )
            for left, right in zip(clips, clips[1:]):
                if (left.id, right.id) in transition_pairs:
                    continue
                delta = right.timeline_start - left.end
                if 1e-9 < abs(delta) <= tolerance:
                    old_start = right.timeline_start
                    right.timeline_start = left.end
                    warning = (
                        f"Sub-frame {'gap' if delta > 0 else 'overlap'} of "
                        f"{abs(delta):.6f}s was snapped to clip {left.id}."
                    )
                    right.metadata.setdefault("timeline_warnings", []).append(warning)
                    changes.append(
                        {
                            "clip_id": right.id,
                            "old_start": old_start,
                            "new_start": right.timeline_start,
                            "delta": delta,
                        }
                    )
        if changes:
            self.project.recompute_duration()
        return changes

    def add_audio_clip(
        self,
        media_id: str,
        track_id: str,
        at: str | float = 0,
        source_in: str | float = 0,
        source_out: str | float | None = None,
        *,
        volume_db: float = 0.0,
        muted: bool = False,
        fade_in: str | float = 0,
        fade_out: str | float = 0,
        highpass_hz: float | None = None,
        denoise_strength: float | None = None,
        compressor: bool = False,
        limiter_db: float | None = None,
        loudnorm_lufs: float | None = None,
        channel_mode: str = "original",
        pan: float | None = None,
        loop: bool = False,
        duration: str | float | None = None,
        clip_id: str | None = None,
    ) -> Clip:
        """Place an audio source on an audio track.

        When ``loop`` is enabled and no duration is supplied, the source is
        repeated until the end of the current video timeline.
        """

        track = self._track(track_id)
        if track.type != TrackType.AUDIO:
            raise TimelineError(f'Track "{track_id}" is not an audio track.')
        asset = self.project.find_media(media_id)
        if asset is None:
            raise TimelineItemNotFound(f'Media "{media_id}" was not found.')
        if asset.kind not in {MediaKind.AUDIO, MediaKind.VIDEO}:
            raise TimelineError(f'Media "{media_id}" does not contain usable audio.')
        if not asset.technical.audio_codec:
            raise TimelineError(f'Media "{media_id}" does not contain an audio stream.')
        if not -96.0 <= float(volume_db) <= 24.0:
            raise TimelineError("Audio volume must be between -96 dB and +24 dB.")

        clip = self.add_clip(
            media_id,
            track_id,
            at=at,
            source_in=source_in,
            source_out=source_out,
            clip_id=clip_id,
        )
        fade_in_seconds = seconds(fade_in, self.fps)
        fade_out_seconds = seconds(fade_out, self.fps)
        if fade_in_seconds < 0 or fade_out_seconds < 0:
            raise TimelineError("Audio fade durations cannot be negative.")
        clip.loop = loop
        if duration is not None:
            if not loop:
                raise TimelineError("An explicit loop duration requires loop=true.")
            clip.timeline_duration = seconds(duration, self.fps)
        elif loop:
            video_end = max(
                (
                    item.end
                    for candidate in self.project.tracks
                    if candidate.type in {TrackType.VIDEO, TrackType.IMAGE}
                    for item in candidate.clips
                    if item.enabled
                ),
                default=clip.timeline_start + clip.duration,
            )
            clip.timeline_duration = max(
                1 / self.fps, video_end - clip.timeline_start
            )
        clip.audio = AudioProcessing(
            gain_db=float(volume_db),
            muted=muted,
            fade_in=fade_in_seconds,
            fade_out=fade_out_seconds,
            highpass_hz=highpass_hz,
            denoise_strength=denoise_strength,
            compressor=AudioCompressorSettings() if compressor else None,
            limiter=AudioLimiterSettings(ceiling_db=limiter_db)
            if limiter_db is not None
            else None,
            loudness=AudioLoudnessSettings(target_lufs=loudnorm_lufs)
            if loudnorm_lufs is not None
            else None,
            channel_mode=channel_mode,
            pan=pan,
        )
        Clip.model_validate(clip.model_dump())
        self.project.recompute_duration()
        return clip

    def set_audio_volume(self, clip_id: str, db: float) -> Clip:
        """Set clip gain in decibels for native or independent audio."""

        clip = self._audio_clip(clip_id)
        if not -96.0 <= float(db) <= 24.0:
            raise TimelineError("Audio volume must be between -96 dB and +24 dB.")
        clip.audio = AudioProcessing.model_validate(
            {**clip.audio.model_dump(), "gain_db": float(db)}
        )
        return clip

    def set_audio_mute(self, clip_id: str, muted: bool = True) -> Clip:
        """Mute or unmute native or independent clip audio."""

        clip = self._audio_clip(clip_id)
        clip.audio = AudioProcessing.model_validate(
            {**clip.audio.model_dump(), "muted": muted}
        )
        return clip

    def set_audio_fades(
        self,
        clip_id: str,
        *,
        fade_in: str | float | None = None,
        fade_out: str | float | None = None,
    ) -> Clip:
        """Set non-destructive fade-in and fade-out durations."""

        clip = self._audio_clip(clip_id)
        if fade_in is None and fade_out is None:
            raise TimelineError("Specify fade_in, fade_out, or both.")
        values = clip.audio.model_dump()
        if fade_in is not None:
            values["fade_in"] = seconds(fade_in, self.fps)
        if fade_out is not None:
            values["fade_out"] = seconds(fade_out, self.fps)
        clip.audio = AudioProcessing.model_validate(values)
        Clip.model_validate(clip.model_dump())
        return clip

    def configure_audio(
        self,
        clip_id: str,
        *,
        highpass_hz: float | None = None,
        denoise_strength: float | None = None,
        compressor: bool | None = None,
        compressor_preset: str | None = None,
        compressor_threshold_db: float | None = None,
        compressor_ratio: float | None = None,
        compressor_attack_ms: float | None = None,
        compressor_release_ms: float | None = None,
        compressor_makeup_db: float | None = None,
        limiter_db: float | None = None,
        loudnorm_lufs: float | None = None,
        true_peak_db: float | None = None,
        loudness_range: float | None = None,
        channel_mode: str | None = None,
        pan: float | None = None,
        clear_pan: bool = False,
    ) -> Clip:
        """Update non-destructive cleanup, dynamics, and channel settings."""

        clip = self._audio_clip(clip_id)
        values = clip.audio.model_dump()
        if highpass_hz is not None:
            values["highpass_hz"] = None if highpass_hz == 0 else highpass_hz
        if denoise_strength is not None:
            values["denoise_strength"] = (
                None if denoise_strength == 0 else denoise_strength
            )
        if compressor_preset is not None:
            presets = {
                "vlog": {
                    "threshold_db": -18.0,
                    "ratio": 4.0,
                    "attack_ms": 20.0,
                    "release_ms": 250.0,
                    "makeup_db": 2.0,
                },
                "dialogue": {
                    "threshold_db": -20.0,
                    "ratio": 3.0,
                    "attack_ms": 15.0,
                    "release_ms": 180.0,
                    "makeup_db": 2.0,
                },
                "gentle": {
                    "threshold_db": -16.0,
                    "ratio": 2.0,
                    "attack_ms": 30.0,
                    "release_ms": 300.0,
                    "makeup_db": 1.0,
                },
            }
            if compressor_preset.casefold() not in presets:
                raise TimelineError("Compressor preset must be vlog, dialogue, or gentle.")
            values["compressor"] = presets[compressor_preset.casefold()]
        if compressor is False:
            values["compressor"] = None
        elif compressor or any(
            item is not None
            for item in (
                compressor_threshold_db,
                compressor_ratio,
                compressor_attack_ms,
                compressor_release_ms,
                compressor_makeup_db,
            )
        ):
            compressor_values = values.get("compressor") or {}
            if compressor_threshold_db is not None:
                compressor_values["threshold_db"] = compressor_threshold_db
            if compressor_ratio is not None:
                compressor_values["ratio"] = compressor_ratio
            if compressor_attack_ms is not None:
                compressor_values["attack_ms"] = compressor_attack_ms
            if compressor_release_ms is not None:
                compressor_values["release_ms"] = compressor_release_ms
            if compressor_makeup_db is not None:
                compressor_values["makeup_db"] = compressor_makeup_db
            values["compressor"] = compressor_values
        if limiter_db is not None:
            values["limiter"] = (
                None if limiter_db == 0 else {"ceiling_db": limiter_db}
            )
        if loudnorm_lufs is not None:
            if loudnorm_lufs == 0:
                values["loudness"] = None
            else:
                loudness_values = values.get("loudness") or {}
                loudness_values["target_lufs"] = loudnorm_lufs
                if true_peak_db is not None:
                    loudness_values["true_peak_db"] = true_peak_db
                if loudness_range is not None:
                    loudness_values["loudness_range"] = loudness_range
                values["loudness"] = loudness_values
        if channel_mode is not None:
            if channel_mode not in {"original", "mono", "stereo"}:
                raise TimelineError(
                    "NOT_IMPLEMENTED: channel mode must be original, mono, or stereo."
                )
            values["channel_mode"] = channel_mode
        if clear_pan:
            values["pan"] = None
        elif pan is not None:
            values["pan"] = pan
        clip.audio = AudioProcessing.model_validate(values)
        return clip

    def set_master_loudness(
        self,
        *,
        target: float,
        true_peak: float = -1.0,
        loudness_range: float = 11.0,
        two_pass: bool = True,
    ) -> dict[str, Any]:
        """Save deterministic master loudness defaults for subsequent renders."""

        settings = AudioLoudnessSettings(
            target_lufs=target,
            true_peak_db=true_peak,
            loudness_range=loudness_range,
        )
        payload = settings.model_dump(mode="json")
        payload["two_pass"] = bool(two_pass)
        self.project.settings.setdefault("audio", {})["master_loudness"] = payload
        return payload

    def crossfade_audio(
        self, from_clip_id: str, to_clip_id: str, duration: str | float
    ) -> tuple[Clip, Clip]:
        """Create a basic equal-gain crossfade between overlapping audio clips."""

        source, source_track = self._clip_and_track(from_clip_id)
        target, target_track = self._clip_and_track(to_clip_id)
        if source_track.type != TrackType.AUDIO or target_track.type != TrackType.AUDIO:
            raise TimelineError(
                "NOT_IMPLEMENTED: audio crossfade currently supports independent "
                "audio-track clips; use a video transition for native clip audio."
            )
        self._audio_clip(from_clip_id)
        self._audio_clip(to_clip_id)
        fade_duration = seconds(duration, self.fps)
        overlap = min(source.end, target.end) - max(
            source.timeline_start, target.timeline_start
        )
        if fade_duration <= 0 or overlap + 1e-9 < fade_duration:
            raise TimelineError(
                "Audio crossfade duration must fit inside the clips' timeline overlap."
            )
        source_values = source.audio.model_dump()
        target_values = target.audio.model_dump()
        source_values["fade_out"] = fade_duration
        target_values["fade_in"] = fade_duration
        source.audio = AudioProcessing.model_validate(source_values)
        target.audio = AudioProcessing.model_validate(target_values)
        return source, target

    def _audio_clip(self, clip_id: str) -> Clip:
        clip, _ = self._clip_and_track(clip_id)
        asset = self.project.find_media(clip.media_id)
        if asset is None or not asset.technical.audio_codec:
            raise TimelineError(f'Clip "{clip_id}" has no audio stream.')
        return clip

    def move_clip(
        self,
        clip_id: str,
        *,
        to: str | float | None = None,
        delta: str | float | None = None,
        track_id: str | None = None,
    ) -> Clip:
        if (to is None) == (delta is None):
            raise TimelineError("Specify exactly one of to or delta.")
        clip, old_track = self._clip_and_track(clip_id)
        destination = self._track(track_id) if track_id else old_track
        if old_track.locked or destination.locked:
            raise TimelineError("Cannot move a clip on a locked track.")
        new_start = (
            seconds(to, self.fps)
            if to is not None
            else clip.timeline_start + seconds(delta, self.fps)
        )
        if new_start < 0:
            raise TimelineError("A clip cannot start before time zero.")
        clip.timeline_start = new_start
        if destination.id != old_track.id:
            old_track.clips.remove(clip)
            clip.track_id = destination.id
            destination.clips.append(clip)
        old_track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        destination.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.snap_subframe_boundaries(old_track.id)
        if destination.id != old_track.id:
            self.snap_subframe_boundaries(destination.id)
        self.project.recompute_duration()
        return clip

    def duplicate_clip(
        self, clip_id: str, to: str | float, track_id: str | None = None
    ) -> Clip:
        original, old_track = self._clip_and_track(clip_id)
        destination = self._track(track_id) if track_id else old_track
        clone = original.model_copy(deep=True)
        clone.id = new_id("clip")
        clone.track_id = destination.id
        clone.timeline_start = seconds(to, self.fps)
        destination.clips.append(clone)
        destination.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.project.recompute_duration()
        return clone

    def transform_clip(
        self,
        clip_id: str,
        *,
        x: float | None = None,
        y: float | None = None,
        scale: float | None = None,
        scale_x: float | None = None,
        scale_y: float | None = None,
        rotation: float | None = None,
        opacity: float | None = None,
        crop_left: float | None = None,
        crop_top: float | None = None,
        crop_right: float | None = None,
        crop_bottom: float | None = None,
        crop: str | None = None,
        fit: str | None = None,
        flip_x: bool | None = None,
        flip_y: bool | None = None,
        autorotate: bool | None = None,
        stabilize: bool | None = None,
        keyframes: list[dict[str, Any]] | None = None,
    ) -> Clip:
        """Apply static or linear-keyframed non-destructive picture transforms."""

        clip, track = self._clip_and_track(clip_id)
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise TimelineError("Picture transforms require a video or image clip.")
        values = clip.transform.model_dump()
        if crop is not None:
            asset = self.project.find_media(clip.media_id)
            if asset is None or not asset.technical.width or not asset.technical.height:
                raise TimelineError("Crop shorthand requires known source dimensions.")
            try:
                left, top, crop_width, crop_height = (
                    float(item.strip()) for item in crop.split(":")
                )
            except (TypeError, ValueError) as error:
                raise TimelineError("Crop must use left:top:width:height.") from error
            if min(left, top, crop_width, crop_height) < 0 or crop_width <= 0 or crop_height <= 0:
                raise TimelineError("Crop values must describe a positive source rectangle.")
            right = asset.technical.width - left - crop_width
            bottom = asset.technical.height - top - crop_height
            if right < 0 or bottom < 0:
                raise TimelineError("Crop rectangle exceeds source dimensions.")
            crop_left, crop_top, crop_right, crop_bottom = left, top, right, bottom
        supplied = {
            "x": x,
            "y": y,
            "scale_x": scale_x if scale_x is not None else scale,
            "scale_y": scale_y if scale_y is not None else scale,
            "rotation": rotation,
            "opacity": opacity,
            "crop_left": crop_left,
            "crop_top": crop_top,
            "crop_right": crop_right,
            "crop_bottom": crop_bottom,
            "fit": fit,
            "flip_x": flip_x,
            "flip_y": flip_y,
            "autorotate": autorotate,
            "stabilize": stabilize,
        }
        values.update({name: value for name, value in supplied.items() if value is not None})
        clip.transform = Transform.model_validate(values)
        if keyframes is not None:
            allowed = {"x", "y", "scale_x", "scale_y", "rotation"}
            parsed = [Keyframe.model_validate(item) for item in keyframes]
            for keyframe in parsed:
                if keyframe.property not in allowed:
                    raise TimelineError(
                        "NOT_IMPLEMENTED: transform keyframes currently support "
                        "x, y, scale_x, scale_y, and rotation."
                    )
                if keyframe.time > clip.duration:
                    raise TimelineError("Transform keyframe exceeds clip duration.")
                if keyframe.easing not in {
                    "linear",
                    "ease-in",
                    "ease-out",
                    "ease-in-out",
                    "cubic",
                }:
                    raise TimelineError(f'Unsupported keyframe easing "{keyframe.easing}".')
                if not isinstance(keyframe.value, (int, float)):
                    raise TimelineError("Transform keyframe values must be numeric.")
            clip.keyframes = parsed
        return clip

    def apply_motion_preset(
        self,
        clip_id: str,
        *,
        preset: str,
        intensity: float = 0.35,
        easing: str = "ease-in-out",
    ) -> Clip:
        """Apply a conservative VLOG-friendly digital camera move."""

        clip, track = self._clip_and_track(clip_id)
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise TimelineError("Motion presets require a video or image clip.")
        if not 0.0 <= float(intensity) <= 1.0:
            raise TimelineError("Motion intensity must be between 0 and 1.")
        normalized = preset.casefold().strip()
        duration = clip.duration
        zoom = 0.15 * float(intensity)
        travel_x = self.project.project.width * 0.06 * float(intensity)
        travel_y = self.project.project.height * 0.06 * float(intensity)
        definitions: dict[str, dict[str, tuple[float, float]]] = {
            "slow-push": {"scale_x": (1.0, 1.0 + zoom), "scale_y": (1.0, 1.0 + zoom)},
            "slow-pull": {"scale_x": (1.0 + zoom, 1.0), "scale_y": (1.0 + zoom, 1.0)},
            "pan-left": {"x": (travel_x, -travel_x), "scale_x": (1.0 + zoom, 1.0 + zoom), "scale_y": (1.0 + zoom, 1.0 + zoom)},
            "pan-right": {"x": (-travel_x, travel_x), "scale_x": (1.0 + zoom, 1.0 + zoom), "scale_y": (1.0 + zoom, 1.0 + zoom)},
            "tilt-up": {"y": (travel_y, -travel_y), "scale_x": (1.0 + zoom, 1.0 + zoom), "scale_y": (1.0 + zoom, 1.0 + zoom)},
            "tilt-down": {"y": (-travel_y, travel_y), "scale_x": (1.0 + zoom, 1.0 + zoom), "scale_y": (1.0 + zoom, 1.0 + zoom)},
        }
        definition = definitions.get(normalized)
        if definition is None:
            raise TimelineError(
                "Motion preset must be slow-push, slow-pull, pan-left, pan-right, tilt-up, or tilt-down."
            )
        properties = set(definition)
        clip.keyframes = [item for item in clip.keyframes if item.property not in properties]
        for property_name, (start, end) in definition.items():
            clip.keyframes.extend(
                [
                    Keyframe(property=property_name, time=0.0, value=start, easing=easing),
                    Keyframe(property=property_name, time=duration, value=end, easing=easing),
                ]
            )
        clip.metadata["motion_preset"] = {
            "name": normalized,
            "intensity": round(float(intensity), 4),
            "easing": easing,
        }
        return clip

    def freeze_clip(
        self,
        clip_id: str,
        *,
        at: str | float,
        duration: str | float,
        to: str | float | None = None,
        ripple: bool = True,
    ) -> Clip:
        """Insert a frame hold, defaulting to the end of the source clip."""

        original, track = self._clip_and_track(clip_id)
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise TimelineError("Freeze frame requires a video or image clip.")
        relative = seconds(at, self.fps)
        hold_duration = seconds(duration, self.fps)
        if relative < 0 or relative > original.duration:
            raise TimelineError("Freeze position must fall within the clip duration.")
        if hold_duration <= 0:
            raise TimelineError("Freeze duration must be positive.")
        source_frame = min(
            original.source_out - 1 / self.fps,
            original.source_in + relative * abs(original.speed),
        )
        insertion = original.end if to is None else seconds(to, self.fps)
        if ripple:
            for following in track.clips:
                if following.id != original.id and following.timeline_start >= insertion - 1e-9:
                    following.timeline_start += hold_duration
        hold = original.model_copy(deep=True)
        hold.id = new_id("clip")
        hold.timeline_start = insertion
        hold.source_in = source_frame
        hold.source_out = source_frame + 1 / self.fps
        hold.speed = 1.0
        hold.loop = False
        hold.freeze_frame = source_frame
        hold.timeline_duration = hold_duration
        hold.audio = AudioProcessing(muted=True)
        hold.muted = True
        track.clips.append(hold)
        track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.project.recompute_duration()
        return hold

    def add_effect(
        self, clip_id: str, effect_type: str, parameters: dict[str, Any] | None = None
    ) -> Effect:
        """Attach one validated non-destructive video effect to a clip."""

        clip, track = self._clip_and_track(clip_id)
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise TimelineError("Video effects require a video or image clip.")
        definition = effect_registry.get(effect_type)
        definition.compile(parameters or {})
        effect = Effect(type=definition.name, parameters=parameters or {})
        clip.effects.append(effect)
        return effect

    def remove_effect(self, effect_id: str) -> Effect:
        for track in self.project.tracks:
            for clip in track.clips:
                for effect in clip.effects:
                    if effect.id == effect_id:
                        clip.effects.remove(effect)
                        return effect
        raise TimelineItemNotFound(f'Effect "{effect_id}" was not found.')

    def configure_composite(
        self,
        clip_id: str,
        *,
        blend_mode: str = "normal",
        mask_path: str | None = None,
    ) -> Clip:
        """Configure overlay blending and an optional grayscale mask."""

        clip, track = self._clip_and_track(clip_id)
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise TimelineError("Composite settings require a video or image clip.")
        allowed = {"normal", "screen", "multiply", "overlay", "addition", "difference"}
        if blend_mode not in allowed:
            raise TimelineError(
                f"Blend mode must be one of: {', '.join(sorted(allowed))}."
            )
        clip.metadata["blend_mode"] = blend_mode
        if mask_path is not None:
            clip.metadata["mask_path"] = mask_path
        return clip

    def add_adjustment(
        self,
        track_id: str,
        *,
        effect_type: str,
        at: str | float,
        duration: str | float,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a timed effect entry to an adjustment track."""

        track = self._track(track_id)
        if track.type != TrackType.ADJUSTMENT:
            raise TimelineError(f'Track "{track_id}" is not an adjustment track.')
        definition = effect_registry.get(effect_type)
        definition.compile(parameters or {})
        start = seconds(at, self.fps)
        length = seconds(duration, self.fps)
        if length <= 0:
            raise TimelineError("Adjustment duration must be positive.")
        entry = {
            "id": new_id("adjustment"),
            "type": definition.name,
            "at": start,
            "duration": length,
            "parameters": parameters or {},
            "enabled": True,
        }
        track.metadata.setdefault("adjustments", []).append(entry)
        return entry

    def split_clip(
        self,
        clip_id: str,
        at: str | float,
        *,
        timeline_position: bool = False,
        allow_protected_cut: bool = False,
    ) -> tuple[Clip, Clip]:
        clip, track = self._clip_and_track(clip_id)
        split_at = seconds(at, self.fps)
        relative = split_at - clip.timeline_start if timeline_position else split_at
        if relative <= 0 or relative >= clip.duration:
            raise TimelineError("Split point must be strictly inside the clip.")
        consumed_source = relative * abs(clip.speed)
        source_split = clip.source_in + consumed_source
        self._assert_protected_boundaries(
            clip.media_id,
            source_split,
            source_split,
            allow_protected_cut=allow_protected_cut,
        )
        right = clip.model_copy(deep=True)
        right.id = new_id("clip")
        right.timeline_start = clip.timeline_start + relative
        right.source_in = source_split
        clip.source_out = source_split
        track.clips.append(right)
        track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self._remove_transitions_for(clip_id)
        self.project.recompute_duration()
        return clip, right

    def trim_clip(
        self,
        clip_id: str,
        *,
        start_delta: str | float | None = None,
        end_delta: str | float | None = None,
        source_in: str | float | None = None,
        source_out: str | float | None = None,
        allow_protected_cut: bool = False,
    ) -> Clip:
        clip, _ = self._clip_and_track(clip_id)
        candidate = clip.model_copy(deep=True)
        if source_in is not None:
            new_in = seconds(source_in, self.fps)
            timeline_shift = (new_in - candidate.source_in) / abs(candidate.speed)
            candidate.source_in = new_in
            candidate.timeline_start += timeline_shift
        if source_out is not None:
            candidate.source_out = seconds(source_out, self.fps)
        if start_delta is not None:
            delta_seconds = seconds(start_delta, self.fps)
            candidate.source_in += delta_seconds * abs(candidate.speed)
            candidate.timeline_start += delta_seconds
        if end_delta is not None:
            candidate.source_out += seconds(end_delta, self.fps) * abs(candidate.speed)
        if (
            candidate.timeline_start < 0
            or candidate.source_in < 0
            or candidate.source_out <= candidate.source_in
        ):
            raise TimelineError("Trim would produce an invalid clip range.")
        # Assignment validators do not run a model-level range check after two
        # separate field assignments, so explicitly validate the final model.
        Clip.model_validate(candidate.model_dump())
        self._assert_protected_boundaries(
            candidate.media_id,
            candidate.source_in,
            candidate.source_out,
            allow_protected_cut=allow_protected_cut,
        )
        clip.source_in = candidate.source_in
        clip.source_out = candidate.source_out
        clip.timeline_start = candidate.timeline_start
        self._remove_transitions_for(clip_id)
        self.project.recompute_duration()
        return clip

    def delete_clip(self, clip_id: str, *, ripple: bool = False) -> Clip:
        clip, track = self._clip_and_track(clip_id)
        if track.locked:
            raise TimelineError(f'Track "{track.id}" is locked.')
        old_end = clip.end
        removed_duration = clip.duration
        track.clips.remove(clip)
        self._remove_transitions_for(clip_id)
        if ripple:
            for following in track.clips:
                if following.timeline_start >= old_end - 1e-9:
                    following.timeline_start -= removed_duration
            track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.project.recompute_duration()
        return clip

    def set_speed(
        self,
        clip_id: str,
        *,
        rate: float | None = None,
        duration: str | float | None = None,
    ) -> Clip:
        clip, _ = self._clip_and_track(clip_id)
        if (rate is None) == (duration is None):
            raise TimelineError("Specify exactly one of rate or duration.")
        if duration is not None:
            desired = seconds(duration, self.fps)
            if desired <= 0:
                raise TimelineError("Duration must be greater than zero.")
            clip.speed = (clip.source_out - clip.source_in) / desired
        else:
            assert rate is not None
            if rate == 0:
                raise TimelineError("Speed rate cannot be zero.")
            clip.speed = float(rate)
        self._remove_transitions_for(clip_id)
        self.project.recompute_duration()
        return clip

    def apply_speed_curve(
        self,
        clip_id: str,
        *,
        curve: dict[str, Any],
    ) -> list[Clip]:
        """Apply an auditable piecewise speed curve by segmenting the source clip.

        Curve ``at`` values are seconds relative to the clip source range. ``step``
        holds each point's rate until the next point. ``linear`` samples the rate
        ramp into deterministic constant-rate segments, which keeps preview,
        final render, cache keys, history and exchange files in agreement.
        """

        clip, track = self._clip_and_track(clip_id)
        mode = str(curve.get("mode", "linear")).strip().casefold()
        if mode not in {"step", "linear"}:
            raise TimelineError('Speed curve mode must be "step" or "linear".')
        raw_points = curve.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise TimelineError('Speed curve requires a non-empty "points" list.')
        source_duration = clip.source_out - clip.source_in
        parsed: list[tuple[float, float]] = []
        for index, item in enumerate(raw_points, start=1):
            if not isinstance(item, dict):
                raise TimelineError(f"Speed curve point {index} must be an object.")
            try:
                at = seconds(item.get("at", 0), self.fps)
                rate = float(item["rate"])
            except (KeyError, TypeError, ValueError) as error:
                raise TimelineError(
                    f'Speed curve point {index} requires numeric "at" and "rate".'
                ) from error
            if not 0 <= at <= source_duration + 1e-9:
                raise TimelineError(
                    f"Speed curve point {index} is outside the clip source range."
                )
            if rate <= 0:
                raise TimelineError(
                    "Speed curve rates must be positive; use clip speed --reverse "
                    "for deterministic full-clip reverse playback."
                )
            parsed.append((min(at, source_duration), rate))
        parsed.sort(key=lambda item: item[0])
        if any(abs(left[0] - right[0]) < 1e-9 for left, right in zip(parsed, parsed[1:])):
            raise TimelineError("Speed curve point times must be unique.")
        if parsed[0][0] > 1e-9:
            parsed.insert(0, (0.0, parsed[0][1]))
        elif parsed[0][0] < 1e-9:
            parsed[0] = (0.0, parsed[0][1])
        if parsed[-1][0] < source_duration - 1e-9:
            parsed.append((source_duration, parsed[-1][1]))
        elif parsed[-1][0] > source_duration - 1e-9:
            parsed[-1] = (source_duration, parsed[-1][1])
        if len(parsed) < 2:
            parsed.append((source_duration, parsed[0][1]))

        steps = curve.get("steps", 8)
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 64:
            raise TimelineError("Speed curve steps must be an integer from 1 to 64.")
        intervals: list[tuple[float, float, float]] = []
        for left, right in zip(parsed, parsed[1:]):
            start, start_rate = left
            end, end_rate = right
            if end - start <= 1e-9:
                continue
            subdivisions = 1 if mode == "step" else steps
            width = (end - start) / subdivisions
            for subdivision in range(subdivisions):
                source_start = start + subdivision * width
                source_end = end if subdivision == subdivisions - 1 else source_start + width
                if mode == "step":
                    rate = start_rate
                else:
                    midpoint = (subdivision + 0.5) / subdivisions
                    rate = start_rate + (end_rate - start_rate) * midpoint
                intervals.append((source_start, source_end, rate))
        if not intervals:
            raise TimelineError("Speed curve produced no playable intervals.")

        original = clip.model_copy(deep=True)
        original_timeline_duration = original.duration
        output_cursor = original.timeline_start
        segments: list[Clip] = []
        for index, (relative_start, relative_end, rate) in enumerate(intervals):
            segment = original.model_copy(deep=True)
            segment.id = original.id if index == 0 else new_id("clip")
            segment.timeline_start = output_cursor
            segment.source_in = original.source_in + relative_start
            segment.source_out = original.source_in + relative_end
            segment.speed = rate
            segment.timeline_duration = None
            segment.freeze_frame = None
            segment.loop = False
            segment.audio.fade_in = original.audio.fade_in if index == 0 else 0.0
            segment.audio.fade_out = original.audio.fade_out if index == len(intervals) - 1 else 0.0
            segment.audio_fade_in = original.audio_fade_in if index == 0 else 0.0
            segment.audio_fade_out = original.audio_fade_out if index == len(intervals) - 1 else 0.0
            segment.metadata = deepcopy(original.metadata)
            segment.metadata["speed_curve"] = {
                "version": str(curve.get("version", "1.0")),
                "mode": mode,
                "segment": index + 1,
                "segments": len(intervals),
                "source_at": relative_start,
                "rate": rate,
            }
            # Existing keyframes are timeline-relative. Preserve those whose
            # normalized source position falls inside this generated segment.
            remapped: list[Keyframe] = []
            for keyframe in original.keyframes:
                source_at = (
                    keyframe.time / original_timeline_duration * source_duration
                    if original_timeline_duration > 0
                    else 0.0
                )
                if relative_start - 1e-9 <= source_at <= relative_end + 1e-9:
                    copied = keyframe.model_copy(deep=True)
                    copied.time = max(0.0, (source_at - relative_start) / rate)
                    remapped.append(copied)
            segment.keyframes = remapped
            segments.append(segment)
            output_cursor += segment.duration

        track.clips.remove(clip)
        track.clips.extend(segments)
        track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self._remove_transitions_for(clip_id)
        self.project.recompute_duration()
        return segments

    def add_transition(
        self,
        transition_type: str,
        duration: str | float | None = None,
        *,
        from_clip_id: str | None = None,
        to_clip_id: str | None = None,
        track_id: str | None = None,
        at: str | float | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> Transition:
        definition = transition_registry.get(transition_type)
        duration_seconds = (
            None if duration is None else seconds(duration, self.fps)
        )
        normalized_duration, normalized_parameters = definition.validate(
            duration_seconds, parameters
        )
        if from_clip_id and to_clip_id:
            source, source_track = self._clip_and_track(from_clip_id)
            target, target_track = self._clip_and_track(to_clip_id)
            if source_track.id != target_track.id:
                raise TimelineError("A basic transition requires clips on the same track.")
            overlap = source.end - target.timeline_start
            if abs(overlap - normalized_duration) > 1 / self.fps + 1e-6:
                raise TimelineConflictError(
                    "Transition duration must match the overlap between its clips "
                    f"({max(0.0, overlap):.3f}s)."
                )
            track_id = source_track.id
            at = target.timeline_start
        elif track_id is not None and at is not None:
            self._track(track_id)
            at = seconds(at, self.fps)
        else:
            raise TimelineError("Specify a from/to clip pair or track and at position.")
        transition = Transition(
            type=definition.name,
            duration=normalized_duration,
            from_clip_id=from_clip_id,
            to_clip_id=to_clip_id,
            track_id=track_id,
            at=at,
            parameters=normalized_parameters,
        )
        self.project.transitions.append(transition)
        return transition

    def remove_transition(self, transition_id: str) -> Transition:
        for transition in self.project.transitions:
            if transition.id == transition_id:
                self.project.transitions.remove(transition)
                return transition
        raise TimelineItemNotFound(f'Transition "{transition_id}" was not found.')

    def _remove_transitions_for(self, clip_id: str) -> None:
        self.project.transitions[:] = [
            transition
            for transition in self.project.transitions
            if clip_id not in (transition.from_clip_id, transition.to_clip_id)
        ]

    def conflicts(self, track_id: str | None = None) -> list[dict[str, Any]]:
        """Return unsupported overlaps; transition-backed overlaps are valid."""

        transition_pairs = {
            (item.from_clip_id, item.to_clip_id): item
            for item in self.project.transitions
            if item.from_clip_id and item.to_clip_id
        }
        result: list[dict[str, Any]] = []
        tracks = (
            [self._track(track_id)]
            if track_id is not None
            else [track for track in self.project.tracks if track.enabled]
        )
        for track in tracks:
            if track.type == TrackType.AUDIO:
                # Independent sound layers are mixed, so overlaps are valid.
                continue
            clips = sorted(
                (clip for clip in track.clips if clip.enabled),
                key=lambda item: item.timeline_start,
            )
            for left, right in zip(clips, clips[1:]):
                overlap = left.end - right.timeline_start
                if overlap > 1e-9:
                    transition = transition_pairs.get((left.id, right.id))
                    if transition and abs(transition.duration - overlap) <= 1 / self.fps:
                        continue
                    result.append(
                        {
                            "track_id": track.id,
                            "left_clip_id": left.id,
                            "right_clip_id": right.id,
                            "overlap": overlap,
                        }
                    )
        return result

    def validate(self) -> None:
        self.snap_subframe_boundaries()
        conflicts = self.conflicts()
        if conflicts:
            first = conflicts[0]
            raise TimelineConflictError(
                f"Clips {first['left_clip_id']} and {first['right_clip_id']} overlap "
                f"by {first['overlap']:.3f}s without a matching transition."
            )
        ProjectDocument.model_validate(self.project.model_dump())
