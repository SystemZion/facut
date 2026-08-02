"""Multiple named timelines within one portable facut project."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from facut.core.models import (
    Marker,
    ProjectDocument,
    SubtitleCue,
    TextOverlay,
    Track,
    Transition,
)


def _store(document: ProjectDocument) -> dict[str, Any]:
    return document.settings.setdefault("sequences", {})


def snapshot_sequence(document: ProjectDocument, name: str) -> dict[str, Any]:
    """Create or replace one named timeline snapshot."""

    normalized = name.strip()
    if not normalized:
        raise ValueError("Sequence name cannot be empty.")
    payload = {
        "name": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tracks": [item.model_dump(mode="json") for item in document.tracks],
        "transitions": [item.model_dump(mode="json") for item in document.transitions],
        "subtitle_cues": [item.model_dump(mode="json") for item in document.subtitle_cues],
        "text_overlays": [item.model_dump(mode="json") for item in document.text_overlays],
        "markers": [item.model_dump(mode="json") for item in document.markers],
        "duration": document.recompute_duration(),
    }
    _store(document)[normalized] = payload
    document.settings["active_sequence"] = normalized
    return payload


def materialize_sequence(document: ProjectDocument, name: str) -> ProjectDocument:
    """Return a validated project copy containing one named timeline."""

    payload = _store(document).get(name)
    if payload is None:
        raise ValueError(f'Sequence "{name}" was not found.')
    candidate = document.model_copy(deep=True)
    candidate.tracks = [Track.model_validate(item) for item in payload.get("tracks", [])]
    candidate.transitions = [
        Transition.model_validate(item) for item in payload.get("transitions", [])
    ]
    candidate.subtitle_cues = [
        SubtitleCue.model_validate(item) for item in payload.get("subtitle_cues", [])
    ]
    candidate.text_overlays = [
        TextOverlay.model_validate(item) for item in payload.get("text_overlays", [])
    ]
    candidate.markers = [Marker.model_validate(item) for item in payload.get("markers", [])]
    candidate.settings["active_sequence"] = name
    candidate.recompute_duration()
    return ProjectDocument.model_validate(candidate.model_dump(mode="json"))


def checkout_sequence(document: ProjectDocument, name: str) -> dict[str, Any]:
    materialized = materialize_sequence(document, name)
    document.tracks = materialized.tracks
    document.transitions = materialized.transitions
    document.subtitle_cues = materialized.subtitle_cues
    document.text_overlays = materialized.text_overlays
    document.markers = materialized.markers
    document.settings["active_sequence"] = name
    document.recompute_duration()
    return _store(document)[name]


def duplicate_sequence(
    document: ProjectDocument, source: str, destination: str
) -> dict[str, Any]:
    materialized = materialize_sequence(document, source)
    original = (
        document.tracks,
        document.transitions,
        document.subtitle_cues,
        document.text_overlays,
        document.markers,
        document.settings.get("active_sequence"),
    )
    try:
        document.tracks = materialized.tracks
        document.transitions = materialized.transitions
        document.subtitle_cues = materialized.subtitle_cues
        document.text_overlays = materialized.text_overlays
        document.markers = materialized.markers
        return snapshot_sequence(document, destination)
    finally:
        (
            document.tracks,
            document.transitions,
            document.subtitle_cues,
            document.text_overlays,
            document.markers,
            active,
        ) = original
        document.settings["active_sequence"] = active
        document.recompute_duration()


def remove_sequence(document: ProjectDocument, name: str) -> dict[str, Any]:
    store = _store(document)
    if name not in store:
        raise ValueError(f'Sequence "{name}" was not found.')
    removed = store.pop(name)
    if document.settings.get("active_sequence") == name:
        document.settings["active_sequence"] = None
    return removed


def list_sequences(document: ProjectDocument) -> list[dict[str, Any]]:
    active = document.settings.get("active_sequence")
    return [
        {
            "name": name,
            "active": name == active,
            "duration": payload.get("duration", 0.0),
            "updated_at": payload.get("updated_at"),
            "track_count": len(payload.get("tracks", [])),
        }
        for name, payload in sorted(_store(document).items())
    ]
