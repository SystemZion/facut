"""Strict public models for the Vlog Director evidence and story contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VlogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(VlogModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "TimeRange":
        if self.end <= self.start:
            raise ValueError("range end must be greater than start")
        return self


class EvidenceObservation(VlogModel):
    """One externally observed, source-addressed piece of visual evidence."""

    observation_id: str = Field(min_length=1)
    media_id: str = Field(min_length=1)
    range: TimeRange
    summary: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    shot_type: str | None = None
    camera_motion: str | None = None
    daypart: str | None = None
    location: str | None = None
    original_audio_value: Literal["unknown", "low", "medium", "high"] = "unknown"
    quality: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_frames: list[int] = Field(default_factory=list)
    subject_id: str | None = None
    event_chain: str | None = None
    event_order: int | None = Field(default=None, ge=0)
    story_role: Literal["setup", "incident", "recovery", "outcome"] | None = None
    episode_id: str | None = None
    location_id: str | None = None
    event_id: str | None = None
    event_role: Literal[
        "setup", "action", "reaction", "incident", "recovery", "outcome", "transition"
    ] | None = None
    causes: list[str] = Field(default_factory=list)
    requires_before: list[str] = Field(default_factory=list)
    requires_after: list[str] = Field(default_factory=list)
    emotion: str | None = None
    original_audio_quote: str | None = None
    visual_motifs: list[str] = Field(default_factory=list)
    movement_direction: Literal[
        "left", "right", "up", "down", "in", "out", "static", "mixed"
    ] | None = None
    composition_signature: str | None = None
    broll_topics: list[str] = Field(default_factory=list)
    provider: str = "external-agent"
    warnings: list[str] = Field(default_factory=list)


class StorySegment(VlogModel):
    stage: str
    media_id: str
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    timeline_start: float = Field(ge=0)
    duration: float = Field(gt=0)
    observation_id: str
    reason: str
    confidence: float = Field(ge=0, le=1)
    evidence_frames: list[int] = Field(default_factory=list)
    preserve_original_audio: bool = False
    alternatives: list[str] = Field(default_factory=list)


class StoryCandidate(VlogModel):
    id: str
    name: str
    strategy: Literal["narrative", "immersive", "visual"]
    score: float = Field(ge=0, le=1)
    estimated_duration: float = Field(ge=0)
    segments: list[StorySegment]
    missing_stages: list[str] = Field(default_factory=list)
    unresolved_gaps: list[dict[str, Any]] = Field(default_factory=list)
    continuity: dict[str, Any] = Field(default_factory=dict)
    sound_strategy: dict[str, Any] = Field(default_factory=dict)
    subtitle_strategy: dict[str, Any] = Field(default_factory=dict)
    polish_plan: dict[str, Any] = Field(default_factory=dict)


class StoryPlan(VlogModel):
    version: Literal["2.0"] = "2.0"
    project_revision: int = Field(ge=0)
    evidence_sha256: str
    trip_bible_sha256: str | None = None
    style: str
    target_duration: float = Field(gt=0)
    status: Literal["review_required", "ready"] = "review_required"
    candidates: list[StoryCandidate]
    selected_candidate_id: str | None = None
    quality_weights: dict[str, float]
    warnings: list[str] = Field(default_factory=list)
    generation_mode: Literal["deterministic-baseline", "external-director"] = (
        "deterministic-baseline"
    )


def vlog_workflow_schema() -> dict[str, Any]:
    """Machine-readable workflow contract used by independent AI agents."""

    from .context import DirectorInboxItem, TripBible

    return {
        "version": "1.0",
        "quality_policy": {
            "priority": "quality-first",
            "token_budget_is_hard_limit": False,
            "visual_provider": "external-agent",
            "minimum_visual_coverage": "Every playable non-duplicate video asset requires a baseline inspection task.",
        },
        "steps": [
            {"action": "vlog.prepare", "mutates_project": True},
            {"action": "vlog.atlas.build", "default_batch_size": 12},
            {"action": "vlog.bible.import", "requires": "reviewed facts or an empty bible"},
            {"action": "vlog.inbox.next", "repeat_until": "baseline and high-priority gaps resolved"},
            {"action": "vlog.inspect.batch", "repeat_until": "atlas pending_tasks == 0"},
            {"action": "vlog.observe.batch", "idempotent_by": "task_id and observation_id"},
            {"action": "vlog.story.brief", "visual_provider": "external-agent"},
            {"action": "vlog.story.submit", "preferred": True},
            {"action": "vlog.plan", "fallback": "deterministic-baseline", "default_candidates": 3},
            {"action": "vlog.continuity.check"},
            {"action": "vlog.opening.plan", "default_candidates": 3},
            {"action": "vlog.ending.plan", "default_candidates": 3},
            {"action": "vlog.compare"},
            {"action": "vlog.refine"},
            {"action": "vlog.apply", "requires": "ready candidate"},
            {"action": "vlog.soundscape.plan", "requires": "audition before approval"},
            {"action": "vlog.review.create", "maximum_rounds": 3},
            {"action": "vlog.review.apply", "requires": "approved deterministic edits"},
            {"action": "vlog.build", "requires": "ready candidate"},
        ],
        "evidence_schema": EvidenceObservation.model_json_schema(),
        "director_inbox_schema": DirectorInboxItem.model_json_schema(),
        "trip_bible_schema": TripBible.model_json_schema(),
        "story_plan_schema": StoryPlan.model_json_schema(),
    }
