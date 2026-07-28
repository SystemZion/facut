"""Cross-platform configuration loading and persistence."""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path, user_config_path, user_log_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from facut.exceptions import InvalidArgumentError


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codec: str = "h264"
    hardware: str = "auto"
    overwrite: bool = False


class PreviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    height: int = Field(default=540, ge=144, le=4320)
    fps: float = Field(default=24.0, gt=0, le=240)


class CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: Path = Field(default_factory=lambda: user_cache_path("facut"))
    max_size_gb: float = Field(default=20.0, gt=0)


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg: str = Field(default_factory=lambda: bundled_tool("ffmpeg"))
    ffprobe: str = Field(default_factory=lambda: bundled_tool("ffprobe"))


class AppConfig(BaseModel):
    """Validated global facut settings."""

    model_config = ConfigDict(extra="forbid")

    render: RenderConfig = Field(default_factory=RenderConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)
    log_level: str = "INFO"
    temporary_directory: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / "facut")


def default_config_path() -> Path:
    """Return the platform-native configuration file location."""

    override = os.environ.get("FACUT_CONFIG")
    return Path(override).expanduser() if override else user_config_path("facut") / "config.toml"


def default_log_directory() -> Path:
    return user_log_path("facut")


def bundled_tool(name: str) -> str:
    """Prefer media tools embedded by PyInstaller, then adjacent portable tools."""

    executable = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "facut_bin" / executable)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "facut_bin" / executable)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return name


def load_config(path: Path | None = None) -> AppConfig:
    """Load TOML configuration, returning validated defaults if absent."""

    config_path = path or default_config_path()
    if not config_path.exists():
        config = AppConfig()
    else:
        try:
            with config_path.open("rb") as stream:
                config = AppConfig.model_validate(tomllib.load(stream))
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            raise InvalidArgumentError(
                f'Configuration "{config_path}" is invalid.',
                suggestion="Fix the TOML file or remove it to restore defaults.",
                details={"reason": str(exc)},
            ) from exc

    # Environment variables intentionally override persisted configuration.
    values = config.model_dump()
    if ffmpeg := os.environ.get("FACUT_FFMPEG"):
        values["tools"]["ffmpeg"] = ffmpeg
    if ffprobe := os.environ.get("FACUT_FFPROBE"):
        values["tools"]["ffprobe"] = ffprobe
    return AppConfig.model_validate(values)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def serialize_config(config: AppConfig) -> str:
    """Serialize the intentionally shallow configuration model to TOML."""

    raw = config.model_dump(mode="json")
    lines = [f'log_level = {_toml_scalar(raw["log_level"])}']
    lines.append(f'temporary_directory = {_toml_scalar(raw["temporary_directory"])}')
    for section in ("render", "preview", "cache", "tools"):
        lines.extend(("", f"[{section}]"))
        lines.extend(f"{key} = {_toml_scalar(value)}" for key, value in raw[section].items())
    return "\n".join(lines) + "\n"


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Atomically write configuration to disk."""

    destination = path or default_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(serialize_config(config), encoding="utf-8")
    os.replace(temporary, destination)
    return destination
