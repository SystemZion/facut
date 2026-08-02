"""Compile project text into an ASS sidecar without coupling to a render backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facut.core.models import ProjectDocument, TextStyle


@dataclass(frozen=True, slots=True)
class SubtitleRenderPlan:
    """Independent artifact that a render backend may consume later."""

    format: str
    content: str
    cue_count: int
    text_overlay_count: int

    def write(self, path: str | Path, *, overwrite: bool = False) -> Path:
        destination = Path(path)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f'Output "{destination}" exists; use overwrite to replace it.'
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.content, encoding="utf-8-sig", newline="\n")
        return destination


class SubtitleCompiler:
    """Compile cues and overlays to a portable Advanced SubStation Alpha file."""

    def compile(self, document: ProjectDocument) -> SubtitleRenderPlan:
        styles: list[TextStyle] = [TextStyle(font_size=52)]
        events: list[str] = []
        for cue in sorted(document.subtitle_cues, key=lambda item: (item.start, item.id)):
            style = cue.style or styles[0]
            style_name = self._style_name(styles, style)
            events.append(
                self._dialogue(cue.start, cue.end, style_name, self._escape(cue.text))
            )
        for overlay in sorted(
            (item for item in document.text_overlays if item.enabled),
            key=lambda item: (item.at, item.id),
        ):
            style_name = self._style_name(styles, overlay.style)
            x = self._position(overlay.x, document.project.width, axis="x")
            y = self._position(overlay.y, document.project.height, axis="y")
            if overlay.style.safe_area:
                x_margin = (
                    document.project.width
                    * overlay.style.safe_margin_percent
                    / 100.0
                )
                y_margin = (
                    document.project.height
                    * overlay.style.safe_margin_percent
                    / 100.0
                )
                x = min(max(x, x_margin), document.project.width - x_margin)
                y = min(max(y, y_margin), document.project.height - y_margin)
            text = f"{{\\an5\\pos({x:.2f},{y:.2f})}}{self._escape(overlay.text)}"
            events.append(
                self._dialogue(
                    overlay.at, overlay.end, style_name, text, effect=overlay.entrance or ""
                )
            )
        style_lines = [
            self._style_line(f"Style{index}", style)
            for index, style in enumerate(styles)
        ]
        content = "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                f"PlayResX: {document.project.width}",
                f"PlayResY: {document.project.height}",
                "WrapStyle: 0",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding",
                *style_lines,
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text",
                *events,
                "",
            ]
        )
        return SubtitleRenderPlan(
            format="ass",
            content=content,
            cue_count=len(document.subtitle_cues),
            text_overlay_count=sum(1 for item in document.text_overlays if item.enabled),
        )

    @staticmethod
    def _style_name(styles: list[TextStyle], target: TextStyle) -> str:
        for index, style in enumerate(styles):
            if style == target:
                return f"Style{index}"
        styles.append(target)
        return f"Style{len(styles) - 1}"

    @staticmethod
    def _ass_color(value: str | None, *, default: str = "#00000000") -> str:
        value = value or default
        raw = value.lstrip("#")
        red, green, blue = raw[0:2], raw[2:4], raw[4:6]
        alpha = raw[6:8] if len(raw) == 8 else "FF"
        ass_alpha = 255 - int(alpha, 16)
        return f"&H{ass_alpha:02X}{blue}{green}{red}"

    def _style_line(self, name: str, style: TextStyle) -> str:
        bold = -1 if style.font_weight.lower() in {"bold", "600", "700", "800", "900"} else 0
        border_style = 3 if style.background else 1
        alignment = {"left": 1, "center": 2, "right": 3}[style.alignment]
        return (
            f"Style: {name},{style.font_family or 'Arial'},{style.font_size:g},"
            f"{self._ass_color(style.color)},&H000000FF,"
            f"{self._ass_color(style.stroke_color)},{self._ass_color(style.background)},"
            f"{bold},0,0,0,100,100,{style.letter_spacing:g},0,{border_style},"
            f"{style.stroke_width:g},{style.shadow:g},{alignment},40,40,40,1"
        )

    @staticmethod
    def _ass_time(seconds: float) -> str:
        centiseconds = round(seconds * 100)
        hours, remainder = divmod(centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        whole_seconds, centis = divmod(remainder, 100)
        return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centis:02d}"

    def _dialogue(
        self,
        start: float,
        end: float,
        style_name: str,
        text: str,
        *,
        effect: str = "",
    ) -> str:
        return (
            f"Dialogue: 0,{self._ass_time(start)},{self._ass_time(end)},"
            f"{style_name},,0,0,0,{effect},{text}"
        )

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", r"\N")
        )

    @staticmethod
    def _position(value: float | str, extent: int, *, axis: str) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        normalized = value.lower()
        if normalized.endswith("%"):
            return float(normalized[:-1]) * extent / 100.0
        named = {
            "x": {"left": 0.1, "center": 0.5, "right": 0.9},
            "y": {"top": 0.1, "center": 0.5, "bottom": 0.9},
        }
        if normalized in named[axis]:
            return named[axis][normalized] * extent
        return float(normalized)
