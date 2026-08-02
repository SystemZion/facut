"""Deterministic OTIO and FCPXML timeline exporters."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import opentimelineio as otio

from facut.core.models import ProjectDocument, TrackType


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    path: Path
    format: str
    tracks: int
    clips: int
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path.resolve()),
            "format": self.format,
            "tracks": self.tracks,
            "clips": self.clips,
            "warnings": list(self.warnings),
        }


def _source_path(document: ProjectDocument, project_dir: Path, media_id: str) -> Path:
    asset = document.find_media(media_id)
    if asset is None:
        raise ValueError(f'Media "{media_id}" was not found.')
    path = Path(asset.path)
    return path.resolve() if path.is_absolute() else (project_dir / path).resolve()


def export_otio(
    document: ProjectDocument,
    project_dir: str | Path,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> ExchangeResult:
    """Write native OTIO JSON while retaining FACUT-only data in metadata."""

    destination = Path(output)
    if destination.exists() and not overwrite:
        raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    rate = document.project.fps
    timeline = otio.schema.Timeline(
        name=document.project.name,
        global_start_time=otio.opentime.RationalTime(0, rate),
        metadata={
            "facut": {
                "project_id": document.project.id,
                "revision": document.revision,
                "canvas": [document.project.width, document.project.height],
                "transitions": [item.model_dump(mode="json") for item in document.transitions],
                "text_overlays": [item.model_dump(mode="json") for item in document.text_overlays],
                "subtitle_cues": [item.model_dump(mode="json") for item in document.subtitle_cues],
            }
        },
    )
    exported_clips = 0
    warnings: list[str] = []
    for source_track in sorted(document.tracks, key=lambda item: item.order):
        if source_track.type not in {TrackType.VIDEO, TrackType.IMAGE, TrackType.AUDIO}:
            warnings.append(f"Track {source_track.id} ({source_track.type.value}) is preserved only in FACUT metadata.")
            continue
        kind = (
            otio.schema.TrackKind.Audio
            if source_track.type == TrackType.AUDIO
            else otio.schema.TrackKind.Video
        )
        track = otio.schema.Track(
            name=source_track.name,
            kind=kind,
            metadata={"facut": {"track_id": source_track.id, "type": source_track.type.value}},
        )
        cursor = 0.0
        for clip in sorted(source_track.clips, key=lambda item: item.timeline_start):
            if not clip.enabled:
                continue
            if clip.timeline_start > cursor:
                track.append(
                    otio.schema.Gap(
                        source_range=otio.opentime.TimeRange(
                            otio.opentime.RationalTime(0, rate),
                            otio.opentime.RationalTime.from_seconds(clip.timeline_start - cursor, rate),
                        )
                    )
                )
            elif clip.timeline_start < cursor - 1e-6:
                warnings.append(
                    f"Clip {clip.id} overlaps its OTIO track; original timeline_start is retained in metadata."
                )
            asset = document.find_media(clip.media_id)
            assert asset is not None
            reference = otio.schema.ExternalReference(
                target_url=_source_path(document, Path(project_dir), clip.media_id).as_uri(),
                available_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(0, rate),
                    otio.opentime.RationalTime.from_seconds(
                        asset.technical.duration or clip.source_out, rate
                    ),
                ),
                metadata={
                    "facut": {"media_id": asset.id, "sha256": asset.sha256}
                },
            )
            otio_clip = otio.schema.Clip(
                name=asset.original_name,
                media_reference=reference,
                source_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime.from_seconds(clip.source_in, rate),
                    otio.opentime.RationalTime.from_seconds(clip.duration, rate),
                ),
                metadata={"facut": clip.model_dump(mode="json")},
            )
            track.append(otio_clip)
            cursor = max(cursor, clip.end)
            exported_clips += 1
        timeline.tracks.append(track)
    for marker in document.markers:
        duration = max(0.0, float(marker.metadata.get("end", marker.at)) - marker.at)
        timeline.tracks.markers.append(
            otio.schema.Marker(
                name=marker.label,
                marked_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime.from_seconds(marker.at, rate),
                    otio.opentime.RationalTime.from_seconds(duration, rate),
                ),
                metadata={"facut": marker.model_dump(mode="json")},
            )
        )
    otio.adapters.write_to_file(timeline, str(destination), adapter_name="otio_json")
    return ExchangeResult(destination, "otio", len(timeline.tracks), exported_clips, tuple(warnings))


def _time(seconds: float, fps: float) -> str:
    frames = round(seconds * fps)
    rate = Fraction(str(fps)).limit_denominator(1001)
    value = Fraction(frames, 1) / rate
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}s"


def export_fcpxml(
    document: ProjectDocument,
    project_dir: str | Path,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> ExchangeResult:
    """Write a conservative FCPXML 1.9 document and report lossy features."""

    destination = Path(output)
    if destination.exists() and not overwrite:
        raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fps = document.project.fps
    root = ET.Element("fcpxml", {"version": "1.9"})
    resources = ET.SubElement(root, "resources")
    format_id = "r1"
    ET.SubElement(
        resources,
        "format",
        {
            "id": format_id,
            "name": f"FACUT {document.project.width}x{document.project.height} {fps:g}p",
            "frameDuration": _time(1 / fps, fps),
            "width": str(document.project.width),
            "height": str(document.project.height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )
    asset_refs: dict[str, str] = {}
    for index, asset in enumerate(document.media, start=2):
        ref = f"r{index}"
        asset_refs[asset.id] = ref
        attributes = {
            "id": ref,
            "name": asset.original_name,
            "src": _source_path(document, Path(project_dir), asset.id).as_uri(),
            "start": "0s",
            "duration": _time(asset.technical.duration or 0, fps),
            "format": format_id,
            "hasVideo": "1" if asset.technical.video_codec or asset.kind.value == "image" else "0",
            "hasAudio": "1" if asset.technical.audio_codec else "0",
        }
        ET.SubElement(resources, "asset", attributes)
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": document.project.name})
    project = ET.SubElement(event, "project", {"name": document.project.name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": _time(document.project.duration, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    exported_clips = 0
    warnings: list[str] = []
    supported_tracks = [
        track
        for track in sorted(document.tracks, key=lambda item: item.order)
        if track.type in {TrackType.VIDEO, TrackType.IMAGE, TrackType.AUDIO}
    ]
    for track_index, track in enumerate(supported_tracks):
        lane = 0 if track_index == 0 and track.type != TrackType.AUDIO else track_index + 1
        if track.type == TrackType.AUDIO:
            lane = -max(1, track_index)
        for clip in sorted(track.clips, key=lambda item: item.timeline_start):
            if not clip.enabled:
                continue
            attributes = {
                "name": document.find_media(clip.media_id).original_name,
                "ref": asset_refs[clip.media_id],
                "offset": _time(clip.timeline_start, fps),
                "start": _time(clip.source_in, fps),
                "duration": _time(clip.duration, fps),
            }
            if lane:
                attributes["lane"] = str(lane)
            ET.SubElement(spine, "asset-clip", attributes)
            exported_clips += 1
            if clip.effects or clip.keyframes or clip.transform.model_dump() != clip.transform.__class__().model_dump():
                warnings.append(f"Clip {clip.id} effects/transform may be lossy in FCPXML 1.9.")
    if document.transitions:
        warnings.append("FACUT transitions are not emitted in the conservative FCPXML 1.9 subset.")
    if document.text_overlays or document.subtitle_cues:
        warnings.append("FACUT text and subtitle styling is not emitted in the conservative FCPXML subset.")
    for marker in document.markers:
        ET.SubElement(
            spine,
            "marker",
            {
                "start": _time(marker.at, fps),
                "duration": _time(
                    max(0.0, float(marker.metadata.get("end", marker.at)) - marker.at), fps
                ),
                "value": marker.label,
                "note": str(marker.metadata.get("note", "")),
            },
        )
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return ExchangeResult(destination, "fcpxml", len(supported_tracks), exported_clips, tuple(dict.fromkeys(warnings)))
