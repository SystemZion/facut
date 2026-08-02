"""Plan-first OTIO and FCPXML imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import opentimelineio as otio

from facut.core.models import (
    Clip,
    Marker,
    ProjectDocument,
    SubtitleCue,
    TextOverlay,
    Track,
    TrackType,
    Transition,
)
from facut.core.project_manager import ProjectManager


@dataclass(slots=True)
class ImportClip:
    path: Path
    timeline_start: float
    source_in: float
    duration: float
    name: str
    facut: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImportTrack:
    id: str
    name: str
    type: TrackType
    order: int
    clips: list[ImportClip] = field(default_factory=list)


@dataclass(slots=True)
class ExchangeImportPlan:
    source: Path
    format: str
    tracks: list[ImportTrack]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    missing_media: list[str] = field(default_factory=list)

    @property
    def clip_count(self) -> int:
        return sum(len(track.clips) for track in self.tracks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source.resolve()),
            "format": self.format,
            "tracks": [
                {
                    "id": track.id,
                    "name": track.name,
                    "type": track.type.value,
                    "order": track.order,
                    "clips": [
                        {
                            "path": str(clip.path),
                            "timeline_start": clip.timeline_start,
                            "source_in": clip.source_in,
                            "duration": clip.duration,
                            "name": clip.name,
                        }
                        for clip in track.clips
                    ],
                }
                for track in self.tracks
            ],
            "clip_count": self.clip_count,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "missing_media": self.missing_media,
        }


def _plain(value: Any) -> Any:
    """Detach OTIO's C++ metadata containers from their owning timeline."""

    if hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)) or (
        hasattr(value, "__iter__") and value.__class__.__name__ == "AnyVector"
    ):
        return [_plain(item) for item in value]
    return value


def _file_url(value: str, base: Path) -> Path:
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        raise ValueError(f'Only local file media is supported, not "{parsed.scheme}".')
    if parsed.scheme.casefold() == "file":
        raw = unquote(parsed.path)
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return Path(raw).resolve()
    path = Path(unquote(value))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def plan_otio_import(source: str | Path) -> ExchangeImportPlan:
    path = Path(source).expanduser().resolve()
    timeline = otio.adapters.read_from_file(str(path), adapter_name="otio_json")
    tracks: list[ImportTrack] = []
    warnings: list[str] = []
    missing: list[str] = []
    video_index = audio_index = 0
    for order, otio_track in enumerate(timeline.tracks):
        is_audio = otio_track.kind == otio.schema.TrackKind.Audio
        if is_audio:
            audio_index += 1
            fallback_id = f"A{audio_index}"
            track_type = TrackType.AUDIO
        else:
            video_index += 1
            fallback_id = f"V{video_index}"
            track_type = TrackType.VIDEO
        facut_track = (otio_track.metadata or {}).get("facut", {})
        track = ImportTrack(
            id=str(facut_track.get("track_id") or fallback_id),
            name=otio_track.name or fallback_id,
            type=TrackType(facut_track.get("type", track_type.value)),
            order=order,
        )
        cursor = 0.0
        for item in otio_track:
            if isinstance(item, otio.schema.Gap):
                cursor += item.duration().to_seconds()
                continue
            if isinstance(item, otio.schema.Transition):
                warnings.append(
                    f'OTIO transition "{item.name or "unnamed"}" is retained only when FACUT metadata is present.'
                )
                continue
            if not isinstance(item, otio.schema.Clip):
                warnings.append(f"Unsupported OTIO item {item.schema_name()} was skipped.")
                continue
            reference = item.media_reference
            target = getattr(reference, "target_url", "")
            if not target:
                warnings.append(f'Clip "{item.name}" has no external media reference.')
                continue
            media_path = _file_url(target, path.parent)
            if not media_path.is_file():
                missing.append(str(media_path))
            source_range = item.source_range
            source_in = source_range.start_time.to_seconds() if source_range else 0.0
            duration = (
                source_range.duration.to_seconds()
                if source_range
                else item.duration().to_seconds()
            )
            facut_clip = _plain((item.metadata or {}).get("facut", {}))
            timeline_start = float(facut_clip.get("timeline_start", cursor))
            track.clips.append(
                ImportClip(
                    path=media_path,
                    timeline_start=timeline_start,
                    source_in=source_in,
                    duration=duration,
                    name=item.name or media_path.name,
                    facut=facut_clip,
                )
            )
            cursor = max(cursor, timeline_start + duration)
        tracks.append(track)
    metadata = _plain((timeline.metadata or {}).get("facut", {}))
    markers: list[dict[str, Any]] = []
    for marker in timeline.tracks.markers:
        facut_marker = (marker.metadata or {}).get("facut")
        if facut_marker:
            markers.append(_plain(facut_marker))
            continue
        marked_range = marker.marked_range
        markers.append(
            {
                "at": marked_range.start_time.to_seconds(),
                "label": marker.name or "",
                "metadata": {
                    "end": marked_range.end_time_exclusive().to_seconds(),
                    "source": "otio",
                },
            }
        )
    metadata["markers"] = markers
    return ExchangeImportPlan(path, "otio", tracks, metadata, warnings, sorted(set(missing)))


def _seconds(value: str | None) -> float:
    if not value:
        return 0.0
    raw = value.removesuffix("s")
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        return float(numerator) / float(denominator)
    return float(raw)


