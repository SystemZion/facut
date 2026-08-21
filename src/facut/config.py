"""Cross-platform configuration loading and persistence."""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path, user_config_path, user_data_path, user_log_path
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
    analysis_python: str | None = None


class ModelConfig(BaseModel):
    """Storage settings for optional, separately downloaded AI models."""

    model_config = ConfigDict(extra="forbid")

    directory: Path = Field(
        default_factory=lambda: user_data_path("facut", appauthor=False) / "models"
    )
    voice_model: Path | None = None
    srt_model: Path | None = None

    def resolve(self, name: str) -> Path:
        """Resolve an explicit model link, canonical download, or known legacy folder."""

        normalized = name.strip().casefold().replace("-", "_")
        if normalized not in {"voice_model", "srt_model"}:
            raise ValueError("Model name must be voice_model or srt_model.")
        explicit = getattr(self, normalized)
        if explicit is not None:
            return Path(explicit).expanduser().resolve()
        canonical = {
            "voice_model": "Fun-CosyVoice3-0.5B-2512",
            "srt_model": "faster-whisper-large-v3-turbo",
        }[normalized]
        aliases = {
            "voice_model": (),
            "srt_model": ("whisper-turbo", ".whisper_turbo"),
        }[normalized]
        root = Path(self.directory).expanduser().resolve()
        preferred = root / canonical
        if preferred.is_dir():
            return preferred
        for alias in aliases:
            candidate = root / alias
            if candidate.is_dir():
                return candidate
        return preferred


class VoiceConfig(BaseModel):
    """Optional external runtimes for local personal-voice synthesis."""

    model_config = ConfigDict(extra="forbid")

    cpu_overlay: Path | None = None


class RuntimeConfig(BaseModel):
    """Background warm-service policy for low-latency interactive commands."""

    model_config = ConfigDict(extra="forbid")

    autoload: bool = True
    services: list[str] = Field(default_factory=lambda: ["voice"])
    voice_idle_timeout: float = Field(default=600.0, ge=0)


class AppConfig(BaseModel):
    """Validated global facut settings."""

    model_config = ConfigDict(extra="forbid")

    render: RenderConfig = Field(default_factory=RenderConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
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
    # Source checkouts carry the same reviewed FFmpeg build used by the EXE;
    # prefer it over an obsolete executable found earlier on system PATH.
    candidates.append(Path(__file__).resolve().parents[2] / "vendor" / "ffmpeg" / executable)
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
    for key in ("ffmpeg", "ffprobe"):
        configured = str(values["tools"].get(key, "")).strip().lower()
        executable = f"{key}.exe" if os.name == "nt" else key
        if configured in {"auto", key, executable}:
            values["tools"][key] = bundled_tool(key)
    if ffmpeg := os.environ.get("FACUT_FFMPEG"):
        values["tools"]["ffmpeg"] = ffmpeg
    if ffprobe := os.environ.get("FACUT_FFPROBE"):
        values["tools"]["ffprobe"] = ffprobe
    if analysis_python := os.environ.get("FACUT_ANALYSIS_PYTHON"):
        values["tools"]["analysis_python"] = analysis_python
    if model_home := os.environ.get("FACUT_MODEL_HOME"):
        values["models"]["directory"] = model_home
    if cpu_overlay := os.environ.get("FACUT_CPU_TORCH_OVERLAY"):
        values["voice"]["cpu_overlay"] = cpu_overlay
    return AppConfig.model_validate(values)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def serialize_config(config: AppConfig) -> str:
    """Serialize the intentionally shallow configuration model to TOML."""

    raw = config.model_dump(mode="json")
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        resolved_bundle = Path(bundle_root).resolve()
        for key in ("ffmpeg", "ffprobe"):
            candidate = Path(str(raw["tools"][key])).resolve()
            if candidate == resolved_bundle or resolved_bundle in candidate.parents:
                raw["tools"][key] = "auto"
    lines = [f'log_level = {_toml_scalar(raw["log_level"])}']
    lines.append(f'temporary_directory = {_toml_scalar(raw["temporary_directory"])}')
    for section in ("render", "preview", "cache", "tools", "models", "voice", "runtime"):
        lines.extend(("", f"[{section}]"))
        lines.extend(
            f"{key} = {_toml_scalar(value)}"
            for key, value in raw[section].items()
            if value is not None
        )
    return "\n".join(lines) + "\n"


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Atomically write configuration to disk."""

    destination = path or default_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(serialize_config(config), encoding="utf-8")
    os.replace(temporary, destination)
    return destination
