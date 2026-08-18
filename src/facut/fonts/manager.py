"""License-aware font catalog with deterministic logical-role fallbacks."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_data_path


FONT_ROLES: dict[str, list[str]] = {
    "caption-sans": ["Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Arial Unicode MS"],
    "documentary-serif": ["Source Han Serif SC", "Noto Serif CJK SC", "SimSun", "Songti SC", "STSong"],
    "cinematic-light": ["Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI Light", "PingFang SC"],
    "comedy-heavy": ["庞门正道粗书体", "PangMenZhengDao", "Source Han Sans SC Heavy", "Microsoft YaHei"],
    "friendly-rounded": ["HarmonyOS Sans SC", "MiSans", "Microsoft YaHei", "PingFang SC"],
    "food-handwritten": ["庞门正道粗书体", "KaiTi", "STKaiti", "Source Han Sans SC"],
    "science-geometric": ["Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC"],
    "brush-accent": ["庞门正道粗书体", "KaiTi", "STKaiti", "Source Han Serif SC"],
}


def _catalog_root() -> Path:
    root = Path(user_data_path("facut", appauthor=False)) / "fonts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _font_directories() -> list[Path]:
    system = platform.system().casefold()
    if system == "windows":
        values = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            values.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        return values
    if system == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts", Path.home() / ".fonts"]


def _font_metadata(path: Path) -> dict[str, Any]:
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(path, fontNumber=0, lazy=True)
        names = font["name"].names
        family = None
        style = None
        for record in names:
            if record.nameID not in {1, 2, 16, 17}:
                continue
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if record.nameID in {16, 1} and not family:
                family = value
            if record.nameID in {17, 2} and not style:
                style = value
        cmap = font.getBestCmap() or {}
        font.close()
        return {
            "family": family or path.stem,
            "style": style or "Regular",
            "glyph_count": len(cmap),
            "coverage": {
                "ascii": all(code in cmap for code in (65, 90, 97, 122)),
                "cjk": any(0x4E00 <= code <= 0x9FFF for code in cmap),
            },
            "codepoints": sorted(cmap),
            "readable": True,
            "error": None,
        }
    except Exception as error:
        return {
            "family": path.stem,
            "style": "Unknown",
            "glyph_count": None,
            "coverage": {"ascii": None, "cjk": None},
            "codepoints": [],
            "readable": False,
            "error": str(error),
        }


class FontCatalog:
    """Scan installed fonts while keeping user font files in their original location."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else _catalog_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / "registry.json"
        self.scan_path = self.root / "scan.json"

    def _registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"version": "1.0", "fonts": []}
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def scan(self, *, refresh: bool = False) -> dict[str, Any]:
        if self.scan_path.is_file() and not refresh:
            return json.loads(self.scan_path.read_text(encoding="utf-8"))
        paths: set[Path] = set()
        for directory in _font_directories():
            if directory.is_dir():
                paths.update(
                    item.resolve()
                    for item in directory.rglob("*")
                    if item.is_file() and item.suffix.casefold() in {".ttf", ".otf", ".ttc", ".otc"}
                )
        registered = self._registry()["fonts"]
        paths.update(Path(item["path"]) for item in registered if Path(item["path"]).is_file())
        registered_by_path = {str(Path(item["path"]).resolve()): item for item in registered}
        fonts = []
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            metadata = _font_metadata(path)
            registration = registered_by_path.get(str(path.resolve()))
            fonts.append(
                {
                    "path": str(path),
                    **{key: value for key, value in metadata.items() if key != "codepoints"},
                    "registered": registration is not None,
                    "license_file": registration.get("license_file") if registration else None,
                    "source": "user-registered" if registration else "system",
                }
            )
        payload = {"version": "1.0", "font_count": len(fonts), "fonts": fonts, "roles": FONT_ROLES}
        _atomic_json(self.scan_path, payload)
        return payload

    def register(self, path: str | Path, *, license_file: str | Path) -> dict[str, Any]:
        font_path = Path(path).expanduser().resolve()
        license_path = Path(license_file).expanduser().resolve()
        if not font_path.is_file():
            raise FileNotFoundError(f'Font file "{font_path}" was not found.')
        if font_path.suffix.casefold() not in {".ttf", ".otf", ".ttc", ".otc"}:
            raise ValueError("Font registration requires a TTF, OTF, TTC, or OTC file.")
        if not license_path.is_file():
            raise FileNotFoundError(f'Font license file "{license_path}" was not found.')
        metadata = _font_metadata(font_path)
        if not metadata["readable"]:
            raise ValueError(f'Font metadata could not be read: {metadata["error"]}')
        registry = self._registry()
        record = {
            "path": str(font_path),
            "sha256": _sha256(font_path),
            "license_file": str(license_path),
            "license_sha256": _sha256(license_path),
            "family": metadata["family"],
            "style": metadata["style"],
        }
        registry["fonts"] = [item for item in registry["fonts"] if item["path"] != str(font_path)]
        registry["fonts"].append(record)
        _atomic_json(self.registry_path, registry)
        self.scan_path.unlink(missing_ok=True)
        return record

    def match(self, role: str, *, language: str = "zh-CN") -> dict[str, Any]:
        if role not in FONT_ROLES:
            raise ValueError(f'Unknown font role "{role}". Available: {", ".join(FONT_ROLES)}.')
        scan = self.scan()
        by_family: dict[str, list[dict[str, Any]]] = {}
        for item in scan["fonts"]:
            by_family.setdefault(str(item["family"]).casefold(), []).append(item)
        requires_cjk = language.casefold().startswith(("zh", "ja", "ko"))
        candidates = []
        selected = None
        for family in FONT_ROLES[role]:
            matches = by_family.get(family.casefold(), [])
            usable = next(
                (
                    item
                    for item in matches
                    if item["readable"] and (not requires_cjk or item["coverage"]["cjk"] is True)
                ),
                None,
            )
            candidates.append({"family": family, "available": usable is not None})
            if selected is None and usable is not None:
                selected = usable
        if selected is None:
            raise FileNotFoundError(
                f'No installed font can satisfy role "{role}" for language {language}.'
            )
        return {
            "role": role,
            "language": language,
            "selected_family": selected["family"],
            "selected_path": selected["path"],
            "source": selected["source"],
            "license_file": selected["license_file"],
            "candidates": candidates,
        }

    def supports_text(self, font_path: str | Path, text: str) -> dict[str, Any]:
        metadata = _font_metadata(Path(font_path))
        codepoints = set(metadata.pop("codepoints"))
        missing = sorted({character for character in text if not character.isspace() and ord(character) not in codepoints})
        return {"supported": not missing, "missing_characters": missing, **metadata}

    def audit_project(self, document: Any, *, language: str = "zh-CN") -> dict[str, Any]:
        scan = self.scan()
        by_family = {str(item["family"]).casefold(): item for item in scan["fonts"]}
        issues = []
        checked = 0
        for item in [*document.subtitle_cues, *document.text_overlays]:
            style = item.style
            family = style.font_family if style else None
            if not family:
                matched = self.match("caption-sans", language=language)
                family = matched["selected_family"]
            font = by_family.get(family.casefold())
            checked += 1
            if font is None or not Path(font["path"]).is_file():
                issues.append({"code": "FONT_MISSING", "item_id": item.id, "family": family})
                continue
            coverage = self.supports_text(font["path"], item.text)
            if not coverage["supported"]:
                issues.append(
                    {
                        "code": "FONT_MISSING_GLYPH",
                        "item_id": item.id,
                        "family": family,
                        "characters": coverage["missing_characters"],
                    }
                )
        return {
            "status": "pass" if not issues else "fail",
            "checked_items": checked,
            "issues": issues,
            "font_count": scan["font_count"],
        }
