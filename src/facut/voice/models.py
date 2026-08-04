"""Strict models for consent-gated local digital voice profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_voice_id() -> str:
    return f"voice_{uuid4().hex[:16].upper()}"


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ConsentRecord(VoiceModel):
    relationship: Literal["self", "authorized"]
    statement: str = Field(min_length=12, max_length=2000)
    granted_at: datetime = Field(default_factory=_now)


class VoiceSample(VoiceModel):
    id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stored_path: str
    original_name: str
    size: int = Field(ge=1)
    duration: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width: int = Field(gt=0)
    transcript: str | None = None
    category: str | None = Field(default=None, max_length=80)
    delivery: str | None = Field(default=None, max_length=80)
    imported_at: datetime = Field(default_factory=_now)

    @field_validator("stored_path")
    @classmethod
    def relative_storage_only(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("stored_path must be a safe relative path")
        return normalized


class VoiceProfile(VoiceModel):
    version: str = "1.0"
    id: str = Field(default_factory=new_voice_id, pattern=r"^voice_[A-Z0-9_]{4,64}$")
    display_name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=16)
    speaker_id: str = Field(min_length=1, max_length=120)
    language: str = Field(default="zh-CN", min_length=2, max_length=32)
    style: str = Field(default="natural-vlog", min_length=1, max_length=80)
    consent: ConsentRecord
    status: Literal["draft", "warning", "ready", "invalid"] = "draft"
    samples: list[VoiceSample] = Field(default_factory=list)
    provider: dict[str, str] | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        """Keep aliases compact, deterministic and case-insensitively unique."""

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            alias = raw.strip()
            if not alias or len(alias) > 80:
                raise ValueError("voice aliases must contain 1 to 80 characters")
            if any(character.isspace() for character in alias):
                raise ValueError("voice aliases cannot contain whitespace")
            key = alias.casefold()
            if key not in seen:
                normalized.append(alias)
                seen.add(key)
        return normalized

    def public_dict(self) -> dict:
        """Return Agent-safe metadata containing no absolute storage paths."""

        payload = self.model_dump(mode="json")
        statement = payload["consent"].pop("statement")
        payload["consent"]["statement_recorded"] = True
        payload["consent"]["statement_sha256"] = hashlib.sha256(
            statement.encode("utf-8")
        ).hexdigest()
        return payload
