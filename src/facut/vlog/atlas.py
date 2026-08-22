"""Resumable, quality-first inspection atlas for large VLOG projects.

Scene Atlas does not perform visual interpretation.  It packages deterministic
source evidence for an external visual agent and stores that agent's
source-addressed observations.  Technical measurements may prioritise review,
but never reject otherwise valid media.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import TypeAdapter

from facut.core.models import MediaAsset, MediaKind, ProjectDocument
from facut.core.project_manager import ProjectManager

from .models import EvidenceObservation


ATLAS_VERSION = "1.0"
DEFAULT_BATCH_SIZE = 12
DEEP_SAMPLE_COUNT = 12


def _atlas_root(project_dir: str | Path) -> Path:
    return Path(project_dir) / "cache" / "vlog" / "atlas"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _digest(payload: Any, *, length: int | None = None) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    value = hashlib.sha256(encoded).hexdigest()
    return value[:length] if length else value


def _sample_times(duration: float | None, count: int = 3) -> list[float]:
    """Return deterministic source times without treating stills as disposable."""

    if duration is None or duration <= 0:
        return [0.0]
    if count <= 1 or duration < 1.5:
        return [round(duration / 2, 6)]
    margin = min(0.5, duration * 0.1)
    if count == 3:
        return [round(margin, 6), round(duration / 2, 6), round(duration - margin, 6)]
    span = max(0.0, duration - (2 * margin))
    return [round(margin + span * index / (count - 1), 6) for index in range(count)]


def _source_exists(manager: ProjectManager, asset: MediaAsset) -> tuple[bool, str | None]:
    source = manager.resolve_path(asset.path)
    if not source.is_file():
        return False, "missing"
    if source.stat().st_size <= 0:
        return False, "empty"
    return True, None


def _asset_record(asset: MediaAsset, prepared: dict[str, Any] | None = None) -> dict[str, Any]:
    duration = asset.technical.duration
    prepared = prepared or {}
    return {
        "media_id": asset.id,
        "content_sha256": asset.sha256,
        "name": asset.original_name,
        "kind": asset.kind.value,
        "duration": duration,
        "dimensions": [asset.technical.width, asset.technical.height],
        "rotation": asset.technical.rotation,
        "creation_time": asset.technical.creation_time,
        "location": {
            "latitude": asset.technical.latitude,
            "longitude": asset.technical.longitude,
        },
        "proxy_available": bool(asset.proxy_path),
        "source_ref": asset.proxy_path or asset.path,
        "source_kind": "proxy" if asset.proxy_path else "original",
        "representative_frames": prepared.get("representative_frames", []),
        "waveform": prepared.get("waveform"),
        "baseline": {
            "required": True,
            "sample_times": _sample_times(duration),
            "required_evidence": [
                "entry-middle-exit",
                "scene-change-or-action-peak",
                "speech-or-original-audio-value",
            ],
        },
        "deep_review": {"required": False, "reasons": [], "sample_count": 0},
    }


def _task_id(kind: Literal["baseline", "deep"], assets: Iterable[dict[str, Any]]) -> str:
    members = sorted(
        (item["media_id"], item["content_sha256"]) for item in assets
    )
    return f"atlas_{kind}_{_digest({'kind': kind, 'members': members}, length=16)}"


def _make_task(
    kind: Literal["baseline", "deep"], assets: list[dict[str, Any]]
) -> dict[str, Any]:
    task_id = _task_id(kind, assets)
    return {
        "task_id": task_id,
        "kind": kind,
        "status": "pending",
        "media_ids": [item["media_id"] for item in assets],
        "asset_hashes": {item["media_id"]: item["content_sha256"] for item in assets},
        "minimum_observations": len(assets),
    }


def _chunk(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _stored_observation_index(root: Path) -> dict[str, list[dict[str, Any]]]:
    payload = _read_json(root / "observations.json", {"observations": []})
    result: dict[str, list[dict[str, Any]]] = {}
    for record in payload.get("observations", []):
        result.setdefault(record["observation"]["media_id"], []).append(record)
    return result


def _current_observed_media(
    assets: list[dict[str, Any]], observations: dict[str, list[dict[str, Any]]]
) -> set[str]:
    hashes = {item["media_id"]: item["content_sha256"] for item in assets}
    return {
        media_id
        for media_id, records in observations.items()
        if any(item.get("content_sha256") == hashes.get(media_id) for item in records)
    }


def _deep_review_reasons(observations: list[dict[str, Any]]) -> list[str]:
    reasons: set[str] = set()
    important_actions = {
        "fall", "recovery", "reaction", "surprise", "reunion", "lost",
        "摔倒", "爬起", "反应", "惊喜", "重逢", "迷路",
    }
    uncertain_terms = ("uncertain", "unclear", "unknown", "不确定", "不清楚", "疑似")
    for record in observations:
        item = record["observation"]
        if float(item.get("confidence", 0.5)) < 0.75:
            reasons.add("low-confidence-meaning")
        if item.get("warnings"):
            reasons.add("agent-warning")
        if any(term in item.get("summary", "").casefold() for term in uncertain_terms):
            reasons.add("ambiguous-summary")
        if important_actions.intersection(item.get("actions", [])):
            reasons.add("important-action-or-reaction")
        if item.get("story_role") in {"incident", "recovery", "outcome"}:
            reasons.add("event-chain")
        if item.get("original_audio_value") == "high":
            reasons.add("valuable-original-audio")
    return sorted(reasons)


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _annotate_near_duplicates(assets: list[dict[str, Any]]) -> None:
    """Record perceptual candidates without excluding either source."""

    fingerprints: list[tuple[dict[str, Any], str]] = []
    for asset in assets:
        frames = asset.get("representative_frames") or []
        middle = frames[len(frames) // 2] if frames else {}
        value = middle.get("perceptual_hash")
        if isinstance(value, str) and len(value) == 16:
            fingerprints.append((asset, value))
    for index, (left, left_hash) in enumerate(fingerprints):
        for right, right_hash in fingerprints[index + 1 :]:
            distance = _hash_distance(left_hash, right_hash)
            if distance > 4:
                continue
            left.setdefault("near_duplicate_candidates", []).append(
                {"media_id": right["media_id"], "hamming_distance": distance}
            )
            right.setdefault("near_duplicate_candidates", []).append(
                {"media_id": left["media_id"], "hamming_distance": distance}
            )


def build_scene_atlas(
    manager: ProjectManager,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Build or resume an atlas without performing visual inference.

    Existing observations remain valid only while their source content hash is
    unchanged. Exact byte duplicates are represented once and explicitly
    reported; missing/empty files are also reported. No quality score excludes
    an otherwise valid asset.
    """

    if batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    root = _atlas_root(manager.project_dir)
    document = manager.require_document()
    prepared_manifest = _read_json(root.parent / "manifest.json", {"assets": []})
    prepared_by_id = {
        item["media_id"]: item for item in prepared_manifest.get("assets", [])
    }
    assets: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    first_by_hash: dict[str, str] = {}
    for media in sorted(document.media, key=lambda item: item.id):
        if media.kind not in {MediaKind.VIDEO, MediaKind.IMAGE}:
            continue
        valid, reason = _source_exists(manager, media)
        if not valid:
            excluded.append({"media_id": media.id, "reason": reason})
            continue
        if media.sha256 in first_by_hash:
            excluded.append(
                {
                    "media_id": media.id,
                    "reason": "exact_duplicate",
                    "duplicate_of": first_by_hash[media.sha256],
                }
            )
            continue
        first_by_hash[media.sha256] = media.id
        assets.append(_asset_record(media, prepared_by_id.get(media.id)))

    stored = _stored_observation_index(root)
    _annotate_near_duplicates(assets)
    observed = _current_observed_media(assets, stored)
    for asset in assets:
        reasons = _deep_review_reasons(stored.get(asset["media_id"], []))
        if reasons:
            asset["deep_review"] = {
                "required": True,
                "reasons": reasons,
                "sample_count": DEEP_SAMPLE_COUNT,
                "sample_times": _sample_times(asset["duration"], DEEP_SAMPLE_COUNT),
            }

    old = _read_json(root / "atlas.json", {"tasks": []})
    old_status = {item["task_id"]: item["status"] for item in old.get("tasks", [])}
    tasks: list[dict[str, Any]] = []
    # Preserve every baseline batch whose sources are unchanged. This keeps a
    # task addressable after a partial submission and also makes retries truly
    # idempotent after a process restart. Only new/changed assets are regrouped.
    current_hashes = {item["media_id"]: item["content_sha256"] for item in assets}
    assigned: set[str] = set()
    for previous_task in old.get("tasks", []):
        if (
            previous_task.get("kind") == "baseline"
            and previous_task.get("asset_hashes")
            and all(
                current_hashes.get(media_id) == content_hash
                for media_id, content_hash in previous_task.get("asset_hashes", {}).items()
            )
        ):
            retained = dict(previous_task)
            members = set(retained["media_ids"])
            retained["status"] = "complete" if members <= observed else "pending"
            tasks.append(retained)
            assigned.update(members)
    baseline = [item for item in assets if item["media_id"] not in assigned]
    for batch in _chunk(baseline, batch_size):
        task = _make_task("baseline", batch)
        task["status"] = "complete" if set(task["media_ids"]) <= observed else "pending"
        tasks.append(task)
    deep = [item for item in assets if item["deep_review"]["required"]]
    for batch in _chunk(deep, batch_size):
        task = _make_task("deep", batch)
        task["status"] = old_status.get(task["task_id"], "pending")
        tasks.append(task)

    identity = [(item["media_id"], item["content_sha256"]) for item in assets]
    atlas = {
        "version": ATLAS_VERSION,
        "atlas_id": f"atlas_{_digest(identity, length=20)}",
        "project_revision": document.revision,
        "batch_size": batch_size,
        "visual_provider": "external-agent",
        "quality_policy": "Technical data prioritises review and never rejects valid media.",
        "assets": assets,
        "excluded": excluded,
        "tasks": tasks,
    }
    _write_json(root / "atlas.json", atlas)
    return {
        "atlas_id": atlas["atlas_id"],
        "asset_count": len(assets),
        "excluded_count": len(excluded),
        "pending_tasks": sum(item["status"] == "pending" for item in tasks),
        "baseline_pending": sum(
            item["status"] == "pending" and item["kind"] == "baseline" for item in tasks
        ),
        "deep_pending": sum(
            item["status"] == "pending" and item["kind"] == "deep" for item in tasks
        ),
        "path": str((root / "atlas.json").resolve()),
    }


