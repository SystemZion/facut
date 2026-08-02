"""Pydantic models used by facut project files.

The project document is intentionally renderer-agnostic.  Editing commands mutate
these models; backends translate the result into a concrete render graph.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Create a compact, sortable-enough opaque identifier.

    Media imports use a content-derived identifier instead; this helper is for
    project, track, clip and history entities.
    """

    return f"{prefix}_{uuid4().hex[:16].upper()}"


class StrictModel(BaseModel):
    """Base class which rejects unknown project-file keys."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    SUBTITLE = "subtitle"


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    SUBTITLE = "subtitle"
    ADJUSTMENT = "adjustment"
    MASK = "mask"


class Transform(StrictModel):
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    crop_left: float = Field(default=0.0, ge=0.0)
    crop_top: float = Field(default=0.0, ge=0.0)
    crop_right: float = Field(default=0.0, ge=0.0)
    crop_bottom: float = Field(default=0.0, ge=0.0)
    fit: Literal["contain", "cover", "stretch", "none"] = "contain"
    flip_x: bool = False
    flip_y: bool = False
    autorotate: bool = True
    stabilize: bool = False


class Keyframe(StrictModel):
    id: str = Field(default_factory=lambda: new_id("keyframe"))
    property: str
    time: float = Field(ge=0.0)
    value: Any
    easing: str = "linear"


class Effect(StrictModel):
    id: str = Field(default_factory=lambda: new_id("effect"))
    type: str
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)
    keyframes: list[Keyframe] = Field(default_factory=list)


class MediaTechnicalInfo(StrictModel):
    container: str | None = None
    duration: float | None = Field(default=None, ge=0.0)
    bitrate: int | None = Field(default=None, ge=0)
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    frame_rate: float | None = Field(default=None, gt=0.0)
    average_frame_rate: float | None = Field(default=None, gt=0.0)
    pixel_format: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    audio_channels: int | None = Field(default=None, ge=1)
    sample_rate: int | None = Field(default=None, ge=1)
    rotation: int = 0
    has_subtitles: bool = False
    variable_frame_rate: bool = False
    creation_time: str | None = None
    timecode: str | None = None
    timezone_offset: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens_model: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    altitude: float | None = None
    dynamic_range: Literal["sdr", "hdr-pq", "hdr-hlg", "log", "unknown"] = "unknown"
    keyframes: list[float] = Field(default_factory=list)


class MediaAsset(StrictModel):
    id: str
    kind: MediaKind
    path: str
    original_name: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_at: datetime = Field(default_factory=utc_now)
    technical: MediaTechnicalInfo = Field(default_factory=MediaTechnicalInfo)
    proxy_path: str | None = None
    offline: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioCompressorSettings(StrictModel):
    """Non-destructive dynamic-range compressor parameters."""

    threshold_db: float = Field(default=-18.0, ge=-60.0, le=0.0)
    ratio: float = Field(default=4.0, ge=1.0, le=20.0)
    attack_ms: float = Field(default=20.0, ge=0.01, le=2000.0)
    release_ms: float = Field(default=250.0, ge=1.0, le=9000.0)
    makeup_db: float = Field(default=0.0, ge=0.0, le=24.0)


class AudioLimiterSettings(StrictModel):
    """True peak guard compiled to FFmpeg's alimiter filter."""

    ceiling_db: float = Field(default=-1.0, ge=-20.0, le=0.0)
    attack_ms: float = Field(default=5.0, ge=0.1, le=80.0)
    release_ms: float = Field(default=50.0, ge=1.0, le=8000.0)


class AudioLoudnessSettings(StrictModel):
    """Single-pass EBU R128 loudness normalization targets."""

    target_lufs: float = Field(default=-14.0, ge=-70.0, le=-5.0)
    true_peak_db: float = Field(default=-1.0, ge=-9.0, le=0.0)
    loudness_range: float = Field(default=11.0, ge=1.0, le=50.0)


