"""Compile project text into an ASS sidecar without coupling to a render backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facut.core.models import ProjectDocument, TextOverlay, TextStyle
from facut.subtitles.templates import resolve_text_template


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
        caption_scale = document.project.height / 1080.0
        styles: list[TextStyle] = [
            TextStyle(
                font_family="Source Han Sans SC",
                font_size=52 * caption_scale,
                font_weight="medium",
                stroke_color="#000000",
                stroke_width=2 * caption_scale,
                shadow=1 * caption_scale,
                alignment="center",
                safe_area=True,
                safe_margin_percent=5.0,
            )
        ]
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
            if overlay.template:
                definition = resolve_text_template(overlay.template)
                renderer = definition.get("renderer", "standard")
                if renderer.startswith("lingang-"):
                    events.extend(
                        self._lingang_events(document, overlay, definition, styles)
                    )
                    continue
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
        layer: int = 0,
    ) -> str:
        return (
            f"Dialogue: {layer},{self._ass_time(start)},{self._ass_time(end)},"
            f"{style_name},,0,0,0,{effect},{text}"
        )

    def _lingang_events(
        self,
        document: ProjectDocument,
        overlay: TextOverlay,
        definition: dict[str, object],
        styles: list[TextStyle],
    ) -> list[str]:
        """Render the Lingang editorial system as ASS vector and text layers."""

        width = document.project.width
        height = document.project.height
        scale = height / 1080.0
        parameters = dict(definition.get("parameters", {}))
        parameters.update(overlay.template_parameters)
        x = self._position(overlay.x, width, axis="x")
        y = self._position(overlay.y, height, axis="y")
        panel_width = width * float(parameters.get("panel_width_percent", 37.5)) / 100
        panel_height = height * float(parameters.get("panel_height_percent", 14.8)) / 100
        radius = float(parameters.get("corner_radius", 22.0)) * scale

        outline = self._shape_event(
            overlay,
            self._rounded_rectangle(x, y, panel_width, panel_height, radius),
            str(parameters.get("panel_outline", "#FFFFFF18")),
            layer=0,
        )
        inset = max(1.0, scale)
        panel = self._shape_event(
            overlay,
            self._rounded_rectangle(
                x + inset,
                y + inset,
                panel_width - 2 * inset,
                panel_height - 2 * inset,
                max(1.0, radius - inset),
            ),
            str(parameters.get("panel_color", "#030A12A4")),
            layer=1,
        )
        accent_width = max(5.0 * scale, panel_width * 0.009)
        accent_margin = 22.0 * scale
        accent = self._shape_event(
            overlay,
            self._rounded_rectangle(
                x + 20.0 * scale,
                y + accent_margin,
                accent_width,
                panel_height - 2 * accent_margin,
                accent_width / 2,
            ),
            str(parameters.get("accent_color", "#66E1FF")),
            layer=2,
        )

        text_x = x + 52.0 * scale
        main_title = definition.get("renderer") == "lingang-main-title"
        title_y = y + (58.0 if main_title else 36.0) * scale
        title_style = overlay.style.model_copy(
            update={"font_size": overlay.style.font_size * scale, "background": None}
        )
        title_name = self._style_name(styles, title_style)
        title = self._dialogue(
            overlay.at,
            overlay.end,
            title_name,
            f"{{\\an7\\pos({text_x:.2f},{title_y:.2f})}}{self._escape(overlay.text)}",
            effect=overlay.entrance or "",
            layer=3,
        )
        events = [outline, panel, accent, title]
        if overlay.subtitle:
            subtitle_size = float(parameters.get("subtitle_font_size", 25.0)) * scale
            subtitle_style = overlay.style.model_copy(
                update={
                    "font_size": subtitle_size,
                    "font_weight": "normal",
                    "color": str(parameters.get("subtitle_color", "#BEDEE8")),
                    "background": None,
                }
            )
            subtitle_name = self._style_name(styles, subtitle_style)
            subtitle_y = y + (158.0 if main_title else 94.0) * scale
            events.append(
                self._dialogue(
                    overlay.at,
                    overlay.end,
                    subtitle_name,
                    f"{{\\an7\\pos({text_x:.2f},{subtitle_y:.2f})}}"
                    f"{self._escape(overlay.subtitle)}",
                    layer=4,
                )
            )
        return events

    def _shape_event(
        self, overlay: TextOverlay, drawing: str, color: str, *, layer: int
    ) -> str:
        bgr, alpha = self._inline_ass_color(color)
        text = (
            f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1"
            f"\\1c&H{bgr}&\\1a&H{alpha}&}}{drawing}{{\\p0}}"
        )
        return self._dialogue(
            overlay.at, overlay.end, "Style0", text, layer=layer
        )

    @staticmethod
    def _inline_ass_color(value: str) -> tuple[str, str]:
        raw = value.lstrip("#")
        red, green, blue = raw[0:2], raw[2:4], raw[4:6]
        opacity = int(raw[6:8], 16) if len(raw) == 8 else 255
        return f"{blue}{green}{red}", f"{255 - opacity:02X}"

    @staticmethod
    def _rounded_rectangle(x: float, y: float, width: float, height: float, radius: float) -> str:
        x0, y0 = x, y
        x1, y1 = x + width, y + height
        radius = min(radius, width / 2, height / 2)
        k = radius * 0.55228475
        return (
            f"m {x0 + radius:.0f} {y0:.0f} "
            f"l {x1 - radius:.0f} {y0:.0f} "
            f"b {x1 - radius + k:.0f} {y0:.0f} {x1:.0f} {y0 + radius - k:.0f} {x1:.0f} {y0 + radius:.0f} "
            f"l {x1:.0f} {y1 - radius:.0f} "
            f"b {x1:.0f} {y1 - radius + k:.0f} {x1 - radius + k:.0f} {y1:.0f} {x1 - radius:.0f} {y1:.0f} "
            f"l {x0 + radius:.0f} {y1:.0f} "
            f"b {x0 + radius - k:.0f} {y1:.0f} {x0:.0f} {y1 - radius + k:.0f} {x0:.0f} {y1 - radius:.0f} "
            f"l {x0:.0f} {y0 + radius:.0f} "
            f"b {x0:.0f} {y0 + radius - k:.0f} {x0 + radius - k:.0f} {y0:.0f} {x0 + radius:.0f} {y0:.0f}"
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
