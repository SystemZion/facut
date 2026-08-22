"""Persistent, evidence-backed VLOG preparation and StoryGraph planning."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from facut.core.models import (
    Clip,
    Effect,
    MediaKind,
    ProjectDocument,
    TextOverlay,
    Track,
    TrackType,
    Transition,
)
from facut.core.project_manager import ProjectManager
from facut.media.thumbnail import generate_thumbnail
from facut.media.importer import SUPPORTED_EXTENSIONS
from facut.media.proxy_manager import is_probable_proxy_path

from .models import EvidenceObservation, StoryCandidate, StoryPlan, StorySegment


QUALITY_WEIGHTS = {
    "story_coherence": 0.25,
    "shot_quality": 0.20,
    "evidence_reliability": 0.20,
    "continuity": 0.15,
    "sound_value": 0.10,
    "style_fit": 0.10,
}

STAGES: dict[str, tuple[str, ...]] = {
    "opening": ("hook", "reaction", "arrival", "aerial", "日出", "开场"),
    "setup": ("departure", "transport", "airport", "train", "出发", "抵达"),
    "exploration": ("explore", "street", "food", "museum", "景点", "游览", "美食"),
    "change": ("surprise", "problem", "fall", "lost", "意外", "摔倒", "迷路", "变化"),
    "climax": ("climax", "reaction", "sunset", "aerial", "高潮", "惊喜"),
    "reflection": ("farewell", "family", "night", "reflection", "告别", "回味", "全家福"),
}


def _root(project_dir: str | Path) -> Path:
    path = Path(project_dir) / "cache" / "vlog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_times(duration: float | None) -> list[float]:
    if not duration or duration <= 0:
        return [0.0]
    if duration < 1.5:
        return [max(0.0, duration * 0.5)]
    margin = min(0.5, duration * 0.1)
    return [margin, duration * 0.5, max(margin, duration - margin)]


def import_source_resumable(
    manager: ProjectManager,
    source: str | Path,
    *,
    batch_size: int = 25,
) -> dict[str, Any]:
    """Import a large tree in committed batches and isolate individual failures."""

    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'VLOG media root "{root}" was not found.')
    files = sorted(
        (
            item.resolve()
            for item in root.rglob("*")
            if item.is_file()
            and item.suffix.casefold() in SUPPORTED_EXTENSIONS
            and not is_probable_proxy_path(item)
        ),
        key=lambda item: str(item).casefold(),
    )
    if not files:
        raise ValueError("No supported original media files were found.")
    existing = {
        os.path.normcase(str(manager.resolve_path(item.path))): item
        for item in manager.require_document().media
    }
    pending = [
        item
        for item in files
        if os.path.normcase(str(item)) not in existing
        or existing[os.path.normcase(str(item))].size != item.stat().st_size
    ]
    imported_ids: list[str] = []
    failures: list[dict[str, Any]] = []
    for offset in range(0, len(pending), max(1, batch_size)):
        batch = pending[offset : offset + max(1, batch_size)]
        try:
            imported_ids.extend(
                item.id for item in manager.import_paths(batch, recursive=False)
            )
        except Exception:
            for path in batch:
                try:
                    imported_ids.extend(
                        item.id for item in manager.import_paths([path], recursive=False)
                    )
                except Exception as error:
                    failures.append(
                        {
                            "path": str(path),
                            "reason": "import_failed",
                            "error": str(error),
                        }
                    )
        _write_json(
            _root(manager.project_dir) / "import-state.json",
            {
                "version": "1.0",
                "source": str(root),
                "discovered": len(files),
                "already_imported": len(files) - len(pending),
                "completed_pending": min(offset + len(batch), len(pending)),
                "imported_ids": imported_ids,
                "failures": failures,
            },
        )
    result = {
        "source": str(root),
        "discovered": len(files),
        "already_imported": len(files) - len(pending),
        "newly_imported": len(set(imported_ids)),
        "failures": failures,
    }
    _write_json(_root(manager.project_dir) / "import-state.json", {"version": "1.0", **result})
    return result


def prepare_evidence_manifest(
    manager: ProjectManager,
    *,
    ffmpeg: str | Path | None = None,
    generate_frames: bool = True,
    batch_size: int = 12,
    native_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create baseline frame coverage and resumable external-AI inspection tasks."""

    document = manager.require_document()
    root = _root(manager.project_dir)
    frames_root = root / "frames"
    existing = _read_json(root / "manifest.json", {})
    previous = {item["media_id"]: item for item in existing.get("assets", [])}
    assets: list[dict[str, Any]] = []
    import_state = _read_json(root / "import-state.json", {"failures": []})
    excluded: list[dict[str, Any]] = list(import_state.get("failures", []))
    for asset in document.media:
        if asset.kind not in {MediaKind.VIDEO, MediaKind.IMAGE}:
            continue
        original = manager.resolve_path(asset.path)
        if not original.is_file() or original.stat().st_size <= 0:
            excluded.append(
                {"media_id": asset.id, "reason": "missing_or_empty", "path": asset.path}
            )
            continue
        source = original
        source_kind = "original"
        if asset.proxy_path:
            proxy = manager.resolve_path(asset.proxy_path)
            if proxy.is_file() and proxy.stat().st_size > 0:
                source = proxy
                source_kind = "proxy"
        cached = previous.get(asset.id)
        if cached and cached.get("sha256") == asset.sha256 and all(
            Path(item["path"]).is_file() for item in cached.get("representative_frames", [])
        ):
            assets.append(cached)
            continue
        representative: list[dict[str, Any]] = []
        native_result = (native_results or {}).get(asset.id)
        if native_result and native_result.get("status") == "success":
            representative = [
                {
                    "at": round(float(item.get("actual_seconds", item.get("requested_seconds", 0))), 6),
                    "requested_at": round(float(item.get("requested_seconds", 0)), 6),
                    "path": str(Path(item["frame"]).resolve()),
                    "exists": Path(item["frame"]).is_file(),
                    "quality": {
                        "luminance_mean": item.get("luminance_mean"),
                        "sharpness": item.get("sharpness"),
                        "motion": item.get("motion"),
                    },
                    "perceptual_hash": item.get("perceptual_hash"),
                }
                for item in native_result.get("representative_frames", [])
            ]
        else:
            times = _sample_times(asset.technical.duration)
            for index, at in enumerate(times, start=1):
                output = frames_root / asset.id / f"baseline-{index:02d}.jpg"
                if generate_frames and not output.is_file():
                    generate_thumbnail(
                        source,
                        output,
                        at=at,
                        width=480,
                        ffmpeg=ffmpeg,
                        overwrite=False,
                    )
                representative.append(
                    {"at": round(at, 6), "path": str(output.resolve()), "exists": output.is_file()}
                )
        assets.append(
            {
                "media_id": asset.id,
                "name": asset.original_name,
                "kind": asset.kind.value,
                "sha256": asset.sha256,
                "duration": asset.technical.duration,
                "width": asset.technical.width,
                "height": asset.technical.height,
                "rotation": asset.technical.rotation,
                "creation_time": asset.technical.creation_time,
                "location": {
                    "latitude": asset.technical.latitude,
                    "longitude": asset.technical.longitude,
                },
                "source_kind": source_kind,
                "analysis_engine": native_result.get("engine") if native_result else {"name": "python"},
                "native_fingerprint": native_result.get("fingerprint") if native_result else None,
                "waveform": native_result.get("waveform") if native_result else None,
                "representative_frames": representative,
                "baseline_coverage": "complete" if all(item["exists"] for item in representative) else "metadata_only",
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps([(item["media_id"], item["sha256"]) for item in assets]).encode()
    ).hexdigest()
    manifest = {
        "version": "2.0",
        "project_revision": document.revision,
        "document_fingerprint": fingerprint,
        "quality_policy": "quality-first",
        "assets": assets,
        "excluded": excluded,
    }
    _write_json(root / "manifest.json", manifest)
    observations = _read_json(root / "observations.json", {"observations": []})
    observed_media = {item["media_id"] for item in observations.get("observations", [])}
    tasks: list[dict[str, Any]] = []
    pending_assets = [item for item in assets if item["media_id"] not in observed_media]
    for offset in range(0, len(pending_assets), max(1, batch_size)):
        batch = pending_assets[offset : offset + max(1, batch_size)]
        task_id = f"inspect_{offset // max(1, batch_size) + 1:04d}"
        tasks.append(
            {
                "task_id": task_id,
                "status": "pending",
                "purpose": "Baseline visual coverage. Inspect every supplied frame; request deeper evidence when uncertain.",
                "assets": batch,
                "required_output_schema": EvidenceObservation.model_json_schema(),
                "minimum_observations": len(batch),
            }
        )
    _write_json(root / "tasks.json", {"version": "1.0", "tasks": tasks})
    from .context import rebuild_director_inbox

    inbox = rebuild_director_inbox(manager.project_dir)
    return {
        "manifest": str((root / "manifest.json").resolve()),
        "asset_count": len(assets),
        "excluded_count": len(excluded),
        "pending_tasks": len(tasks),
        "pending_assets": len(pending_assets),
        "baseline_coverage": "complete" if all(item["baseline_coverage"] == "complete" for item in assets) else "partial",
        "director_inbox": {"pending": inbox["pending"], "path": inbox["path"]},
    }


def next_inspection_task(project_dir: str | Path) -> dict[str, Any]:
    root = _root(project_dir)
    payload = _read_json(root / "tasks.json")
    if payload is None:
        raise FileNotFoundError("VLOG inspection tasks were not found. Run `facut vlog prepare` first.")
    for task in payload["tasks"]:
        if task["status"] == "pending":
            return {**task, "remaining_tasks": sum(item["status"] == "pending" for item in payload["tasks"])}
    return {"status": "complete", "remaining_tasks": 0, "assets": []}


def ingest_observations(
    document: ProjectDocument,
    project_dir: str | Path,
    payload: dict[str, Any],
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    root = _root(project_dir)
    known = {item.id: item for item in document.media}
    raw_items = payload.get("observations", payload.get("items"))
    if not isinstance(raw_items, list):
        raise ValueError('Observation input requires an "observations" list.')
    adapter = TypeAdapter(list[EvidenceObservation])
    observations = adapter.validate_python(raw_items)
    for item in observations:
        asset = known.get(item.media_id)
        if asset is None:
            raise ValueError(f'Observation references unknown media "{item.media_id}".')
        duration = asset.technical.duration
        if duration is not None and item.range.end > duration + 1e-6:
            raise ValueError(
                f'Observation {item.observation_id} exceeds media duration {duration:.3f}s.'
            )
    stored = _read_json(root / "observations.json", {"version": "2.0", "observations": []})
    by_id = {item["observation_id"]: item for item in stored["observations"]}
    inserted = 0
    unchanged = 0
    for item in observations:
        serialized = item.model_dump(mode="json")
        previous = by_id.get(item.observation_id)
        if previous == serialized:
            unchanged += 1
        else:
            by_id[item.observation_id] = serialized
            inserted += 1
    output = {"version": "2.0", "observations": sorted(by_id.values(), key=lambda item: item["observation_id"])}
    _write_json(root / "observations.json", output)
    tasks = _read_json(root / "tasks.json", {"version": "1.0", "tasks": []})
    observed_media = {item.media_id for item in observations}
    all_observed_media = {item["media_id"] for item in output["observations"]}
    matched_task = None
    for task in tasks["tasks"]:
        task_media = {item["media_id"] for item in task["assets"]}
        selected_task = task_id and task["task_id"] == task_id
        inferred_task = not task_id and task_media and task_media <= observed_media
        if (selected_task or inferred_task) and task_media <= all_observed_media:
            task["status"] = "complete"
            matched_task = task["task_id"]
    _write_json(root / "tasks.json", tasks)
    from .context import rebuild_director_inbox

    inbox = rebuild_director_inbox(project_dir)
    return {
        "inserted_or_updated": inserted,
        "unchanged": unchanged,
        "observation_count": len(output["observations"]),
        "completed_task": matched_task,
        "remaining_tasks": sum(item["status"] == "pending" for item in tasks["tasks"]),
        "path": str((root / "observations.json").resolve()),
        "director_inbox_pending": inbox["pending"],
    }


def director_status(
    project_dir: str | Path, document: ProjectDocument | None = None
) -> dict[str, Any]:
    root = _root(project_dir)
    manifest = _read_json(root / "manifest.json", {"assets": [], "excluded": []})
    tasks = _read_json(root / "tasks.json", {"tasks": []})
    observations = _read_json(root / "observations.json", {"observations": []})
    story = _read_json(root / "story.plan.json")
    pending = [item for item in tasks["tasks"] if item["status"] == "pending"]
    applied = {} if document is None else document.settings.get("vlog_director", {})
    delivery = applied.get("delivery", {}) if isinstance(applied, dict) else {}
    if delivery.get("status") == "pass":
        stage, next_command = "delivered", "facut qc <output>"
    elif delivery:
        stage, next_command = "built", "facut qc <output>"
    elif applied.get("candidate_id"):
        stage, next_command = "applied", "facut render --output <output>"
    elif not manifest["assets"]:
        stage, next_command = "not_prepared", "facut vlog prepare <source>"
    elif pending:
        stage, next_command = "inspection", "facut vlog inspect next"
    elif not story:
        stage, next_command = "ready_to_plan", "facut vlog plan --style natural-vlog"
    elif story.get("status") != "ready":
        stage, next_command = "story_review", "facut vlog compare"
    else:
        stage, next_command = "ready_to_build", f"facut vlog build {story.get('selected_candidate_id') or '<candidate-id>'}"
    result = {
        "stage": stage,
        "asset_count": len(manifest["assets"]),
        "excluded_count": len(manifest["excluded"]),
        "observation_count": len(observations["observations"]),
        "pending_tasks": len(pending),
        "next_command": next_command,
        "quality_policy": "quality-first",
    }
    from .context import load_trip_bible, rebuild_director_inbox

    inbox = rebuild_director_inbox(project_dir, persist=False)
    bible = load_trip_bible(project_dir)
    result["director_inbox"] = {"pending": inbox["pending"], "total": inbox["total"]}
    result["trip_bible"] = {
        "trip_name": bible.trip_name,
        "confirmed_facts": sum(item.status == "confirmed" for item in bible.facts),
        "uncertain_facts": sum(item.status == "uncertain" for item in bible.facts),
    }
    if applied.get("candidate_id"):
        result["candidate_id"] = applied["candidate_id"]
    if delivery:
        result["delivery"] = delivery
    from .atlas import scene_atlas_status

    atlas = scene_atlas_status(project_dir)
    result["scene_atlas"] = atlas
    if (
        atlas.get("stage") == "inspection"
        and stage not in {"applied", "built", "delivered"}
    ):
        result["stage"] = "atlas_inspection"
        result["pending_tasks"] = atlas["pending_tasks"]
        result["next_command"] = atlas["next_command"]
    review_root = root / "reviews"
    review_packages = list((review_root / "packages").glob("*.json"))
    review_plans = list((review_root / "plans").glob("*.json"))
    result["director_review"] = {
        "rounds_created": len(review_packages),
        "maximum_rounds": 3,
        "revision_plans": len(review_plans),
        "applications": len(
            document.settings.get("director_review_applications", [])
            if document is not None
            else []
        ),
    }
    soundscape_root = root / "soundscape"
    result["soundscape"] = {
        "analysis_available": (soundscape_root / "analysis.json").is_file(),
        "plans": len(list((soundscape_root / "plans").glob("*.json"))),
        "applications": len(
            document.settings.get("soundscape_applications", [])
            if document is not None
            else []
        ),
    }
    return result


def _observation_text(item: EvidenceObservation) -> str:
    return " ".join(
        [item.summary, *item.entities, *item.actions, *item.tags, item.shot_type or "", item.daypart or "", item.location or ""]
    ).casefold()


def _stage(item: EvidenceObservation) -> str:
    if item.story_role == "incident":
        return "change"
    if item.story_role in {"recovery", "outcome"}:
        return "climax"
    text = _observation_text(item)
    scored = [(stage, sum(token.casefold() in text for token in tokens)) for stage, tokens in STAGES.items()]
    selected, score = max(scored, key=lambda pair: pair[1])
    return selected if score else "exploration"


def _rank(item: EvidenceObservation, strategy: str, style: str) -> float:
    audio = {"unknown": 0.35, "low": 0.2, "medium": 0.65, "high": 1.0}[item.original_audio_value]
    style_text = _observation_text(item)
    comedy = any(token in style_text for token in ("funny", "laugh", "fall", "搞笑", "笑", "摔倒", "意外"))
    if strategy == "narrative":
        value = item.confidence * 0.45 + item.quality * 0.3 + audio * 0.25
    elif strategy == "immersive":
        value = audio * 0.45 + item.quality * 0.35 + item.confidence * 0.2
    else:
        value = item.quality * 0.55 + item.confidence * 0.35 + audio * 0.1
    if style == "comedy-vlog" and comedy:
        value += 0.12
    return min(value, 1.0)


def _candidate(
    observations: list[EvidenceObservation], strategy: str, target_duration: float, style: str
) -> StoryCandidate:
    stage_order = list(STAGES)
    per_stage = max(2.0, target_duration / len(stage_order))
    selected_ids: set[str] = set()
    segments: list[StorySegment] = []
    missing: list[str] = []
    cursor = 0.0
    for stage in stage_order:
        candidates = [item for item in observations if _stage(item) == stage]
        candidates.sort(key=lambda item: (-_rank(item, strategy, style), item.range.start))
        used = 0.0
        if not candidates:
            missing.append(stage)
        for item in candidates:
            if item.observation_id in selected_ids or used >= per_stage:
                continue
            available = item.range.end - item.range.start
            duration = min(available, max(1.5, min(8.0, per_stage - used)))
            if duration <= 0:
                continue
            alternatives = [other.observation_id for other in candidates if other.observation_id != item.observation_id][:3]
            score = _rank(item, strategy, style)
            segments.append(
                StorySegment(
                    stage=stage,
                    media_id=item.media_id,
                    source_in=item.range.start,
                    source_out=item.range.start + duration,
                    timeline_start=cursor,
                    duration=duration,
                    observation_id=item.observation_id,
                    reason=f"Selected for {stage} by {strategy} strategy; evidence score {score:.3f}.",
                    confidence=item.confidence,
                    evidence_frames=item.evidence_frames,
                    preserve_original_audio=item.original_audio_value == "high",
                    alternatives=alternatives,
                )
            )
            selected_ids.add(item.observation_id)
            cursor += duration
            used += duration
    observation_by_id = {item.observation_id: item for item in observations}

    # Explicit event chains are hard story constraints.  If an incident has a
    # recovery/outcome, keep the complete same-subject sequence and replace an
    # unrelated lower-ranked shot in that stage instead of dropping resolution.
    chains: dict[str, list[EvidenceObservation]] = {}
    for item in observations:
        if item.event_chain:
            chains.setdefault(item.event_chain, []).append(item)
    required_chain_ids: set[str] = set()
    for chain_items in chains.values():
        roles = {item.story_role for item in chain_items}
        if "incident" not in roles or not roles.intersection({"recovery", "outcome"}):
            continue
        ordered = sorted(
            chain_items,
            key=lambda item: (
                item.event_order if item.event_order is not None else 10**9,
                item.range.start,
            ),
        )
        for evidence in ordered:
            required_chain_ids.add(evidence.observation_id)
            if evidence.observation_id in selected_ids:
                continue
            stage = _stage(evidence)
            replaceable = [
                segment
                for segment in segments
                if segment.stage == stage and segment.observation_id not in required_chain_ids
            ]
            if replaceable:
                victim = min(
                    replaceable,
                    key=lambda segment: _rank(
                        observation_by_id[segment.observation_id], strategy, style
                    ),
                )
                segments.remove(victim)
                selected_ids.discard(victim.observation_id)
            duration = min(evidence.range.end - evidence.range.start, 8.0)
            stage_candidates = [item for item in observations if _stage(item) == stage]
            segments.append(
                StorySegment(
                    stage=stage,
                    media_id=evidence.media_id,
                    source_in=evidence.range.start,
                    source_out=evidence.range.start + duration,
                    timeline_start=0,
                    duration=duration,
                    observation_id=evidence.observation_id,
                    reason=(
                        f"Required event-chain beat {evidence.event_chain} "
                        f"({evidence.story_role}, order {evidence.event_order})."
                    ),
                    confidence=evidence.confidence,
                    evidence_frames=evidence.evidence_frames,
                    preserve_original_audio=evidence.original_audio_value == "high",
                    alternatives=[
                        item.observation_id
                        for item in stage_candidates
                        if item.observation_id != evidence.observation_id
                    ][:3],
                )
            )
            selected_ids.add(evidence.observation_id)

    # Stage quotas preserve the intended story shape, but sparse stages must
    # not make a requested long-form edit silently come out much shorter. Once
    # every stage has had its first pass and explicit event chains are complete,
    # redistribute unused duration to the best remaining evidence. The final
    # item is trimmed to the requested duration so downstream code never needs
    # to pad or repeat footage merely to meet the requested runtime.
    selected_duration = sum(segment.duration for segment in segments)
    for segment in sorted(
        segments,
        key=lambda item: -_rank(observation_by_id[item.observation_id], strategy, style),
    ):
        remaining = target_duration - selected_duration
        if remaining <= 1e-6:
            break
        evidence = observation_by_id[segment.observation_id]
        maximum = min(evidence.range.end - evidence.range.start, 8.0)
        extra = min(maximum - segment.duration, remaining)
        if extra <= 0:
            continue
        segment.duration += extra
        segment.source_out += extra
        selected_duration += extra

    required_chain_stages = {
        _stage(observation_by_id[item_id]) for item_id in required_chain_ids
    }
    required_chain_subjects = {
        observation_by_id[item_id].subject_id
        for item_id in required_chain_ids
        if observation_by_id[item_id].subject_id
    }
    remaining_evidence = [
        item
        for item in observations
        if item.observation_id not in selected_ids
        and not (
            _stage(item) in required_chain_stages
            and item.subject_id not in required_chain_subjects
            and any(
                token in _observation_text(item)
                for token in ("reaction", "反应", "恢复", "recovery")
            )
        )
    ]
    remaining_evidence.sort(
        key=lambda item: (
            -_rank(item, strategy, style),
            stage_order.index(_stage(item)),
            item.range.start,
        )
    )
    for evidence in remaining_evidence:
        remaining = target_duration - selected_duration
        if remaining <= 1e-6:
            break
        available = evidence.range.end - evidence.range.start
        duration = min(available, 8.0, remaining)
        if duration <= 0:
            continue
        stage = _stage(evidence)
        stage_candidates = [item for item in observations if _stage(item) == stage]
        segments.append(
            StorySegment(
                stage=stage,
                media_id=evidence.media_id,
                source_in=evidence.range.start,
                source_out=evidence.range.start + duration,
                timeline_start=0,
                duration=duration,
                observation_id=evidence.observation_id,
                reason=(
                    "Selected while redistributing unused story-stage duration; "
                    f"evidence score {_rank(evidence, strategy, style):.3f}."
                ),
                confidence=evidence.confidence,
                evidence_frames=evidence.evidence_frames,
                preserve_original_audio=evidence.original_audio_value == "high",
                alternatives=[
                    item.observation_id
                    for item in stage_candidates
                    if item.observation_id != evidence.observation_id
                ][:3],
            )
        )
        selected_ids.add(evidence.observation_id)
        selected_duration += duration

    chain_order = {
        item.observation_id: item.event_order if item.event_order is not None else 10**9
        for items in chains.values()
        for item in items
    }
    segments.sort(
        key=lambda segment: (
            stage_order.index(segment.stage),
            chain_order.get(segment.observation_id, 10**8),
            segment.source_in,
        )
    )
    cursor = 0.0
    for segment in segments:
        segment.timeline_start = cursor
        cursor += segment.duration

    chain_issues: list[dict[str, Any]] = []
    for chain_name, chain_items in chains.items():
        roles = {item.story_role for item in chain_items}
        if "incident" not in roles:
            continue
        subjects = {item.subject_id for item in chain_items if item.subject_id}
        orders = [item.event_order for item in chain_items]
        if not roles.intersection({"recovery", "outcome"}):
            chain_issues.append(
                {
                    "code": "EVENT_CHAIN_INCOMPLETE",
                    "severity": "error",
                    "event_chain": chain_name,
                    "message": "An incident has no recovery or outcome evidence.",
                }
            )
        elif len(subjects) > 1 or any(order is None for order in orders) or len(set(orders)) != len(orders):
            chain_issues.append(
                {
                    "code": "EVENT_CHAIN_AMBIGUOUS",
                    "severity": "error",
                    "event_chain": chain_name,
                    "message": "Subject identity or event ordering is ambiguous.",
                }
            )

    mean_score = sum(_rank(item, strategy, style) for item in observations if item.observation_id in selected_ids)
    mean_score = mean_score / max(1, len(selected_ids))
    names = {"narrative": "叙事完整版", "immersive": "沉浸体验版", "visual": "视觉情绪版"}
    transitions = []
    effects = []
    for index, segment in enumerate(segments):
        evidence = observation_by_id[segment.observation_id]
        text = _observation_text(evidence)
        if index:
            previous = segments[index - 1]
            transition = "hard-cut"
            reason = "Hard cut preserves continuity and avoids decorative transitions."
            if previous.stage != segment.stage:
                previous_evidence = observation_by_id[previous.observation_id]
                previous_location = previous_evidence.location_id or previous_evidence.location
                current_location = evidence.location_id or evidence.location
                if previous_location and current_location and previous_location != current_location:
                    transition, reason = (
                        "location-card",
                        "Confirmed location changes; use a restrained chapter/location bridge.",
                    )
                elif strategy == "visual":
                    transition, reason = (
                        "restrained-dissolve",
                        "The visual-emotion strategy changes story stages without a confirmed location jump.",
                    )
            elif (
                evidence.camera_motion
                and observation_by_id[previous.observation_id].camera_motion == evidence.camera_motion
            ):
                transition, reason = "movement-match", "Adjacent evidence supports a motion-related cut."
            transitions.append(
                {
                    "between": [previous.observation_id, segment.observation_id],
                    "type": transition,
                    "reason": reason,
                    "confidence": min(previous.confidence, segment.confidence),
                    "render_status": "planned" if transition != "hard-cut" else "rendered",
                }
            )
        comedic = any(token in text for token in ("funny", "laugh", "fall", "搞笑", "笑", "摔倒", "迷路", "失败"))
        if style == "comedy-vlog" and comedic:
            effect = "freeze-repeat" if any(token in text for token in ("fall", "摔倒", "失败")) else "punch-zoom"
            effects.append(
                {
                    "observation_id": segment.observation_id,
                    "effect": effect,
                    "reason": "Evidence contains a complete comedic action or reaction.",
                    "confidence": evidence.confidence,
                    "fallback": "punch-zoom",
                    "render_status": "review-required",
                }
            )
    return StoryCandidate(
        id=f"candidate-{strategy}",
        name=names[strategy],
        strategy=strategy,
        score=round(max(0.0, mean_score - len(missing) * 0.04), 4),
        estimated_duration=round(cursor, 6),
        segments=segments,
        missing_stages=missing,
        unresolved_gaps=[
            *[{"code": "MISSING_STAGE", "stage": stage} for stage in missing],
            *chain_issues,
        ],
        continuity={
            "status": "review_required",
            "checked_fields": ["location", "daypart", "entities", "subject_id", "event_chain"],
        },
        sound_strategy={"preserve_original_audio_segments": sum(item.preserve_original_audio for item in segments)},
        subtitle_strategy={"dialogue": "readable-unified", "titles": "content-adaptive"},
        polish_plan={
            "transition_intents": transitions,
            "effect_intents": effects,
            "music_query": {
                "mood": "playful" if style == "comedy-vlog" else "travel",
                "license_required": True,
                "selection_status": "library-match-required",
            },
            "sfx_queries": ["comedy,impact"] if effects else [],
            "policy": "Intent records are reviewable; unsupported effects are never reported as rendered.",
        },
    )


def build_story_candidates(
    document: ProjectDocument,
    project_dir: str | Path,
    *,
    style: str = "natural-vlog",
    target_duration: float = 480.0,
) -> StoryPlan:
    from facut.styles import describe_style

    describe_style(style)
    root = _root(project_dir)
    raw = _read_json(root / "observations.json", {"observations": []})
    observations = TypeAdapter(list[EvidenceObservation]).validate_python(raw["observations"])
    if not observations:
        raise ValueError(
            "No external visual observations are available. Complete VLOG inspection before planning."
        )
    manifest = _read_json(root / "manifest.json", {"assets": []})
    observed_media = {item.media_id for item in observations}
    uncovered = [item["media_id"] for item in manifest["assets"] if item["media_id"] not in observed_media]
    if uncovered:
        raise ValueError(
            f"Visual coverage is incomplete for {len(uncovered)} asset(s); quality-first planning cannot skip them."
        )
    evidence_bytes = (root / "observations.json").read_bytes()
    candidates = [
        _candidate(observations, strategy, target_duration, style)
        for strategy in ("narrative", "immersive", "visual")
    ]
    from .context import load_trip_bible, trip_bible_fact_policy, trip_bible_sha256

    bible = load_trip_bible(project_dir, default_name=document.project.name)
    fact_policy = trip_bible_fact_policy(bible)
    for candidate in candidates:
        candidate.polish_plan["trip_bible"] = fact_policy
    warnings = sorted(
        {f"Candidate {item.id} lacks stage {stage}." for item in candidates for stage in item.missing_stages}
    )
    if fact_policy["withheld_uncertain_facts"]:
        warnings.append(
            f"Trip Bible withholds {len(fact_policy['withheld_uncertain_facts'])} uncertain fact(s) from generated claims."
        )
    plan = StoryPlan(
        project_revision=document.revision,
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        trip_bible_sha256=trip_bible_sha256(bible),
        style=style,
        target_duration=target_duration,
        candidates=candidates,
        quality_weights=QUALITY_WEIGHTS,
        warnings=warnings,
    )
    _write_json(root / "story.plan.json", plan.model_dump(mode="json"))
    return plan


def compare_story_candidates(project_dir: str | Path) -> dict[str, Any]:
    raw = _read_json(_root(project_dir) / "story.plan.json")
    if raw is None:
        raise FileNotFoundError("Story plan was not found. Run `facut vlog plan` first.")
    plan = StoryPlan.model_validate(raw)
    sets = {item.id: {segment.observation_id for segment in item.segments} for item in plan.candidates}
    differences: list[dict[str, Any]] = []
    for left_index, left in enumerate(plan.candidates):
        for right in plan.candidates[left_index + 1 :]:
            differences.append(
                {
                    "left": left.id,
                    "right": right.id,
                    "shared": len(sets[left.id] & sets[right.id]),
                    "only_left": sorted(sets[left.id] - sets[right.id]),
                    "only_right": sorted(sets[right.id] - sets[left.id]),
                    "duration_delta": round(left.estimated_duration - right.estimated_duration, 6),
                    "score_delta": round(left.score - right.score, 4),
                }
            )
    return {"style": plan.style, "candidates": [item.model_dump(mode="json") for item in plan.candidates], "differences": differences}


def _assert_story_context_current(
    plan: StoryPlan,
    project_dir: str | Path,
    *,
    candidate_id: str | None = None,
) -> None:
    """Refuse plans whose project, evidence, or factual policy is stale."""

    from .context import load_trip_bible, trip_bible_sha256

    planned_name = next(
        (
            str(item.polish_plan.get("trip_bible", {}).get("trip_name"))
            for item in plan.candidates
            if item.polish_plan.get("trip_bible", {}).get("trip_name")
        ),
        "Untitled trip",
    )
    current = trip_bible_sha256(load_trip_bible(project_dir, default_name=planned_name))
    if plan.trip_bible_sha256 != current:
        raise ValueError(
            "The Trip Bible changed after this StoryGraph plan was generated. "
            "Run `facut vlog plan` again before refining or applying a candidate."
        )
    evidence_path = _root(project_dir) / "observations.json"
    evidence_sha256 = hashlib.sha256(
        evidence_path.read_bytes() if evidence_path.is_file() else b""
    ).hexdigest()
    if plan.evidence_sha256 != evidence_sha256:
        raise ValueError(
            "Visual evidence changed after this StoryGraph plan was generated. "
            "Run `facut vlog story brief` and submit or generate a new plan."
        )
    manager = ProjectManager(project_dir)
    document = manager.load()
    if plan.project_revision == document.revision:
        return
    applied = document.settings.get("vlog_director", {})
    expected_revision = int(applied.get("applied_revision", plan.project_revision + 1))
    already_applied = (
        candidate_id is not None
        and applied.get("candidate_id") == candidate_id
        and applied.get("evidence_sha256") == plan.evidence_sha256
        and applied.get("trip_bible_sha256") == plan.trip_bible_sha256
        and document.revision == expected_revision
    )
    if not already_applied:
        raise ValueError(
            "The project changed after this StoryGraph plan was generated. "
            "Create a new plan before replacing the current timeline."
        )


def refine_story_candidate(project_dir: str | Path, candidate_id: str) -> StoryPlan:
    path = _root(project_dir) / "story.plan.json"
    plan = StoryPlan.model_validate(_read_json(path))
    _assert_story_context_current(plan, project_dir, candidate_id=candidate_id)
    candidate = next((item for item in plan.candidates if item.id == candidate_id), None)
    if candidate is None:
        raise ValueError(f'Unknown story candidate "{candidate_id}".')
    severe = [item for item in candidate.unresolved_gaps if item.get("severity") == "error"]
    candidate.continuity["status"] = "pass" if not severe else "review_required"
    plan.selected_candidate_id = candidate_id
    plan.status = "ready" if candidate.segments and not severe else "review_required"
    _write_json(path, plan.model_dump(mode="json"))
    return plan


def candidate_document(
    document: ProjectDocument, project_dir: str | Path, candidate_id: str
) -> tuple[ProjectDocument, StoryCandidate]:
    """Materialize one candidate into an isolated in-memory project document."""

    raw = _read_json(_root(project_dir) / "story.plan.json")
    if raw is None:
        raise FileNotFoundError("Story plan was not found. Run `facut vlog plan` first.")
    plan = StoryPlan.model_validate(raw)
    _assert_story_context_current(plan, project_dir, candidate_id=candidate_id)
    candidate = next((item for item in plan.candidates if item.id == candidate_id), None)
    if candidate is None:
        raise ValueError(f'Unknown story candidate "{candidate_id}".')
    copy = document.model_copy(deep=True)
    clips = [
        Clip(
            id=f"vlog_{candidate.strategy}_{index:04d}",
            media_id=item.media_id,
            track_id="V1",
            timeline_start=item.timeline_start,
            source_in=item.source_in,
            source_out=item.source_out,
            metadata={
                "story_stage": item.stage,
                "observation_id": item.observation_id,
                "selection_reason": item.reason,
                "preserve_original_audio": item.preserve_original_audio,
            },
        )
        for index, item in enumerate(candidate.segments, start=1)
    ]
    copy.tracks = [Track(id="V1", name=f"VLOG {candidate.name}", type=TrackType.VIDEO, clips=clips)]
    copy.transitions = []
    copy.text_overlays = []
    by_observation = {
        clip.metadata["observation_id"]: clip for clip in clips
    }
    rendered_effects: list[dict[str, Any]] = []
    for intent in candidate.polish_plan.get("effect_intents", []):
        clip = by_observation.get(intent.get("observation_id"))
        if clip is None:
            continue
        requested = str(intent.get("effect", ""))
        confidence = float(intent.get("confidence", 0.0))
        selected = requested
        fallback_used = False
        if requested not in {"punch-zoom", "micro-shake", "comic-impact"}:
            selected = str(intent.get("fallback", ""))
            fallback_used = True
        if selected not in {"punch-zoom", "micro-shake", "comic-impact"} or confidence < 0.75:
            intent["render_status"] = "review-required"
            continue
        parameters = {"intensity": 0.12} if selected == "punch-zoom" else {}
        clip.effects.append(Effect(type=selected, parameters=parameters))
        intent["render_status"] = "fallback" if fallback_used else "rendered"
        intent["rendered_effect"] = selected
        rendered_effects.append(
            {
                "clip_id": clip.id,
                "requested": requested,
                "rendered": selected,
                "fallback": fallback_used,
            }
        )

    # xfade transitions require a real overlap. Shift the target and all later
    # clips by the reviewed boundary duration; hard cuts and location cards do
    # not silently become decorative dissolves.
    clip_indexes = {clip.id: index for index, clip in enumerate(clips)}
    for intent in candidate.polish_plan.get("transition_intents", []):
        requested = str(intent.get("type", "hard-cut"))
        if requested == "hard-cut":
            intent["render_status"] = "rendered"
            continue
        if requested == "location-card":
            between = intent.get("between") or []
            target = by_observation.get(between[1]) if len(between) == 2 else None
            if target is None:
                intent["render_status"] = "review-required"
                continue
            stage = str(target.metadata.get("story_stage", "chapter"))
            labels = {
                "opening": "出发",
                "setup": "抵达",
                "exploration": "探索",
                "change": "途中插曲",
                "climax": "高光时刻",
                "reflection": "回味",
            }
            copy.text_overlays.append(
                TextOverlay(
                    text=labels.get(stage, stage),
                    at=target.timeline_start,
                    duration=min(2.0, target.duration),
                    x="center",
                    y="center",
                    entrance="fade",
                    exit="fade",
                    template="chapter",
                    metadata={
                        "source": "vlog-location-card",
                        "between": between,
                    },
                )
            )
            intent["render_status"] = "rendered"
            intent["rendered_as"] = "chapter-text-overlay"
            continue
        if requested not in {"movement-match", "restrained-dissolve"}:
            intent["render_status"] = "review-required"
            continue
        between = intent.get("between") or []
        if len(between) != 2:
            intent["render_status"] = "review-required"
            continue
        source = by_observation.get(between[0])
        target = by_observation.get(between[1])
        if source is None or target is None:
            intent["render_status"] = "review-required"
            continue
        target_index = clip_indexes[target.id]
        if target_index != clip_indexes[source.id] + 1:
            intent["render_status"] = "review-required"
            continue
        duration = min(
            0.3 if requested == "movement-match" else 0.45,
            source.duration / 3,
            target.duration / 3,
        )
        if duration <= 1 / copy.project.fps:
            intent["render_status"] = "review-required"
            continue
        for later in clips[target_index:]:
            later.timeline_start = max(0.0, later.timeline_start - duration)
        copy.transitions.append(
            Transition(
                type=requested,
                duration=duration,
                from_clip_id=source.id,
                to_clip_id=target.id,
                track_id="V1",
                at=target.timeline_start,
            )
        )
        intent["render_status"] = "rendered"
        intent["duration"] = duration
    if rendered_effects:
        copy.settings["vlog_polish"] = {"effects": rendered_effects}
    copy.subtitle_cues = []
    copy.markers = []
    copy.recompute_duration()
    return copy, candidate
