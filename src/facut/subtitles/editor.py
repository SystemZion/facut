"""Non-destructive project operations for subtitles and free text."""

from __future__ import annotations

from collections.abc import Iterable

from facut.core.models import (
    ProjectDocument,
    SubtitleCue,
    TextOverlay,
    TextStyle,
    TrackType,
)
from facut.subtitles.formats import ParsedCue


class SubtitleEditError(ValueError):
    """A recoverable subtitle timeline validation error."""

    code = "SUBTITLE_EDIT_ERROR"


def require_subtitle_track(document: ProjectDocument, track_id: str):
    track = document.find_track(track_id)
    if track is None:
        raise SubtitleEditError(f'Subtitle track "{track_id}" was not found.')
    if track.type != TrackType.SUBTITLE:
        raise SubtitleEditError(f'Track "{track_id}" is not a subtitle track.')
    if track.locked:
        raise SubtitleEditError(f'Subtitle track "{track_id}" is locked.')
    return track


def import_cues(
    document: ProjectDocument,
    track_id: str,
    parsed_cues: Iterable[ParsedCue],
    *,
    offset: float = 0.0,
    source: str | None = None,
) -> list[SubtitleCue]:
    """Append parsed cues to one subtitle track."""

    require_subtitle_track(document, track_id)
    created: list[SubtitleCue] = []
    for parsed in parsed_cues:
        start = parsed.start + offset
        end = parsed.end + offset
        if start < 0:
            raise SubtitleEditError("Subtitle offset would move a cue before time zero.")
        created.append(
            SubtitleCue(
                track_id=track_id,
                start=start,
                end=end,
                text=parsed.text,
                source_identifier=parsed.identifier,
                settings=parsed.settings,
                metadata={"source": source} if source else {},
            )
        )
    document.subtitle_cues.extend(created)
    document.subtitle_cues.sort(key=lambda cue: (cue.start, cue.end, cue.id))
    document.recompute_duration()
    return created


def shift_track(document: ProjectDocument, track_id: str, offset: float) -> list[SubtitleCue]:
    """Shift all cues on a subtitle track by a signed offset."""

    require_subtitle_track(document, track_id)
    cues = [cue for cue in document.subtitle_cues if cue.track_id == track_id]
    if any(cue.start + offset < 0 for cue in cues):
        raise SubtitleEditError("Subtitle shift would move a cue before time zero.")
    for cue in cues:
        cue.start += offset
        cue.end += offset
    document.subtitle_cues.sort(key=lambda cue: (cue.start, cue.end, cue.id))
    document.recompute_duration()
    return cues


def remove_cue(document: ProjectDocument, cue_id: str) -> SubtitleCue:
    for cue in document.subtitle_cues:
        if cue.id == cue_id:
            require_subtitle_track(document, cue.track_id)
            document.subtitle_cues.remove(cue)
            document.recompute_duration()
            return cue
    raise SubtitleEditError(f'Subtitle cue "{cue_id}" was not found.')


def add_text_overlay(
    document: ProjectDocument,
    *,
    text: str,
    at: float,
    duration: float,
    x: float | str = "center",
    y: float | str = "80%",
    track_id: str | None = None,
    style: TextStyle | None = None,
    entrance: str | None = None,
    exit: str | None = None,
) -> TextOverlay:
    if track_id is not None and document.find_track(track_id) is None:
        raise SubtitleEditError(f'Track "{track_id}" was not found.')
    overlay = TextOverlay(
        text=text,
        at=at,
        duration=duration,
        x=x,
        y=y,
        track_id=track_id,
        style=style or TextStyle(),
        entrance=entrance,
        exit=exit,
    )
    document.text_overlays.append(overlay)
    document.text_overlays.sort(key=lambda item: (item.at, item.id))
    document.recompute_duration()
    return overlay


def remove_text_overlay(document: ProjectDocument, overlay_id: str) -> TextOverlay:
    for overlay in document.text_overlays:
        if overlay.id == overlay_id:
            document.text_overlays.remove(overlay)
            document.recompute_duration()
            return overlay
    raise SubtitleEditError(f'Text overlay "{overlay_id}" was not found.')
