"""Strict public models for FACUT recipe files."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from facut.exceptions import InvalidArgumentError


class RecipeError(InvalidArgumentError):
    code = "INVALID_RECIPE"


class RecipeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RecipeTrack(RecipeModel):
    type: Literal["video", "audio", "image", "subtitle", "adjustment", "mask"]
    name: str = Field(min_length=1)
    id: str | None = None
    role: Literal["music", "narration", "ambience", "voice", "effects"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecipeEffect(RecipeModel):
    type: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecipeClip(RecipeModel):
    media_id: str = Field(min_length=1)
    track: str = Field(min_length=1)
    id: str | None = None
    at: str | float = 0
    source_in: str | float = Field(default=0, alias="in")
    source_out: str | float | None = Field(default=None, alias="out")
    duration: str | float | None = None
    append: bool = False
    transform: dict[str, Any] = Field(default_factory=dict)
    effects: list[RecipeEffect] = Field(default_factory=list)
    audio: dict[str, Any] = Field(default_factory=dict)
    speed: float | None = None
    reverse: bool = False
    speed_curve: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_speed_controls(self) -> "RecipeClip":
        if self.source_out is not None and self.duration is not None:
            raise ValueError("recipe clip out and duration are mutually exclusive")
        if sum((self.speed is not None, self.reverse, self.speed_curve is not None)) > 1:
            raise ValueError("clip speed, reverse and speed_curve are mutually exclusive")
        if self.speed == 0:
            raise ValueError("clip speed cannot be zero")
        return self


class RecipeTransition(RecipeModel):
    type: str = Field(min_length=1)
    duration: str | float | None = None
    from_clip: str | None = Field(default=None, alias="from")
    to_clip: str | None = Field(default=None, alias="to")
    track: str | None = None
    at: str | float | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchor(self) -> "RecipeTransition":
        pair = self.from_clip is not None and self.to_clip is not None
        position = self.track is not None and self.at is not None
        if pair == position:
            raise ValueError("transition requires exactly one clip pair or track/at anchor")
        return self


class RecipeNarration(RecipeModel):
    plan: str = Field(min_length=1)
    approved_only: bool = True
    duck_music: bool = False
    preserve_original: bool = True
    track_id: str = "A_NARRATION"
    allow_stale: bool = False


class RecipeVlogDirector(RecipeModel):
    candidate_id: str = Field(min_length=1)
    preset: str = "youtube-4k"


class RecipeDirectorReview(RecipeModel):
    plan: str = Field(min_length=1)
    approved_only: bool = True


class RecipeSoundscape(RecipeModel):
    plan: str = Field(min_length=1)
    approved_only: bool = True


class RecipeRender(RecipeModel):
    output: str | None = None
    preset: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecipeDocument(RecipeModel):
    version: Literal["1.0"] = "1.0"
    atomic: Literal[True] = True
    tracks: list[RecipeTrack] = Field(default_factory=list)
    clips: list[RecipeClip] = Field(default_factory=list)
    transitions: list[RecipeTransition] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    narration: RecipeNarration | None = None
    vlog: RecipeVlogDirector | None = None
    review: RecipeDirectorReview | None = None
    soundscape: RecipeSoundscape | None = None
    render: RecipeRender | None = None
    qc: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_identifiers_and_commands(self) -> "RecipeDocument":
        explicit_tracks = [track.id for track in self.tracks if track.id]
        explicit_clips = [clip.id for clip in self.clips if clip.id]
        if len(explicit_tracks) != len(set(explicit_tracks)):
            raise ValueError("recipe track ids must be unique")
        if len(explicit_clips) != len(set(explicit_clips)):
            raise ValueError("recipe clip ids must be unique")
        for command in self.commands:
            if not isinstance(command.get("action"), str) or not command["action"]:
                raise ValueError("every recipe command requires a non-empty action")
        if not any((self.tracks, self.clips, self.transitions, self.commands, self.narration, self.vlog, self.review, self.soundscape, self.render, self.qc)):
            raise ValueError("recipe must declare at least one operation")
        return self


def recipe_json_schema() -> dict[str, Any]:
    return RecipeDocument.model_json_schema()