def scene_atlas_status(project_dir: str | Path) -> dict[str, Any]:
    root = _atlas_root(project_dir)
    atlas = _read_json(root / "atlas.json", None)
    if atlas is None:
        return {
            "stage": "not_built",
            "pending_tasks": 0,
            "next_command": "facut vlog atlas build",
        }
    pending = [item for item in atlas["tasks"] if item["status"] == "pending"]
    stage = "inspection" if pending else "complete"
    return {
        "stage": stage,
        "atlas_id": atlas["atlas_id"],
        "asset_count": len(atlas["assets"]),
        "excluded_count": len(atlas["excluded"]),
        "pending_tasks": len(pending),
        "completed_tasks": sum(item["status"] == "complete" for item in atlas["tasks"]),
        "baseline_pending": sum(item["kind"] == "baseline" for item in pending),
        "deep_pending": sum(item["kind"] == "deep" for item in pending),
        "next_task_id": pending[0]["task_id"] if pending else None,
        "next_command": "facut vlog inspect batch" if pending else "facut vlog story brief",
    }


def next_atlas_inspection_batch(
    project_dir: str | Path, *, task_id: str | None = None
) -> dict[str, Any]:
    root = _atlas_root(project_dir)
    atlas = _read_json(root / "atlas.json", None)
    if atlas is None:
        raise FileNotFoundError("Scene Atlas was not built. Run `facut vlog atlas build` first.")
    pending = [item for item in atlas["tasks"] if item["status"] == "pending"]
    if task_id is None:
        if not pending:
            return {"status": "complete", "remaining_tasks": 0, "assets": []}
        task = pending[0]
    else:
        task = next((item for item in atlas["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise ValueError(f'Atlas task "{task_id}" was not found.')
    by_id = {item["media_id"]: item for item in atlas["assets"]}
    assets = [by_id[media_id] for media_id in task["media_ids"]]
    return {
        **task,
        "atlas_id": atlas["atlas_id"],
        "visual_provider": atlas["visual_provider"],
        "remaining_tasks": len(pending),
        "purpose": (
            "Inspect baseline evidence for every asset; do not discard an asset from technical quality alone."
            if task["kind"] == "baseline"
            else "Resolve ambiguity or important actions with denser frames or a short proxy segment."
        ),
        "assets": assets,
        "required_output_schema": EvidenceObservation.model_json_schema(),
    }


def ingest_atlas_observations(
    document: ProjectDocument,
    project_dir: str | Path,
    payload: dict[str, Any],
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Idempotently accept one batch of external-agent visual observations."""

    root = _atlas_root(project_dir)
    atlas = _read_json(root / "atlas.json", None)
    if atlas is None:
        raise FileNotFoundError("Scene Atlas was not built. Run `facut vlog atlas build` first.")
    raw = payload.get("observations", payload.get("items"))
    if not isinstance(raw, list):
        raise ValueError('Observation input requires an "observations" list.')
    observations = TypeAdapter(list[EvidenceObservation]).validate_python(raw)
    task_id = task_id or payload.get("task_id")
    task = next((item for item in atlas["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ValueError("A valid atlas task_id is required for batch observations.")
    known = {item.id: item for item in document.media}
    atlas_assets = {item["media_id"]: item for item in atlas["assets"]}
    task_media = set(task["media_ids"])
    supplied_hashes = payload.get("asset_hashes") or task["asset_hashes"]
    for media_id, expected_hash in task["asset_hashes"].items():
        if supplied_hashes.get(media_id) != expected_hash:
            raise ValueError(f'Atlas evidence for "{media_id}" is stale; rebuild the inspection batch.')
    for item in observations:
        if item.media_id not in task_media:
            raise ValueError(
                f'Observation "{item.observation_id}" references media outside task "{task_id}".'
            )
        media = known.get(item.media_id)
        if media is None or media.sha256 != atlas_assets[item.media_id]["content_sha256"]:
            raise ValueError(f'Atlas source "{item.media_id}" changed; rebuild Scene Atlas.')
        duration = media.technical.duration
        if duration is not None and item.range.end > duration + 1e-6:
            raise ValueError(
                f'Observation {item.observation_id} exceeds media duration {duration:.3f}s.'
            )

    stored = _read_json(root / "observations.json", {"version": ATLAS_VERSION, "observations": []})
    by_id = {item["observation"]["observation_id"]: item for item in stored["observations"]}
    inserted = updated = unchanged = 0
    for item in observations:
        record = {
            "content_sha256": atlas_assets[item.media_id]["content_sha256"],
            "task_id": task_id,
            "observation": item.model_dump(mode="json"),
        }
        previous = by_id.get(item.observation_id)
        if previous == record:
            unchanged += 1
        elif previous is None:
            by_id[item.observation_id] = record
            inserted += 1
        else:
            if previous["observation"]["media_id"] != item.media_id:
                raise ValueError(f'Observation id "{item.observation_id}" belongs to another media asset.')
            by_id[item.observation_id] = record
            updated += 1
    output = {
        "version": ATLAS_VERSION,
        "observations": sorted(by_id.values(), key=lambda item: item["observation"]["observation_id"]),
    }
    _write_json(root / "observations.json", output)

    # Scene Atlas is an efficient transport layer, not a second evidence
    # universe. Mirror validated observations into the canonical evidence.v2
    # store so StoryGraph, Trip Bible, narration and continuity consumers all
    # see the same source-addressed facts.
    from .director import ingest_observations

    canonical = ingest_observations(
        document,
        project_dir,
        {"observations": [item.model_dump(mode="json") for item in observations]},
    )

    covered = {
        record["observation"]["media_id"]
        for record in output["observations"]
        if record.get("task_id") == task_id
        and record.get("content_sha256")
        == task["asset_hashes"].get(record["observation"]["media_id"])
    }
    if task_media <= covered:
        task["status"] = "complete"
    _write_json(root / "atlas.json", atlas)
    # Rebuild creates any newly required deep-review batches and keeps stable
    # completed task IDs. It performs no media decoding or model inference.
    manager = ProjectManager(project_dir)
    manager.load()
    build_scene_atlas(manager, batch_size=atlas["batch_size"])
    status = scene_atlas_status(project_dir)
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "task_id": task_id,
        "task_status": "complete" if task_media <= covered else "pending",
        "observation_count": len(output["observations"]),
        "pending_tasks": status["pending_tasks"],
        "deep_pending": status["deep_pending"],
        "path": str((root / "observations.json").resolve()),
        "canonical_evidence_path": canonical["path"],
        "director_inbox_pending": canonical["director_inbox_pending"],
    }
