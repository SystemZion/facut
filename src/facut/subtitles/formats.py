"""Deterministic SRT and WebVTT parsing and serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable

from facut.core.models import SubtitleCue


_SRT_TIMING = re.compile(
    r"^(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{3})(?:\s+(?P<settings>.*))?$"
)
_VTT_TIMING = re.compile(
    r"^(?P<start>(?:\d{1,3}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*"
    r"(?P<end>(?:\d{1,3}:)?\d{2}:\d{2}\.\d{3})(?:\s+(?P<settings>.*))?$"
)


class SubtitleFormatError(ValueError):
    """Raised when a subtitle document cannot be parsed safely."""

    code = "SUBTITLE_PARSE_ERROR"


@dataclass(frozen=True, slots=True)
class ParsedCue:
    """Renderer-independent cue returned by subtitle parsers."""

    start: float
    end: float
    text: str
    identifier: str | None = None
    settings: dict[str, str] = field(default_factory=dict)


def _timestamp_seconds(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise SubtitleFormatError(f"Invalid subtitle timestamp: {value}")
    try:
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError as exc:
        raise SubtitleFormatError(f"Invalid subtitle timestamp: {value}") from exc
    if int(minutes) > 59 or float(seconds) >= 60:
        raise SubtitleFormatError(f"Invalid subtitle timestamp: {value}")
    return total


def _settings(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    result: dict[str, str] = {}
    for token in value.split():
        if ":" in token:
            key, setting = token.split(":", 1)
            result[key] = setting
        else:
            result[token] = ""
    return result


def _make_cue(
    match: re.Match[str],
    text_lines: list[str],
    identifier: str | None,
    *,
    block_number: int,
) -> ParsedCue:
    text = "\n".join(text_lines).strip()
    if not text:
        raise SubtitleFormatError(f"Subtitle cue {block_number} has no text.")
    start = _timestamp_seconds(match["start"])
    end = _timestamp_seconds(match["end"])
    if end <= start:
        raise SubtitleFormatError(
            f"Subtitle cue {block_number} must end after it starts."
        )
    return ParsedCue(
        start=start,
        end=end,
        text=text,
        identifier=identifier,
        settings=_settings(match.groupdict().get("settings")),
    )


def parse_srt(content: str) -> list[ParsedCue]:
    """Parse UTF-decoded SubRip text into ordered cues."""

    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", normalized.strip())
    cues: list[ParsedCue] = []
    for block_number, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if not lines:
            continue
        identifier: str | None = None
        timing_index = 0
        if not _SRT_TIMING.fullmatch(lines[0].strip()):
            identifier = lines[0].strip() or None
            timing_index = 1
        if timing_index >= len(lines):
            raise SubtitleFormatError(f"Subtitle block {block_number} has no timing line.")
        timing = _SRT_TIMING.fullmatch(lines[timing_index].strip())
        if timing is None:
            raise SubtitleFormatError(
                f"Invalid timing line in subtitle block {block_number}: "
                f"{lines[timing_index]!r}"
            )
        cues.append(
            _make_cue(
                timing,
                lines[timing_index + 1 :],
                identifier,
                block_number=block_number,
            )
        )
    return cues


def parse_vtt(content: str) -> list[ParsedCue]:
    """Parse WebVTT, ignoring NOTE/STYLE/REGION metadata blocks."""

    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or not lines[0].strip().startswith("WEBVTT"):
        raise SubtitleFormatError("WebVTT input must begin with WEBVTT.")
    body_start = 1
    header_end = body_start
    while header_end < len(lines) and lines[header_end].strip():
        header_end += 1
    header_metadata = lines[body_start:header_end]
    if header_metadata and not any("-->" in line for line in header_metadata):
        body_start = min(header_end + 1, len(lines))
    elif body_start < len(lines) and not lines[body_start].strip():
        body_start += 1
    blocks = re.split(r"\n[ \t]*\n", "\n".join(lines[body_start:]).strip())
    cues: list[ParsedCue] = []
    for block_number, block in enumerate(blocks, 1):
        block_lines = block.splitlines()
        if not block_lines:
            continue
        first = block_lines[0].strip()
        if first.startswith(("NOTE", "STYLE", "REGION")):
            continue
        identifier: str | None = None
        timing_index = 0
        if not _VTT_TIMING.fullmatch(first):
            identifier = first or None
            timing_index = 1
        if timing_index >= len(block_lines):
            raise SubtitleFormatError(f"WebVTT block {block_number} has no timing line.")
        timing = _VTT_TIMING.fullmatch(block_lines[timing_index].strip())
        if timing is None:
            raise SubtitleFormatError(
                f"Invalid timing line in WebVTT block {block_number}: "
                f"{block_lines[timing_index]!r}"
            )
        cues.append(
            _make_cue(
                timing,
                block_lines[timing_index + 1 :],
                identifier,
                block_number=block_number,
            )
        )
    return cues


def _decode_subtitle(path: Path) -> str:
    raw = path.read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise SubtitleFormatError(
        f'Could not decode subtitle "{path.name}" as UTF-8, UTF-16, or GB18030.'
    )


def parse_subtitle_file(
    path: str | Path, subtitle_format: str | None = None
) -> list[ParsedCue]:
    """Parse an SRT or VTT file selected by option or extension."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f'Subtitle file "{source}" was not found.')
    selected = (subtitle_format or source.suffix.lstrip(".")).lower()
    content = _decode_subtitle(source)
    if selected == "srt":
        return parse_srt(content)
    if selected in {"vtt", "webvtt"}:
        return parse_vtt(content)
    raise SubtitleFormatError(
        f'Unsupported subtitle format "{selected}"; use srt or vtt.'
    )


def _timecode(seconds: float, separator: str, *, short_hours: bool = False) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    if short_hours and hours == 0:
        return f"{minutes:02d}:{whole_seconds:02d}{separator}{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{millis:03d}"


def _cue_values(cue: ParsedCue | SubtitleCue) -> tuple[float, float, str, str | None, dict[str, str]]:
    if isinstance(cue, ParsedCue):
        return cue.start, cue.end, cue.text, cue.identifier, cue.settings
    return cue.start, cue.end, cue.text, cue.source_identifier, cue.settings


def export_srt(cues: Iterable[ParsedCue | SubtitleCue]) -> str:
    """Serialize cues as normalized UTF-8 SubRip text."""

    blocks: list[str] = []
    for index, cue in enumerate(sorted(cues, key=lambda item: _cue_values(item)[0]), 1):
        start, end, text, _, _ = _cue_values(cue)
        blocks.append(
            f"{index}\n{_timecode(start, ',')} --> {_timecode(end, ',')}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_vtt(cues: Iterable[ParsedCue | SubtitleCue]) -> str:
    """Serialize cues as normalized WebVTT text."""

    blocks: list[str] = []
    for cue in sorted(cues, key=lambda item: _cue_values(item)[0]):
        start, end, text, identifier, settings = _cue_values(cue)
        timing = (
            f"{_timecode(start, '.', short_hours=True)} --> "
            f"{_timecode(end, '.', short_hours=True)}"
        )
        if settings:
            timing += " " + " ".join(
                f"{key}:{value}" if value else key for key, value in settings.items()
            )
        prefix = f"{identifier}\n" if identifier else ""
        blocks.append(f"{prefix}{timing}\n{text}")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")
