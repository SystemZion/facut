"""Review-first, evidence-bound revision plans for VLOG projects.

This module deliberately does not perform visual review.  It packages a
deterministic project snapshot for an external reviewer, validates the returned
findings, and applies only explicitly approved, supported timeline commands in
one ProjectManager transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from facut.core.command_engine import CommandEngine
from facut.core.models import ProjectDocument
from facut.core.project_manager import ProjectManager
from facut.exceptions import InvalidArgumentError, ReviewRequiredError


ReviewPass = Literal["story", "continuity", "sound"]
ReviewStatus = Literal["draft", "approved", "rejected"]

_SUPPORTED_ACTIONS = {
    "timeline.add",
    "clip.move",
    "clip.trim",
    "clip.delete",
    "clip.transform",
    "clip.motion",
    "clip.freeze",
    "clip.speed",
    "clip.speed_curve",
    "audio.volume",
    "audio.fade",
    "audio.process",
    "audio.crossfade",
    "audio.loudness",
    "effect.add",
    "effect.remove",
    "transition.add",
    "transition.remove",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewEdit(_StrictModel):
    """One deterministic edit proposed by an external reviewer."""

    id: str = Field(default_factory=lambda: f"edit_{uuid4().hex[:16].upper()}")
    action: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ReviewStatus = "draft"
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ReviewFinding(_StrictModel):
    id: str = Field(default_factory=lambda: f"finding_{uuid4().hex[:16].upper()}")
    severity: Literal["info", "warning", "error"] = "warning"
    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    edits: list[ReviewEdit] = Field(default_factory=list)


class ReviewPackage(_StrictModel):
    version: Literal["1.0"] = "1.0"
    id: str
    review_pass: ReviewPass
    round: int = Field(ge=1, le=3)
    max_rounds: Literal[3] = 3
    created_at: datetime
    project_id: str
    project_revision: int = Field(ge=0)
    project_sha256: str
    evidence_sha256: str
    timeline: dict[str, Any]
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    questions: list[str]


class ReviewSubmission(_StrictModel):
    version: Literal["1.0"] = "1.0"
    id: str = Field(default_factory=lambda: f"submission_{uuid4().hex[:16].upper()}")
    review_id: str
    project_id: str
    project_revision: int = Field(ge=0)
    project_sha256: str
    evidence_sha256: str
    conclusion: Literal["pass", "changes_requested", "review_required"]
    findings: list[ReviewFinding] = Field(default_factory=list)
    reviewer: str = "external-agent"


class ReviewPlan(_StrictModel):
    version: Literal["1.0"] = "1.0"
    id: str
    review_id: str
    submission_id: str
    round: int = Field(ge=1, le=3)
    project_id: str
    project_revision: int = Field(ge=0)
    project_sha256: str
    evidence_sha256: str
    submission_sha256: str
    status: Literal["ready", "review_required"]
    edits: list[ReviewEdit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _review_root(project_dir: str | Path) -> Path:
    return Path(project_dir) / "cache" / "vlog" / "reviews"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _document_sha256(document: ProjectDocument) -> str:
    return hashlib.sha256(_canonical(document.model_dump(mode="json"))).hexdigest()


def _evidence_sha256(project_dir: str | Path) -> str:
    path = Path(project_dir) / "cache" / "vlog" / "observations.json"
    return hashlib.sha256(path.read_bytes() if path.is_file() else b"").hexdigest()


def _atomic_write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def _load(source: str | Path | dict[str, Any], model: type[_StrictModel]) -> _StrictModel:
    if isinstance(source, dict):
        payload = {key: value for key, value in source.items() if key != "path"}
    else:
        payload = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    return model.model_validate(payload)


def _package_path(project_dir: str | Path, review_id: str) -> Path:
    return _review_root(project_dir) / "packages" / f"{review_id}.json"


def _submitted_review_ids(project_dir: str | Path) -> set[str]:
    submissions = _review_root(project_dir) / "submissions"
    if not submissions.is_dir():
        return set()
    review_ids: set[str] = set()
    for path in submissions.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        review_id = payload.get("review_id")
        if isinstance(review_id, str):
            review_ids.add(review_id)
    return review_ids


def _timeline_snapshot(document: ProjectDocument) -> dict[str, Any]:
    return {
        "duration": document.project.duration,
        "tracks": [
            {
                "id": track.id,
                "type": track.type.value,
                "role": track.metadata.get("role"),
                "clips": [
                    {
                        "id": clip.id,
                        "media_id": clip.media_id,
                        "start": clip.timeline_start,
                        "end": clip.end,
                        "source_in": clip.source_in,
                        "source_out": clip.source_out,
                        "metadata": clip.metadata,
                    }
                    for clip in track.clips
                ],
            }
            for track in document.tracks
        ],
        "transition_count": len(document.transitions),
        "subtitle_count": len(document.subtitle_cues),
        "text_overlay_count": len(document.text_overlays),
    }


def _review_evidence(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "cache" / "vlog" / "observations.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    output: list[dict[str, Any]] = []
    for item in payload.get("observations", []):
        if not isinstance(item, dict):
            continue
        output.append(
            {
                key: item.get(key)
                for key in (
                    "observation_id",
                    "media_id",
                    "range",
                    "summary",
                    "confidence",
                    "evidence_frames",
                )
            }
        )
    return output


def _questions(review_pass: ReviewPass) -> list[str]:
    return {
        "story": [
            "Does every selected event have enough setup, reaction, and outcome evidence?",
            "Are important people, facts, and original-audio moments represented accurately?",
            "Does the opening establish a promise that the ending resolves?",
        ],
        "continuity": [
            "Are time, place, daypart, weather, gaze, and movement direction coherent?",
            "Are repeated source ranges or near-identical compositions overused?",
            "Does every spoken claim have matching picture evidence or an explicit gap?",
        ],
        "sound": [
            "Are dialogue, narration, ambience, music, original sound, and SFX roles clear?",
            "Are valuable reactions and ambience preserved across picture cuts?",
            "Where is measured audio analysis still required before changing gain or cleanup?",
        ],
    }[review_pass]


def create_review(
    manager: ProjectManager,
    review_pass: ReviewPass,
) -> dict[str, Any]:
    """Create one immutable external-review package, for at most three rounds."""

    document = manager.require_document()
    packages = _review_root(manager.project_dir) / "packages"
    existing_paths = sorted(packages.glob("review-*.json")) if packages.is_dir() else []
    existing: list[ReviewPackage] = []
    for path in existing_paths:
        try:
            existing.append(ReviewPackage.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    submitted_ids = _submitted_review_ids(manager.project_dir)
    current_project_sha = _document_sha256(document)
    current_evidence_sha = _evidence_sha256(manager.project_dir)
    for package in reversed(existing):
        if (
            package.id not in submitted_ids
            and package.review_pass == review_pass
            and package.project_id == document.project.id
            and package.project_revision == document.revision
            and package.project_sha256 == current_project_sha
            and package.evidence_sha256 == current_evidence_sha
        ):
            path = _package_path(manager.project_dir, package.id)
            return {**package.model_dump(mode="json"), "path": str(path.resolve()), "reused": True}
    completed_rounds = len(
        {package.id for package in existing if package.id in submitted_ids}
    )
    if completed_rounds >= 3:
        raise ReviewRequiredError(
            "The automatic Director Review Loop reached its three-round limit.",
            suggestion="Compare the three review branches and ask a person to choose or revise the edit.",
            details={"completed_rounds": completed_rounds, "max_rounds": 3},
        )
    round_number = completed_rounds + 1
    review_id = f"review-{round_number:02d}-{uuid4().hex[:8]}"
    package = ReviewPackage(
        id=review_id,
        review_pass=review_pass,
        round=round_number,
        created_at=datetime.now(timezone.utc),
        project_id=document.project.id,
        project_revision=document.revision,
        project_sha256=current_project_sha,
        evidence_sha256=current_evidence_sha,
        timeline=_timeline_snapshot(document),
        evidence=_review_evidence(manager.project_dir),
        questions=_questions(review_pass),
    )
    path = _atomic_write(
        _package_path(manager.project_dir, review_id), package.model_dump(mode="json")
    )
    return {**package.model_dump(mode="json"), "path": str(path.resolve()), "reused": False}


def submit_review(
    manager: ProjectManager,
    submission: str | Path | dict[str, Any],
) -> dict[str, Any]:
    """Validate and idempotently store an external review submission."""

    item = _load(submission, ReviewSubmission)
    assert isinstance(item, ReviewSubmission)
    package_path = _package_path(manager.project_dir, item.review_id)
    if not package_path.is_file():
        raise InvalidArgumentError(f'Review package "{item.review_id}" was not found.')
    package = ReviewPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    expected = (
        package.project_id,
        package.project_revision,
        package.project_sha256,
        package.evidence_sha256,
    )
    actual = (
        item.project_id,
        item.project_revision,
        item.project_sha256,
        item.evidence_sha256,
    )
    if actual != expected:
        raise InvalidArgumentError(
            "Review submission does not match its immutable project/evidence package.",
            suggestion="Create a new review package from the current project and resubmit findings.",
        )
    known_observations = {
        str(entry.get("observation_id"))
        for entry in package.evidence
        if entry.get("observation_id")
    }
    known_clips = {
        str(clip.get("id"))
        for track in package.timeline.get("tracks", [])
        for clip in track.get("clips", [])
        if clip.get("id")
    }
    for finding in item.findings:
        if finding.edits and not finding.evidence:
            raise InvalidArgumentError(
                f'Review finding "{finding.id}" proposes edits without evidence references.'
            )
        for reference in finding.evidence:
            observation_id = reference.get("observation_id")
            clip_id = reference.get("clip_id")
            if observation_id in known_observations or clip_id in known_clips:
                continue
            raise InvalidArgumentError(
                f'Review finding "{finding.id}" references unknown evidence.',
                suggestion="Use an observation_id or clip_id included in the immutable review package.",
            )
    path = _review_root(manager.project_dir) / "submissions" / f"{item.id}.json"
    encoded = item.model_dump(mode="json")
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
        if _canonical(current) != _canonical(encoded):
            raise InvalidArgumentError(
                f'Review submission id "{item.id}" was already used for different content.'
            )
    else:
        _atomic_write(path, encoded)
    return {**encoded, "path": str(path.resolve())}


def plan_review(
    manager: ProjectManager,
    submission: str | Path | dict[str, Any],
) -> dict[str, Any]:
    """Compile external findings into an auditable, still-unapplied edit plan."""

    submitted = submit_review(manager, submission)
    item = ReviewSubmission.model_validate(
        {key: value for key, value in submitted.items() if key != "path"}
    )
    package = ReviewPackage.model_validate_json(
        _package_path(manager.project_dir, item.review_id).read_text(encoding="utf-8")
    )
    edits = [edit for finding in item.findings for edit in finding.edits]
    unsupported = sorted({edit.action for edit in edits if edit.action not in _SUPPORTED_ACTIONS})
    warnings = [f'Unsupported review action "{action}" cannot be applied.' for action in unsupported]
    status: Literal["ready", "review_required"] = (
        "review_required" if item.conclusion == "review_required" or unsupported else "ready"
    )
    plan = ReviewPlan(
        id=f"review-plan-{uuid4().hex[:12]}",
        review_id=item.review_id,
        submission_id=item.id,
        round=package.round,
        project_id=item.project_id,
        project_revision=item.project_revision,
        project_sha256=item.project_sha256,
        evidence_sha256=item.evidence_sha256,
        submission_sha256=hashlib.sha256(_canonical(item.model_dump(mode="json"))).hexdigest(),
        status=status,
        edits=edits,
        warnings=warnings,
    )
    path = _review_root(manager.project_dir) / "plans" / f"{plan.id}.json"
    _atomic_write(path, plan.model_dump(mode="json"))
    return {**plan.model_dump(mode="json"), "path": str(path.resolve())}


def apply_review(
    manager: ProjectManager,
    plan: str | Path | dict[str, Any],
    *,
    approved_only: bool = True,
) -> dict[str, Any]:
    """Apply eligible review edits as exactly one atomic CutGraph revision."""

    prepared = prepare_review_apply(
        manager, plan, approved_only=approved_only
    )

    def operation(candidate: ProjectDocument) -> list[dict[str, Any]]:
        return apply_prepared_review(candidate, prepared)

    applied, state = manager.mutate(
        "vlog.review.apply",
        f"Applied {len(prepared['edits'])} approved Director Review edit(s)",
        operation,
        command={
            "plan_id": prepared["plan_id"],
            "review_id": prepared["review_id"],
            "approved_only": approved_only,
            "_actor": "agent",
            "_intent": "Apply explicitly reviewed VLOG revisions",
        },
    )
    return {
        "plan_id": prepared["plan_id"],
        "review_id": prepared["review_id"],
        "round": prepared["round"],
        "applied": applied,
        "project_revision": state.revision,
        "requires_human_review_after_this_round": prepared["round"] >= 3,
    }


def prepare_review_apply(
    manager: ProjectManager,
    plan: str | Path | dict[str, Any],
    *,
    approved_only: bool = True,
) -> dict[str, Any]:
    """Resolve and validate a review plan before an atomic batch begins."""

    item = _load(plan, ReviewPlan)
    assert isinstance(item, ReviewPlan)
    document = manager.require_document()
    if (
        item.project_id != document.project.id
        or item.project_revision != document.revision
        or item.project_sha256 != _document_sha256(document)
        or item.evidence_sha256 != _evidence_sha256(manager.project_dir)
    ):
        raise InvalidArgumentError(
            "Review plan is stale for the current project or visual evidence.",
            suggestion="Create a new review package and regenerate the revision plan.",
        )
    selected = [
        edit
        for edit in item.edits
        if edit.status == "approved"
        or (not approved_only and edit.status != "rejected")
    ]
    if not selected:
        raise ReviewRequiredError(
            "No review edits are approved for application.",
            suggestion="Approve specific deterministic edits or keep the reviewed cut unchanged.",
        )
    unsupported = sorted({edit.action for edit in selected if edit.action not in _SUPPORTED_ACTIONS})
    if unsupported:
        raise ReviewRequiredError(
            "The review plan contains edits that FACUT cannot execute deterministically.",
            suggestion="Replace unsupported edits with supported timeline operations or apply them manually.",
            details={"unsupported_actions": unsupported},
        )

    return {
        "action": "vlog.review.apply.prepared",
        "plan_id": item.id,
        "review_id": item.review_id,
        "submission_id": item.submission_id,
        "round": item.round,
        "approved_only": approved_only,
        "edits": [edit.model_dump(mode="json") for edit in selected],
    }


def apply_prepared_review(
    candidate: ProjectDocument, command: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply a validated review command to one in-memory transaction state."""

    applied: list[dict[str, Any]] = []
    edits = [ReviewEdit.model_validate(item) for item in command["edits"]]
    for edit in edits:
        result = CommandEngine.apply(
            candidate,
            {"action": edit.action, **edit.parameters},
        )
        applied.append(
            {
                "id": edit.id,
                "action": edit.action,
                "result": _jsonable(result),
            }
        )
    candidate.settings.setdefault("director_review_applications", []).append(
        {
            "plan_id": command["plan_id"],
            "review_id": command["review_id"],
            "submission_id": command["submission_id"],
            "round": command["round"],
            "approved_only": command["approved_only"],
            "edit_ids": [edit.id for edit in edits],
        }
    )
    return applied


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value
