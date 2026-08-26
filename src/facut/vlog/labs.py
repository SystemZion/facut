"""Evidence-grounded StoryGraph 3 briefs and director labs.

The module deliberately contains no visual inference.  It validates and organises
observations supplied by an external visual agent, and persists reviewable plans.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from facut.core.models import ProjectDocument
from facut.core.project_manager import ProjectManager

from .context import load_trip_bible, trip_bible_fact_policy, trip_bible_sha256
from .director import _read_json, _root, _write_json
from .models import EvidenceObservation, StoryCandidate, StoryPlan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalStorySubmission(_StrictModel):
    """A complete, evidence-addressed story proposal from an external AI."""

    version: Literal["3.0"] = "3.0"
    style: str = Field(min_length=1)
    target_duration: float = Field(gt=0)
    candidates: list[StoryCandidate] = Field(min_length=1, max_length=6)
    selected_candidate_id: str | None = None

    @model_validator(mode="after")
    def selected_candidate_exists(self) -> "ExternalStorySubmission":
        ids = [item.id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique")
        if self.selected_candidate_id and self.selected_candidate_id not in ids:
            raise ValueError("selected_candidate_id does not reference a submitted candidate")
        return self


def _load_observations(project_dir: str | Path) -> tuple[list[EvidenceObservation], bytes]:
    path = _root(project_dir) / "observations.json"
    if not path.is_file():
        raise FileNotFoundError("Visual observations were not found. Run VLOG inspection first.")
    raw = path.read_bytes()
    payload = json.loads(raw)
    observations = TypeAdapter(list[EvidenceObservation]).validate_python(
        payload.get("observations", [])
    )
    if not observations:
        raise ValueError("StoryGraph requires at least one visual observation.")
    return observations, raw


def build_story_brief(project_dir: str | Path) -> dict[str, Any]:
    """Build the compact, source-addressed contract an external director consumes."""

    observations, raw = _load_observations(project_dir)
    episodes: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}
    locations: dict[str, list[str]] = {}
    motifs: dict[str, list[str]] = {}
    for item in observations:
        if item.episode_id:
            episodes.setdefault(item.episode_id, []).append(item.observation_id)
        if item.event_id:
            events.setdefault(item.event_id, []).append(item.observation_id)
        if item.location_id or item.location:
            locations.setdefault(item.location_id or item.location or "", []).append(
                item.observation_id
            )
        for motif in item.visual_motifs:
            motifs.setdefault(motif, []).append(item.observation_id)
    brief = {
        "version": "3.0",
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "visual_provider": "external-agent",
        "generation_baseline": "deterministic-baseline",
        "constraints": {
            "evidence_only": True,
            "do_not_invent_facts": True,
            "preserve_causal_order": True,
            "incident_requires_resolution_when_available": True,
        },
        "episodes": episodes,
        "events": events,
        "locations": locations,
        "visual_motifs": motifs,
        "observations": [item.model_dump(mode="json") for item in observations],
        "required_output_schema": ExternalStorySubmission.model_json_schema(),
    }
    _write_json(_root(project_dir) / "story.brief.json", brief)
    return brief


def _story_validation_issues(
    candidates: list[StoryCandidate],
    observations: list[EvidenceObservation],
    document: ProjectDocument | None = None,
) -> list[dict[str, Any]]:
    known = {item.observation_id: item for item in observations}
    chains: dict[str, list[EvidenceObservation]] = {}
    for item in observations:
        if item.event_chain:
            chains.setdefault(item.event_chain, []).append(item)
    issues: list[dict[str, Any]] = []
    for candidate in candidates:
        ids = [item.observation_id for item in candidate.segments]
        positions = {observation_id: index for index, observation_id in enumerate(ids)}
        if len(ids) != len(set(ids)):
            issues.append({"code": "DUPLICATE_STORY_EVIDENCE", "severity": "error", "candidate_id": candidate.id})
        for segment in candidate.segments:
            evidence = known.get(segment.observation_id)
            if evidence is None:
                issues.append({"code": "UNKNOWN_STORY_EVIDENCE", "severity": "error", "candidate_id": candidate.id, "observation_id": segment.observation_id})
            elif segment.media_id != evidence.media_id:
                issues.append({"code": "EVIDENCE_MEDIA_MISMATCH", "severity": "error", "candidate_id": candidate.id, "observation_id": segment.observation_id})
        selected_events = {
            known[item].event_id: positions[item]
            for item in ids
            if item in known and known[item].event_id
        }
        for observation_id in ids:
            evidence = known.get(observation_id)
            if evidence is None:
                continue
            for required in evidence.requires_before:
                if required not in selected_events:
                    issues.append({"code": "MISSING_REQUIRED_PREDECESSOR", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": required})
                elif selected_events[required] >= positions[observation_id]:
                    issues.append({"code": "CAUSAL_ORDER_VIOLATION", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": required})
            for cause in evidence.causes:
                if cause not in selected_events:
                    issues.append({"code": "MISSING_CAUSAL_EVENT", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": cause})
                elif selected_events[cause] >= positions[observation_id]:
                    issues.append({"code": "CAUSAL_ORDER_VIOLATION", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": cause})
            for required in evidence.requires_after:
                if required not in selected_events:
                    issues.append({"code": "MISSING_REQUIRED_OUTCOME", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": required})
                elif selected_events[required] <= positions[observation_id]:
                    issues.append({"code": "CAUSAL_ORDER_VIOLATION", "severity": "error", "candidate_id": candidate.id, "observation_id": observation_id, "event_id": required})

        selected_chain_names = {
            known[item].event_chain
            for item in ids
            if item in known and known[item].event_chain
        }
        for chain_name in sorted(selected_chain_names):
            chain = chains[chain_name]
            selected = [known[item] for item in ids if item in known and known[item].event_chain == chain_name]
            subjects = {item.subject_id for item in selected if item.subject_id}
            if len(subjects) > 1:
                issues.append({
                    "code": "EVENT_CHAIN_SUBJECT_MISMATCH",
                    "severity": "error",
                    "candidate_id": candidate.id,
                    "event_chain": chain_name,
                })
            orders = [item.event_order for item in selected if item.event_order is not None]
            if orders != sorted(orders) or len(orders) != len(set(orders)):
                issues.append({
                    "code": "EVENT_CHAIN_ORDER_VIOLATION",
                    "severity": "error",
                    "candidate_id": candidate.id,
                    "event_chain": chain_name,
                })
            selected_ids = {item.observation_id for item in selected}
            for incident in [item for item in selected if item.story_role == "incident"]:
                outcomes = [
                    item
                    for item in chain
                    if item.story_role in {"recovery", "outcome"}
                    and (not incident.subject_id or item.subject_id == incident.subject_id)
                    and (
                        incident.event_order is None
                        or item.event_order is None
                        or item.event_order > incident.event_order
                    )
                ]
                if outcomes and not any(item.observation_id in selected_ids for item in outcomes):
                    issues.append({
                        "code": "MISSING_EVENT_CHAIN_RECOVERY",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "event_chain": chain_name,
                        "observation_id": incident.observation_id,
                    })
            for recovery in [item for item in selected if item.story_role in {"recovery", "outcome"}]:
                incidents = [
                    item
                    for item in chain
                    if item.story_role == "incident"
                    and (not recovery.subject_id or item.subject_id == recovery.subject_id)
                    and (
                        recovery.event_order is None
                        or item.event_order is None
                        or item.event_order < recovery.event_order
                    )
                ]
                if incidents and not any(item.observation_id in selected_ids for item in incidents):
                    issues.append({
                        "code": "MISSING_EVENT_CHAIN_INCIDENT",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "event_chain": chain_name,
                        "observation_id": recovery.observation_id,
                    })

        if document is not None:
            media_by_id = {item.id: item for item in document.media}
            frame_tolerance = 1.0 / max(1.0, document.project.fps)
            previous_end: float | None = None
            for segment in candidate.segments:
                media = media_by_id.get(segment.media_id)
                if media is None:
                    issues.append({
                        "code": "UNKNOWN_STORY_MEDIA",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "media_id": segment.media_id,
                    })
                    continue
                if (
                    media.technical.duration is not None
                    and segment.source_out > media.technical.duration + frame_tolerance
                ):
                    issues.append({
                        "code": "STORY_SOURCE_RANGE_EXCEEDS_MEDIA",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "observation_id": segment.observation_id,
                    })
                evidence = known.get(segment.observation_id)
                if evidence and (
                    segment.source_in < evidence.range.start - frame_tolerance
                    or segment.source_out > evidence.range.end + frame_tolerance
                ):
                    issues.append({
                        "code": "STORY_RANGE_OUTSIDE_EVIDENCE",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "observation_id": segment.observation_id,
                    })
                if previous_end is not None and segment.timeline_start < previous_end - frame_tolerance:
                    issues.append({
                        "code": "STORY_TIMELINE_OVERLAP",
                        "severity": "error",
                        "candidate_id": candidate.id,
                        "observation_id": segment.observation_id,
                    })
                previous_end = segment.timeline_start + segment.duration
    return issues


def submit_story_proposal(project_dir: str | Path, payload: dict[str, Any]) -> StoryPlan:
    """Validate and persist an externally directed StoryGraph without applying it."""

    submission = ExternalStorySubmission.model_validate(payload)
    manager = ProjectManager(project_dir)
    document = manager.load() if manager.project_file.is_file() else None
    observations, evidence_raw = _load_observations(project_dir)
    issues = _story_validation_issues(submission.candidates, observations, document)
    errors = [item for item in issues if item["severity"] == "error"]
    if errors:
        codes = ", ".join(sorted({item["code"] for item in errors}))
        raise ValueError(f"External story proposal failed validation: {codes}.")
    current = _read_json(_root(project_dir) / "story.plan.json", {})
    manifest = _read_json(_root(project_dir) / "manifest.json", {})
    bible = load_trip_bible(
        project_dir,
        default_name=document.project.name if document is not None else "Untitled trip",
    )
    fact_policy = trip_bible_fact_policy(bible)
    for candidate in submission.candidates:
        candidate.polish_plan["trip_bible"] = fact_policy
    plan = StoryPlan(
        project_revision=(
            document.revision
            if document is not None
            else int(manifest.get("project_revision", 0))
        ),
        evidence_sha256=hashlib.sha256(evidence_raw).hexdigest(),
        trip_bible_sha256=trip_bible_sha256(bible),
        style=submission.style,
        target_duration=submission.target_duration,
        status="ready" if submission.selected_candidate_id else "review_required",
        candidates=submission.candidates,
        selected_candidate_id=submission.selected_candidate_id,
        quality_weights=current.get("quality_weights", {
            "story_coherence": 0.25, "shot_quality": 0.20,
            "evidence_reliability": 0.20, "continuity": 0.15,
            "sound_value": 0.10, "style_fit": 0.10,
        }),
        warnings=[],
        generation_mode="external-director",
    )
    _write_json(_root(project_dir) / "story.plan.json", plan.model_dump(mode="json"))
    return plan


def validate_story_plan(project_dir: str | Path) -> dict[str, Any]:
    """Validate the persisted plan against current evidence and causal constraints."""

    raw = _read_json(_root(project_dir) / "story.plan.json")
    if raw is None:
        raise FileNotFoundError("Story plan was not found.")
    plan = StoryPlan.model_validate(raw)
    manager = ProjectManager(project_dir)
    document = manager.load() if manager.project_file.is_file() else None
    observations, evidence_raw = _load_observations(project_dir)
    issues = _story_validation_issues(plan.candidates, observations, document)
    if plan.evidence_sha256 != hashlib.sha256(evidence_raw).hexdigest():
        issues.insert(0, {"code": "STALE_STORY_EVIDENCE", "severity": "error"})
    if document is not None and plan.project_revision != document.revision:
        issues.insert(0, {"code": "STALE_STORY_PROJECT", "severity": "error"})
    return {
        "status": "pass" if not any(item["severity"] == "error" for item in issues) else "review_required",
        "generation_mode": plan.generation_mode,
        "candidate_count": len(plan.candidates),
        "issues": issues,
    }


def _lab_candidates(observations: list[EvidenceObservation], kind: Literal["opening", "ending"]) -> list[dict[str, Any]]:
    if kind == "opening":
        specifications = [
            ("highlight-cold-open", ("reaction", "climax", "incident")),
            ("person-or-question", ("reaction", "setup", "action")),
            ("place-and-atmosphere", ("transition", "setup")),
        ]
    else:
        specifications = [
            ("resolution-and-reflection", ("outcome", "recovery", "reaction")),
            ("departure-sunset-photo", ("transition", "outcome")),
            ("motif-callback", ("outcome", "reaction", "transition")),
        ]
    output: list[dict[str, Any]] = []
    for candidate_id, roles in specifications:
        ranked = sorted(
            observations,
            key=lambda item: (
                item.event_role not in roles,
                -(item.confidence * 0.55 + item.quality * 0.45),
                item.observation_id,
            ),
        )
        selected = ranked[: min(3, len(ranked))]
        output.append({
            "id": f"{kind}-{candidate_id}",
            "strategy": candidate_id,
            "status": "review_required",
            "observation_ids": [item.observation_id for item in selected],
            "estimated_duration": round(sum(min(4.0, item.range.end - item.range.start) for item in selected), 3),
            "reason": f"Evidence-ranked {kind} proposal; external AI must review timing and meaning.",
        })
    return output


def plan_openings(project_dir: str | Path) -> dict[str, Any]:
    observations, raw = _load_observations(project_dir)
    result = {"version": "1.0", "kind": "opening", "evidence_sha256": hashlib.sha256(raw).hexdigest(), "candidates": _lab_candidates(observations, "opening")}
    _write_json(_root(project_dir) / "opening.plan.json", result)
    return result


def plan_endings(project_dir: str | Path) -> dict[str, Any]:
    observations, raw = _load_observations(project_dir)
    result = {"version": "1.0", "kind": "ending", "evidence_sha256": hashlib.sha256(raw).hexdigest(), "candidates": _lab_candidates(observations, "ending")}
    _write_json(_root(project_dir) / "ending.plan.json", result)
    return result


_DAYPART_ORDER = {"dawn": 0, "morning": 1, "day": 2, "afternoon": 3, "sunset": 4, "evening": 5, "night": 6}
_OPPOSITE_DIRECTIONS = {("left", "right"), ("right", "left"), ("up", "down"), ("down", "up"), ("in", "out"), ("out", "in")}


def check_continuity(project_dir: str | Path, candidate_id: str | None = None) -> dict[str, Any]:
    """Run deterministic checks only; semantic ambiguities remain review items."""

    plan = StoryPlan.model_validate(_read_json(_root(project_dir) / "story.plan.json"))
    observations, _ = _load_observations(project_dir)
    known = {item.observation_id: item for item in observations}
    candidates = plan.candidates if candidate_id is None else [item for item in plan.candidates if item.id == candidate_id]
    if not candidates:
        raise ValueError(f'Unknown story candidate "{candidate_id}".')
    reports: list[dict[str, Any]] = []
    for candidate in candidates:
        selected = [known[item.observation_id] for item in candidate.segments if item.observation_id in known]
        issues = _story_validation_issues([candidate], observations)
        seen_compositions: dict[str, str] = {}
        for index, item in enumerate(selected):
            previous = selected[index - 1] if index else None
            if item.composition_signature:
                prior = seen_compositions.get(item.composition_signature)
                if prior:
                    issues.append({"code": "REPEATED_COMPOSITION", "severity": "warning", "between": [prior, item.observation_id]})
                seen_compositions[item.composition_signature] = item.observation_id
            if previous is None:
                continue
            previous_location = previous.location_id or previous.location
            current_location = item.location_id or item.location
            if previous_location and current_location and previous_location != current_location:
                issues.append({"code": "LOCATION_JUMP", "severity": "warning", "between": [previous.observation_id, item.observation_id]})
            if previous.daypart in _DAYPART_ORDER and item.daypart in _DAYPART_ORDER and _DAYPART_ORDER[item.daypart] < _DAYPART_ORDER[previous.daypart]:
                issues.append({"code": "DAYPART_REGRESSION", "severity": "warning", "between": [previous.observation_id, item.observation_id]})
            if (previous.movement_direction, item.movement_direction) in _OPPOSITE_DIRECTIONS:
                issues.append({"code": "MOVEMENT_DIRECTION_CONFLICT", "severity": "warning", "between": [previous.observation_id, item.observation_id]})
        for index, item in enumerate(selected):
            is_dialogue = bool(item.original_audio_quote) or any(token in _text(item) for token in ("dialogue", "talk", "讲话", "口播"))
            neighbours = selected[max(0, index - 1): index + 2]
            if is_dialogue and not any(other.broll_topics for other in neighbours if other is not item):
                issues.append({"code": "BROLL_COVERAGE_GAP", "severity": "warning", "observation_id": item.observation_id})
        if selected:
            last = selected[-1]
            ending_tokens = ("farewell", "sunset", "photo", "reflection", "告别", "日落", "全家福", "回味")
            if last.event_role not in {"outcome", "recovery"} and not any(token in _text(last) for token in ending_tokens):
                issues.append({"code": "HARD_STOP_RISK", "severity": "warning", "observation_id": last.observation_id})
        reports.append({
            "candidate_id": candidate.id,
            "status": "review_required" if issues else "pass",
            "issues": issues,
            "checked_fields": ["causal_order", "location", "daypart", "movement_direction", "composition_signature", "broll_topics", "ending_resolution"],
        })
    result = {"version": "1.0", "reports": reports}
    _write_json(_root(project_dir) / "continuity.report.json", result)
    return result


def _text(item: EvidenceObservation) -> str:
    return " ".join([item.summary, *item.actions, *item.tags, item.event_role or ""]).casefold()
