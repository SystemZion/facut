"""Frame-aware, non-destructive timeline editing operations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from facut.core.models import Clip, ProjectDocument, Track, TrackType, Transition, new_id
from facut.transitions import registry as transition_registry


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
        self, track_type: str | TrackType, name: str, track_id: str | None = None
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
        clip_id: str | None = None,
    ) -> Clip:
        track = self._track(track_id)
        if track.locked:
            raise TimelineError(f'Track "{track_id}" is locked.')
        asset = self.project.find_media(media_id)
        if asset is None:
            raise TimelineItemNotFound(f'Media "{media_id}" was not found.')
        in_seconds = seconds(source_in, self.fps)
        media_duration = asset.technical.duration
        out_seconds = (
            seconds(source_out, self.fps) if source_out is not None else media_duration
        )
        if out_seconds is None:
            raise TimelineError(
                f'Media "{media_id}" has no duration; specify an explicit out point.'
            )
        if media_duration is not None and out_seconds > media_duration + 1e-6:
            raise TimelineError(
                f"Source out {out_seconds:.3f}s exceeds media duration {media_duration:.3f}s."
            )
        clip = Clip(
            id=clip_id or new_id("clip"),
            media_id=media_id,
            track_id=track_id,
            timeline_start=seconds(at, self.fps),
            source_in=in_seconds,
            source_out=out_seconds,
        )
        track.clips.append(clip)
        track.clips.sort(key=lambda item: (item.timeline_start, item.id))
        self.project.recompute_duration()
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

    def split_clip(
        self, clip_id: str, at: str | float, *, timeline_position: bool = False
    ) -> tuple[Clip, Clip]:
        clip, track = self._clip_and_track(clip_id)
        split_at = seconds(at, self.fps)
        relative = split_at - clip.timeline_start if timeline_position else split_at
        if relative <= 0 or relative >= clip.duration:
            raise TimelineError("Split point must be strictly inside the clip.")
        consumed_source = relative * abs(clip.speed)
        source_split = clip.source_in + consumed_source
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
    ) -> Clip:
        clip, _ = self._clip_and_track(clip_id)
        if source_in is not None:
            new_in = seconds(source_in, self.fps)
            timeline_shift = (new_in - clip.source_in) / abs(clip.speed)
            clip.source_in = new_in
            clip.timeline_start += timeline_shift
        if source_out is not None:
            clip.source_out = seconds(source_out, self.fps)
        if start_delta is not None:
            delta_seconds = seconds(start_delta, self.fps)
            clip.source_in += delta_seconds * abs(clip.speed)
            clip.timeline_start += delta_seconds
        if end_delta is not None:
            clip.source_out += seconds(end_delta, self.fps) * abs(clip.speed)
        if clip.timeline_start < 0 or clip.source_in < 0 or clip.source_out <= clip.source_in:
            raise TimelineError("Trim would produce an invalid clip range.")
        # Assignment validators do not run a model-level range check after two
        # separate field assignments, so explicitly validate the final model.
        Clip.model_validate(clip.model_dump())
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
        conflicts = self.conflicts()
        if conflicts:
            first = conflicts[0]
            raise TimelineConflictError(
                f"Clips {first['left_clip_id']} and {first['right_clip_id']} overlap "
                f"by {first['overlap']:.3f}s without a matching transition."
            )
        ProjectDocument.model_validate(self.project.model_dump())