class AudioProcessing(StrictModel):
    """Unified audio settings shared by native and independent audio clips."""

    gain_db: float = Field(default=0.0, ge=-96.0, le=24.0)
    muted: bool = False
    fade_in: float = Field(default=0.0, ge=0.0)
    fade_out: float = Field(default=0.0, ge=0.0)
    highpass_hz: float | None = Field(default=None, ge=20.0, le=20000.0)
    denoise_strength: float | None = Field(default=None, ge=0.01, le=1.0)
    compressor: AudioCompressorSettings | None = None
    limiter: AudioLimiterSettings | None = None
    loudness: AudioLoudnessSettings | None = None
    channel_mode: Literal["original", "mono", "stereo"] = "original"
    pan: float | None = Field(default=None, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def validate_channel_processing(self) -> "AudioProcessing":
        if self.channel_mode == "mono" and self.pan is not None:
            raise ValueError("pan is unavailable when channel_mode is mono")
        return self


class Clip(StrictModel):
    id: str = Field(default_factory=lambda: new_id("clip"))
    media_id: str
    track_id: str
    timeline_start: float = Field(default=0.0, ge=0.0)
    source_in: float = Field(default=0.0, ge=0.0)
    source_out: float = Field(gt=0.0)
    speed: float = 1.0
    enabled: bool = True
    muted: bool = False
    volume_db: float = 0.0
    timeline_duration: float | None = Field(default=None, gt=0.0)
    freeze_frame: float | None = Field(default=None, ge=0.0)
    loop: bool = False
    audio_fade_in: float = Field(default=0.0, ge=0.0)
    audio_fade_out: float = Field(default=0.0, ge=0.0)
    audio: AudioProcessing = Field(default_factory=AudioProcessing)
    transform: Transform = Field(default_factory=Transform)
    effects: list[Effect] = Field(default_factory=list)
    keyframes: list[Keyframe] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_range(self) -> "Clip":
        if self.source_out <= self.source_in:
            raise ValueError("source_out must be greater than source_in")
        if self.speed == 0:
            raise ValueError("speed cannot be zero")
        natural_duration = (self.source_out - self.source_in) / abs(self.speed)
        if (
            self.timeline_duration is not None
            and not self.loop
            and self.freeze_frame is None
        ):
            raise ValueError(
                "timeline_duration requires loop or freeze_frame to be enabled"
            )
        duration = self.timeline_duration or natural_duration
        if (
            self.audio_fade_in > duration
            or self.audio_fade_out > duration
            or self.audio.fade_in > duration
            or self.audio.fade_out > duration
        ):
            raise ValueError("audio fade duration cannot exceed clip duration")
        return self

    @property
    def duration(self) -> float:
        return self.timeline_duration or (
            (self.source_out - self.source_in) / abs(self.speed)
        )

    @property
    def end(self) -> float:
        return self.timeline_start + self.duration

    @property
    def start(self) -> float:
        """Compatibility shorthand for timeline_start."""

        return self.timeline_start


class Track(StrictModel):
    id: str
    type: TrackType
    name: str
    order: int = Field(default=0, ge=0)
    enabled: bool = True
    locked: bool = False
    muted: bool = False
    clips: list[Clip] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("clips")
    @classmethod
    def unique_clip_ids(cls, clips: list[Clip]) -> list[Clip]:
        ids = [clip.id for clip in clips]
        if len(ids) != len(set(ids)):
            raise ValueError("clip ids must be unique within a track")
        return clips


class Transition(StrictModel):
    id: str = Field(default_factory=lambda: new_id("transition"))
    type: str
    duration: float = Field(gt=0.0)
    from_clip_id: str | None = None
    to_clip_id: str | None = None
    track_id: str | None = None
    at: float | None = Field(default=None, ge=0.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    sound_media_id: str | None = None
    sound_offset: float = 0.0

    @model_validator(mode="after")
    def validate_anchor(self) -> "Transition":
        clip_pair = bool(self.from_clip_id and self.to_clip_id)
        timeline_anchor = self.track_id is not None and self.at is not None
        if not (clip_pair or timeline_anchor):
            raise ValueError("transition requires a clip pair or track_id and at")
        return self


class Marker(StrictModel):
    id: str = Field(default_factory=lambda: new_id("marker"))
    at: float = Field(ge=0.0)
    label: str = ""
    color: str = "#FFD54F"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TextStyle(StrictModel):
    """Portable styling shared by subtitle cues and free text overlays."""

    font_family: str | None = None
    font_size: float = Field(default=64.0, gt=0.0)
    font_weight: str = "normal"
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    stroke_color: str = Field(
        default="#000000", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
    )
    stroke_width: float = Field(default=2.0, ge=0.0)
    shadow: float = Field(default=1.0, ge=0.0)
    background: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
    )
    alignment: Literal["left", "center", "right"] = "center"
    line_spacing: float = 1.0
    letter_spacing: float = 0.0
    safe_area: bool = False
    safe_margin_percent: float = Field(default=5.0, ge=0.0, le=25.0)


class SubtitleCue(StrictModel):
    """One timed subtitle cue attached to a subtitle track."""

    id: str = Field(default_factory=lambda: new_id("subtitle"))
    track_id: str
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    text: str = Field(min_length=1)
    style: TextStyle | None = None
    source_identifier: str | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "SubtitleCue":
        if self.end <= self.start:
            raise ValueError("subtitle cue end must be greater than start")
        return self


class TextOverlay(StrictModel):
    """Timed free text rendered independently of spoken-word subtitles."""

    id: str = Field(default_factory=lambda: new_id("text"))
    track_id: str | None = None
    text: str = Field(min_length=1)
    subtitle: str | None = None
    at: float = Field(ge=0.0)
    duration: float = Field(gt=0.0)
    x: float | str = "center"
    y: float | str = "80%"
    style: TextStyle = Field(default_factory=TextStyle)
    entrance: str | None = None
    exit: str | None = None
    template: str | None = None
    template_parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("x", "y")
    @classmethod
    def validate_position(cls, value: float | str) -> float | str:
        if isinstance(value, float):
            return value
        normalized = value.strip().lower()
        if normalized in {"left", "center", "right", "top", "bottom"}:
            return normalized
        if normalized.endswith("%"):
            try:
                percent = float(normalized[:-1])
            except ValueError as exc:
                raise ValueError("position percentage must be numeric") from exc
            if not 0.0 <= percent <= 100.0:
                raise ValueError("position percentage must be between 0% and 100%")
            return normalized
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError(
                "position must be a number, percentage, or named alignment"
            ) from exc

    @property
    def end(self) -> float:
        return self.at + self.duration


class HistoryEntry(StrictModel):
    revision: int = Field(ge=1)
    action: str
    timestamp: datetime = Field(default_factory=utc_now)
    summary: str
    command: dict[str, Any] = Field(default_factory=dict)


class ProjectSettings(StrictModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    name: str
    width: int = Field(default=1920, ge=16, le=16384)
    height: int = Field(default=1080, ge=16, le=16384)
    fps: float = Field(default=30.0, gt=0.0, le=240.0)
    sample_rate: int = Field(default=48000, ge=8000, le=384000)
    background: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    duration: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectDocument(StrictModel):
    version: str = "1.0"
    revision: int = Field(default=0, ge=0)
    project: ProjectSettings
    media: list[MediaAsset] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    subtitle_cues: list[SubtitleCue] = Field(default_factory=list)
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    history: list[HistoryEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_global_ids_and_references(self) -> "ProjectDocument":
        media_ids = [asset.id for asset in self.media]
        track_ids = [track.id for track in self.tracks]
        if len(media_ids) != len(set(media_ids)):
            raise ValueError("media ids must be unique")
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("track ids must be unique")
        known_media = set(media_ids)
        known_tracks = set(track_ids)
        clip_ids: set[str] = set()
        for track in self.tracks:
            for clip in track.clips:
                if clip.track_id != track.id:
                    raise ValueError(f"clip {clip.id} track_id does not match containing track")
                if clip.media_id not in known_media:
                    raise ValueError(f"clip {clip.id} references unknown media {clip.media_id}")
                if clip.id in clip_ids:
                    raise ValueError(f"duplicate clip id {clip.id}")
                clip_ids.add(clip.id)
        for transition in self.transitions:
            for clip_id in (transition.from_clip_id, transition.to_clip_id):
                if clip_id is not None and clip_id not in clip_ids:
                    raise ValueError(f"transition {transition.id} references unknown clip {clip_id}")
            if transition.track_id is not None and transition.track_id not in known_tracks:
                raise ValueError(
                    f"transition {transition.id} references unknown track {transition.track_id}"
                )
        cue_ids: set[str] = set()
        for cue in self.subtitle_cues:
            if cue.id in cue_ids:
                raise ValueError(f"duplicate subtitle cue id {cue.id}")
            cue_ids.add(cue.id)
            track = self.find_track(cue.track_id)
            if track is None:
                raise ValueError(f"subtitle cue {cue.id} references unknown track {cue.track_id}")
            if track.type != TrackType.SUBTITLE:
                raise ValueError(f"subtitle cue {cue.id} requires a subtitle track")
        overlay_ids: set[str] = set()
        for overlay in self.text_overlays:
            if overlay.id in overlay_ids:
                raise ValueError(f"duplicate text overlay id {overlay.id}")
            overlay_ids.add(overlay.id)
            if overlay.track_id is not None and overlay.track_id not in known_tracks:
                raise ValueError(
                    f"text overlay {overlay.id} references unknown track {overlay.track_id}"
                )
        return self

    def recompute_duration(self) -> float:
        """Update and return the maximum enabled clip end."""

        ends = [clip.end for track in self.tracks for clip in track.clips if clip.enabled]
        ends.extend(cue.end for cue in self.subtitle_cues)
        ends.extend(overlay.end for overlay in self.text_overlays if overlay.enabled)
        duration = max(ends, default=0.0)
        self.project.duration = duration
        return duration

    def find_media(self, media_id: str) -> MediaAsset | None:
        return next((item for item in self.media if item.id == media_id), None)

    def find_track(self, track_id: str) -> Track | None:
        return next((item for item in self.tracks if item.id == track_id), None)

    def find_clip(self, clip_id: str) -> Clip | None:
        return next(
            (clip for track in self.tracks for clip in track.clips if clip.id == clip_id),
            None,
        )


# Backward-friendly name used in examples and third-party integrations.
FacutProject = ProjectDocument


def project_json_schema() -> dict[str, Any]:
    """Return the machine-readable JSON Schema for a project file."""

    return ProjectDocument.model_json_schema()


def resolve_stored_path(project_dir: Path, stored_path: str) -> Path:
    """Resolve a project path while keeping project files portable."""

    path = Path(stored_path)
    return path if path.is_absolute() else project_dir / path