def plan_fcpxml_import(source: str | Path) -> ExchangeImportPlan:
    path = Path(source).expanduser().resolve()
    root = ET.parse(path).getroot()
    if root.tag != "fcpxml":
        raise ValueError("The input is not an FCPXML document.")
    assets: dict[str, tuple[Path, str]] = {}
    for asset in root.findall("./resources/asset"):
        ref = asset.attrib.get("id")
        source_url = asset.attrib.get("src")
        if ref and source_url:
            assets[ref] = (_file_url(source_url, path.parent), asset.attrib.get("name", ref))
    groups: dict[int, ImportTrack] = {}
    missing: list[str] = []
    warnings = [
        "FCPXML import supports the deterministic asset-clip subset; generators, compound clips and roles may be lossy."
    ]
    for item in root.findall("./library/event/project/sequence/spine/asset-clip"):
        ref = item.attrib.get("ref", "")
        if ref not in assets:
            warnings.append(f'FCPXML asset reference "{ref}" was not found.')
            continue
        media_path, asset_name = assets[ref]
        lane = int(item.attrib.get("lane", "0"))
        track_type = TrackType.AUDIO if lane < 0 else TrackType.VIDEO
        normalized_lane = abs(lane) + 1 if lane else 1
        track_id = f"A{normalized_lane}" if track_type == TrackType.AUDIO else f"V{normalized_lane}"
        track = groups.setdefault(
            lane,
            ImportTrack(track_id, track_id, track_type, len(groups)),
        )
        track.clips.append(
            ImportClip(
                path=media_path,
                timeline_start=_seconds(item.attrib.get("offset")),
                source_in=_seconds(item.attrib.get("start")),
                duration=_seconds(item.attrib.get("duration")),
                name=item.attrib.get("name", asset_name),
            )
        )
        if not media_path.is_file():
            missing.append(str(media_path))
    markers = [
        {
            "at": _seconds(marker.attrib.get("start")),
            "label": marker.attrib.get("value", ""),
            "metadata": {
                "end": _seconds(marker.attrib.get("start"))
                + _seconds(marker.attrib.get("duration")),
                "note": marker.attrib.get("note", ""),
                "source": "fcpxml",
            },
        }
        for marker in root.findall("./library/event/project/sequence/spine/marker")
    ]
    return ExchangeImportPlan(
        path,
        "fcpxml",
        sorted(groups.values(), key=lambda item: item.order),
        {"markers": markers},
        warnings,
        sorted(set(missing)),
    )


def apply_import_plan(manager: ProjectManager, plan: ExchangeImportPlan) -> ProjectDocument:
    """Import referenced files, then atomically replace timeline objects."""

    if plan.missing_media:
        raise FileNotFoundError(
            f"Exchange import has {len(plan.missing_media)} missing media file(s)."
        )
    paths = list(dict.fromkeys(clip.path for track in plan.tracks for clip in track.clips))
    from facut.media.importer import MediaImporter

    importer = MediaImporter(manager)
    prepared = [importer._prepare(path) for path in paths]
    existing_by_hash = {
        asset.sha256: asset for asset in manager.require_document().media
    }
    by_path = {
        path.resolve(): existing_by_hash.get(asset.sha256, asset)
        for path, asset in zip(paths, prepared, strict=True)
    }

    def operation(document: ProjectDocument) -> ProjectDocument:
        existing_hashes = {asset.sha256 for asset in document.media}
        for asset in prepared:
            if asset.sha256 not in existing_hashes:
                document.media.append(asset)
                existing_hashes.add(asset.sha256)
        tracks: list[Track] = []
        known_clip_ids: set[str] = set()
        for spec in plan.tracks:
            clips: list[Clip] = []
            for item in spec.clips:
                asset = by_path[item.path.resolve()]
                values = dict(item.facut)
                values.update(
                    {
                        "media_id": asset.id,
                        "track_id": spec.id,
                        "timeline_start": item.timeline_start,
                    }
                )
                if not item.facut:
                    values.update(
                        {
                            "source_in": item.source_in,
                            "source_out": item.source_in + item.duration,
                        }
                    )
                clip = Clip.model_validate(values)
                if clip.id in known_clip_ids:
                    clip.id = f"{clip.id}_{len(known_clip_ids) + 1}"
                known_clip_ids.add(clip.id)
                clips.append(clip)
            tracks.append(
                Track(
                    id=spec.id,
                    name=spec.name,
                    type=spec.type,
                    order=spec.order,
                    clips=clips,
                )
            )
        document.tracks = tracks
        metadata = plan.metadata
        document.transitions = [
            Transition.model_validate(value)
            for value in metadata.get("transitions", [])
            if value.get("from_clip_id") in known_clip_ids
            and value.get("to_clip_id") in known_clip_ids
        ]
        document.text_overlays = [
            TextOverlay.model_validate(value) for value in metadata.get("text_overlays", [])
        ]
        document.subtitle_cues = [
            SubtitleCue.model_validate(value) for value in metadata.get("subtitle_cues", [])
        ]
        document.markers = [
            Marker.model_validate(value) for value in metadata.get("markers", [])
        ]
        document.settings["last_exchange_import"] = {
            "source": str(plan.source),
            "format": plan.format,
        }
        document.recompute_duration()
        return document

    _, state = manager.mutate(
        "exchange.import",
        f"Imported {plan.format.upper()} timeline from {plan.source.name}",
        operation,
        command={"source": plan.source.name, "format": plan.format},
    )
    return state
