"""Portable semantic index with an explicit external-vision observation boundary."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from facut.core.models import ProjectDocument


INDEX_VERSION = "1.0"
_WORD = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


def _tokens(value: str) -> list[str]:
    normalized = value.casefold().replace("_", " ").replace("-", " ")
    base = _WORD.findall(normalized)
    chinese = [item for item in base if "\u3400" <= item <= "\u9fff"]
    bigrams = ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
    return base + bigrams


def _observation_text(item: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("text", "caption", "label", "category", "shot_type", "daypart"):
        value = item.get(key)
        if value is not None:
            values.append(str(value))
    values.extend(str(value) for value in item.get("tags", []))
    return " ".join(values)


def _metadata_observations(document: ProjectDocument) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for asset in document.media:
        labels: list[str] = []
        evidence: list[dict[str, Any]] = [{"type": "filename", "value": asset.original_name}]
        travel = asset.metadata.get("analysis", {}).get("travel", {})
        for candidate in travel.get("candidates", []):
            label = candidate.get("label")
            if label:
                labels.append(str(label))
            evidence.extend(candidate.get("evidence", []))
        observations.append(
            {
                "id": f"obs_{asset.id}_0000",
                "media_id": asset.id,
                "start": 0.0,
                "end": float(asset.technical.duration or 0.0),
                "text": " ".join([asset.original_name, *labels]),
                "tags": labels,
                "confidence": 0.55 if labels else 0.35,
                "quality_score": asset.metadata.get("analysis", {})
                .get("quality", {})
                .get("quality_score"),
                "evidence": evidence,
                "provider": "facut-metadata-v1",
            }
        )
    return observations


def _validate_external(
    document: ProjectDocument, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    known = {asset.id: asset for asset in document.media}
    items = payload.get("observations", payload.get("items"))
    if not isinstance(items, list):
        raise ValueError('Vision observation input requires an "observations" list.')
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"Observation {index} must be an object.")
        media_id = str(raw.get("media_id", ""))
        if media_id not in known:
            raise ValueError(f'Observation {index} references unknown media "{media_id}".')
        start = float(raw.get("start", 0.0))
        end = float(raw.get("end", known[media_id].technical.duration or start))
        if start < 0 or end <= start:
            raise ValueError(f"Observation {index} has an invalid time range.")
        media_duration = known[media_id].technical.duration
        if media_duration is not None and end > media_duration + 1e-6:
            raise ValueError(
                f"Observation {index} ends after media {media_id} ({media_duration:.3f}s)."
            )
        confidence = float(raw.get("confidence", 0.5))
        if not 0 <= confidence <= 1:
            raise ValueError(f"Observation {index} confidence must be between 0 and 1.")
        item = dict(raw)
        item.update(
            {
                "id": str(raw.get("id") or f"obs_ext_{media_id}_{index:04d}"),
                "media_id": media_id,
                "start": start,
                "end": end,
                "confidence": confidence,
                "provider": str(raw.get("provider") or payload.get("provider") or "external-vision"),
                "evidence": list(raw.get("evidence", [])),
            }
        )
        result.append(item)
    return result


def build_semantic_index(
    document: ProjectDocument,
    project_dir: str | Path,
    *,
    observations_file: str | Path | None = None,
) -> dict[str, Any]:
    """Build a hash-addressed index without claiming unconfigured vision inference."""

    observations = _metadata_observations(document)
    providers = ["facut-metadata-v1"]
    input_hash: str | None = None
    if observations_file is not None:
        path = Path(observations_file).expanduser().resolve()
        raw = path.read_bytes()
        input_hash = hashlib.sha256(raw).hexdigest()
        external = _validate_external(document, json.loads(raw.decode("utf-8")))
        known_ids = {item["id"] for item in observations}
        duplicate_ids = sorted(
            item["id"] for item in external if item["id"] in known_ids
        )
        external_ids = [item["id"] for item in external]
        duplicate_ids.extend(
            sorted(
                item
                for item, count in Counter(external_ids).items()
                if count > 1
            )
        )
        if duplicate_ids:
            raise ValueError(
                "Vision observations contain duplicate IDs: "
                + ", ".join(sorted(set(duplicate_ids)))
            )
        observations.extend(external)
        providers.extend(sorted({item["provider"] for item in external}))
    document_fingerprint = hashlib.sha256(
        json.dumps(
            [(asset.id, asset.sha256) for asset in document.media],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "version": INDEX_VERSION,
        "document_fingerprint": document_fingerprint,
        "input_hash": input_hash,
        "providers": sorted(set(providers)),
        "observation_count": len(observations),
        "observations": observations,
        "limitations": []
        if observations_file
        else [
            "No visual observation file was supplied; results use filenames and saved metadata only."
        ],
    }
    destination = Path(project_dir) / "cache" / "semantic" / "index.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    return {**payload, "path": str(destination.resolve()), "observations": None}


def load_semantic_index(project_dir: str | Path) -> dict[str, Any]:
    path = Path(project_dir) / "cache" / "semantic" / "index.json"
    if not path.is_file():
        raise FileNotFoundError(
            "Semantic index was not found. Run `facut semantic index` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def search_semantic_index(
    index: dict[str, Any], query: str, *, limit: int = 10, minimum_score: float = 0.05
) -> dict[str, Any]:
    """Rank structured observations using deterministic BM25-like lexical evidence."""

    if not query.strip():
        raise ValueError("Semantic query cannot be empty.")
    observations = list(index.get("observations", []))
    query_terms = Counter(_tokens(query))
    documents = [Counter(_tokens(_observation_text(item))) for item in observations]
    frequencies = Counter(term for terms in documents for term in set(terms))
    ranked: list[dict[str, Any]] = []
    total = max(1, len(documents))
    for item, terms in zip(observations, documents, strict=True):
        score = 0.0
        matches: list[str] = []
        for term, query_count in query_terms.items():
            if terms[term]:
                inverse = math.log(1 + (total + 1) / (frequencies[term] + 1))
                score += min(terms[term], 3) * query_count * inverse
                matches.append(term)
        normalized = score / max(1.0, sum(query_terms.values()))
        confidence = float(item.get("confidence", 0.5))
        final = normalized * (0.6 + 0.4 * confidence)
        if final < minimum_score:
            continue
        ranked.append(
            {
                **item,
                "score": round(final, 6),
                "match_terms": sorted(set(matches)),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -float(item.get("confidence", 0))))
    return {
        "query": query,
        "results": ranked[: max(1, limit)],
        "index_version": index.get("version"),
        "providers": index.get("providers", []),
        "limitations": index.get("limitations", []),
    }
