"""Director Inbox and Trip Bible for evidence-grounded travel editing."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TripPerson(ContextModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    relationship: str | None = None
    confirmed: bool = True


class TripPlace(ContextModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    confirmed: bool = True


class TripFact(ContextModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: Literal["confirmed", "uncertain", "rejected"] = "uncertain"
    source: Literal["user", "metadata", "external-agent"] = "external-agent"
    evidence_observation_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class TripBible(ContextModel):
    version: Literal["1.0"] = "1.0"
    trip_name: str = "Untitled trip"
    locale: str = "zh-CN"
    people: list[TripPerson] = Field(default_factory=list)
    places: list[TripPlace] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    facts: list[TripFact] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class DirectorInboxItem(ContextModel):
    id: str
    priority: int = Field(ge=0, le=100)
    kind: Literal["baseline", "deep-review", "continuity", "fact-review"]
    status: Literal["pending", "resolved"] = "pending"
    reason: str
    media_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    requested_evidence: list[str] = Field(default_factory=list)
    source_fingerprint: str
    resolution: str | None = None
    created_at: str
    resolved_at: str | None = None


def _root(project_dir: str | Path) -> Path:
    path = Path(project_dir) / "cache" / "vlog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text("utf-8"))


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def load_trip_bible(project_dir: str | Path, *, default_name: str = "Untitled trip") -> TripBible:
    path = _root(project_dir) / "trip-bible.json"
    payload = _read(path, None)
    if payload is None:
        return TripBible(trip_name=default_name)
    return TripBible.model_validate(payload)


def save_trip_bible(project_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    bible = TripBible.model_validate(payload)
    bible.updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    path = _write(_root(project_dir) / "trip-bible.json", bible.model_dump(mode="json"))
    return {
        "path": str(path.resolve()),
        "bible": bible.model_dump(mode="json"),
        "uncertain_fact_count": sum(item.status == "uncertain" for item in bible.facts),
        "policy": "Only confirmed facts may be asserted in generated narration or titles.",
    }


def audit_trip_bible(project_dir: str | Path) -> dict[str, Any]:
    """Report ambiguous entities and facts that cannot safely reach narration or titles."""

    bible = load_trip_bible(project_dir)
    issues: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    seen_names: dict[str, str] = {}
    for kind, entities in (("person", bible.people), ("place", bible.places)):
        for entity in entities:
            if entity.id in seen_ids:
                issues.append({"code": "DUPLICATE_ENTITY_ID", "entity": entity.id})
            seen_ids[entity.id] = kind
            for name in [entity.display_name, *entity.aliases]:
                normalized = name.strip().casefold()
                owner = seen_names.get(normalized)
                if owner is not None and owner != entity.id:
                    issues.append(
                        {
                            "code": "AMBIGUOUS_ENTITY_NAME",
                            "name": name,
                            "entities": sorted({owner, entity.id}),
                        }
                    )
                seen_names[normalized] = entity.id
            if not entity.confirmed:
                issues.append(
                    {"code": "UNCONFIRMED_ENTITY", "kind": kind, "entity": entity.id}
                )
    for fact in bible.facts:
        if fact.status == "uncertain":
            issues.append({"code": "UNCERTAIN_FACT", "fact": fact.id})
        if fact.status == "confirmed" and not fact.evidence_observation_ids and fact.source != "user":
            issues.append({"code": "CONFIRMED_FACT_WITHOUT_EVIDENCE", "fact": fact.id})
    blocking_codes = {
        "DUPLICATE_ENTITY_ID",
        "AMBIGUOUS_ENTITY_NAME",
        "CONFIRMED_FACT_WITHOUT_EVIDENCE",
    }
    return {
        "valid": not any(item["code"] in blocking_codes for item in issues),
        "issue_count": len(issues),
        "blocking_count": sum(item["code"] in blocking_codes for item in issues),
        "issues": issues,
        "policy_sha256": trip_bible_sha256(bible),
    }


def rename_trip_entity(
    project_dir: str | Path,
    reference: str,
    new_name: str,
    *,
    kind: str = "auto",
) -> dict[str, Any]:
    """Rename one uniquely resolved person/place while retaining the old name as an alias."""

    if kind not in {"auto", "person", "place"}:
        raise ValueError("kind must be auto, person, or place")
    normalized_reference = reference.strip().casefold()
    selected: list[tuple[str, Any]] = []
    bible = load_trip_bible(project_dir)
    groups = []
    if kind in {"auto", "person"}:
        groups.append(("person", bible.people))
    if kind in {"auto", "place"}:
        groups.append(("place", bible.places))
    for entity_kind, entities in groups:
        for entity in entities:
            names = [entity.id, entity.display_name, *entity.aliases]
            if normalized_reference in {item.strip().casefold() for item in names}:
                selected.append((entity_kind, entity))
    if not selected:
        raise ValueError(f'Trip Bible entity "{reference}" was not found.')
    if len(selected) != 1:
        raise ValueError(
            f'Trip Bible entity "{reference}" is ambiguous; use a stable entity ID and --kind.'
        )
    entity_kind, entity = selected[0]
    clean_name = new_name.strip()
    if not clean_name:
        raise ValueError("new_name must not be empty")
    normalized_new = clean_name.casefold()
    for other_kind, entities in (("person", bible.people), ("place", bible.places)):
        for other in entities:
            if other.id == entity.id and other_kind == entity_kind:
                continue
            if normalized_new in {
                item.strip().casefold()
                for item in [other.display_name, *other.aliases]
            }:
                raise ValueError(
                    f'New Trip Bible name "{clean_name}" already belongs to "{other.id}".'
                )
    old_name = entity.display_name
    if old_name != clean_name and old_name not in entity.aliases:
        entity.aliases.append(old_name)
    entity.display_name = clean_name
    saved = save_trip_bible(project_dir, bible.model_dump(mode="json"))
    return {
        "kind": entity_kind,
        "entity_id": entity.id,
        "old_name": old_name,
        "new_name": clean_name,
        "aliases": entity.aliases,
        "trip_bible_sha256": trip_bible_sha256(TripBible.model_validate(saved["bible"])),
        "stale_dependents": ["story-plan", "narration-plan", "typography-plan"],
        "path": saved["path"],
    }


def trip_bible_fact_policy(bible: TripBible) -> dict[str, Any]:
    """Return the compact allow/withhold contract consumed by generators."""

    return {
        "trip_name": bible.trip_name,
        "confirmed_people": [item.display_name for item in bible.people if item.confirmed],
        "confirmed_places": [item.display_name for item in bible.places if item.confirmed],
        "confirmed_dates": list(bible.dates),
        "glossary": dict(bible.glossary),
        "allowed_facts": [item.statement for item in bible.facts if item.status == "confirmed"],
        "withheld_uncertain_facts": [
            item.statement for item in bible.facts if item.status == "uncertain"
        ],
        "rejected_facts": [item.statement for item in bible.facts if item.status == "rejected"],
        "forbidden_claims": list(bible.forbidden_claims),
        "rule": (
            "Generated narration and titles may assert only allowed_facts and confirmed names; "
            "uncertain, rejected and forbidden claims must not be asserted."
        ),
    }


def fact_policy_sha256(policy: dict[str, Any]) -> str:
    """Return a stable semantic digest for plan staleness checks."""

    payload = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def trip_bible_sha256(bible: TripBible) -> str:
    """Hash only the effective policy, not the volatile ``updated_at`` field."""

    return fact_policy_sha256(trip_bible_fact_policy(bible))


def _item_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:12]
    return f"inbox_{digest}"


def rebuild_director_inbox(
    project_dir: str | Path, *, persist: bool = True
) -> dict[str, Any]:
    """Build a stable priority queue without claiming unobserved visual facts."""

    root = _root(project_dir)
    manifest = _read(root / "manifest.json", {"assets": []})
    observations = _read(root / "observations.json", {"observations": []})["observations"]
    existing = _read(root / "director-inbox.json", {"items": []})
    previous = {item["id"]: item for item in existing.get("items", [])}
    observed_media = {item["media_id"] for item in observations}
    generated: list[DirectorInboxItem] = []
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def add(item: DirectorInboxItem) -> None:
        old = previous.get(item.id)
        if (
            old
            and old.get("status") == "resolved"
            and old.get("source_fingerprint") == item.source_fingerprint
        ):
            item.status = "resolved"
            item.resolution = old.get("resolution")
            item.resolved_at = old.get("resolved_at")
            item.created_at = old.get("created_at") or item.created_at
        generated.append(item)

    def fingerprint(payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    for asset in manifest.get("assets", []):
        if asset["media_id"] in observed_media:
            continue
        duration = float(asset.get("duration") or 0)
        priority = min(75, 50 + round(min(duration, 120) / 8))
        add(DirectorInboxItem(
            id=_item_id("baseline", asset["media_id"]),
            priority=priority,
            kind="baseline",
            reason="This playable asset still needs truthful baseline visual coverage.",
            media_ids=[asset["media_id"]],
            requested_evidence=["entry-middle-exit", "speech-or-reaction", "shot-quality"],
            source_fingerprint=fingerprint(asset),
            created_at=now,
        ))
    for observation in observations:
        confidence = float(observation.get("confidence", 0.5))
        warnings = observation.get("warnings") or []
        if confidence < 0.75 or warnings:
            priority = min(96, 78 + round((0.75 - min(confidence, 0.75)) * 40) + len(warnings) * 2)
            add(DirectorInboxItem(
                id=_item_id("deep-review", observation["observation_id"]),
                priority=priority,
                kind="deep-review",
                reason="The external observation is ambiguous and needs denser frames or a short proxy segment.",
                media_ids=[observation["media_id"]],
                observation_ids=[observation["observation_id"]],
                requested_evidence=["8-16-frames", "short-proxy-range", "resolve-warnings"],
                source_fingerprint=fingerprint(observation),
                created_at=now,
            ))
    chains: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("subject_id") and observation.get("event_chain"):
            chains.setdefault(
                (observation["subject_id"], observation["event_chain"]), []
            ).append(observation)
    for (subject, chain), items in chains.items():
        roles = {item.get("story_role") for item in items}
        if "incident" in roles and not roles.intersection({"recovery", "outcome"}):
            add(DirectorInboxItem(
                id=_item_id("continuity", f"{subject}:{chain}"),
                priority=100,
                kind="continuity",
                reason="An incident has no confirmed recovery/outcome for the same subject.",
                media_ids=sorted({item["media_id"] for item in items}),
                observation_ids=sorted(item["observation_id"] for item in items),
                requested_evidence=["same-subject-recovery", "same-subject-outcome"],
                source_fingerprint=fingerprint(items),
                created_at=now,
            ))
    bible = load_trip_bible(project_dir)
    for fact in bible.facts:
        if fact.status == "uncertain":
            add(DirectorInboxItem(
                id=_item_id("fact-review", fact.id),
                priority=90,
                kind="fact-review",
                reason=f"Trip Bible fact is uncertain: {fact.statement}",
                observation_ids=fact.evidence_observation_ids,
                requested_evidence=["confirm-or-reject-fact", "source-attribution"],
                source_fingerprint=fingerprint(fact.model_dump(mode="json")),
                created_at=now,
            ))
    generated.sort(key=lambda item: (-item.priority, item.created_at, item.id))
    payload = {"version": "1.0", "items": [item.model_dump(mode="json") for item in generated]}
    path = root / "director-inbox.json"
    if persist:
        _write(path, payload)
    return {
        "path": str(path.resolve()),
        "total": len(generated),
        "pending": sum(item.status == "pending" for item in generated),
        "items": payload["items"],
    }


def next_inbox_items(project_dir: str | Path, *, limit: int = 8) -> dict[str, Any]:
    inbox = rebuild_director_inbox(project_dir)
    pending = [item for item in inbox["items"] if item["status"] == "pending"]
    return {
        "items": pending[:limit],
        "returned": min(limit, len(pending)),
        "remaining": max(0, len(pending) - limit),
        "quality_policy": "Priority changes inspection order, never minimum coverage.",
    }


def resolve_inbox_item(project_dir: str | Path, item_id: str, *, resolution: str) -> dict[str, Any]:
    root = _root(project_dir)
    inbox = rebuild_director_inbox(project_dir)
    note = resolution.strip()
    if not note:
        raise ValueError("Resolving an inbox item requires a non-empty resolution.")
    matched = None
    for item in inbox["items"]:
        if item["id"] == item_id:
            item["status"] = "resolved"
            item["resolution"] = note
            item["resolved_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            matched = item
            break
    if matched is None:
        raise FileNotFoundError(f'Director Inbox item "{item_id}" was not found.')
    _write(root / "director-inbox.json", {"version": "1.0", "items": inbox["items"]})
    return {"item": matched, "pending": sum(item["status"] == "pending" for item in inbox["items"])}
