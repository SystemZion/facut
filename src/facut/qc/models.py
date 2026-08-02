"""Stable structured output models for automated QC."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QCModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QCStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class CheckResult(QCModel):
    """Result of one independently inspectable media check."""

    status: QCStatus
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class FileQCResult(QCModel):
    """All QC evidence for one source file."""

    path: str
    media_id: str | None = None
    kind: str | None = None
    status: QCStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    checks: dict[str, CheckResult] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    contact_sheet: str | None = None


class QCReport(QCModel):
    """Machine-readable report for a file or complete facut project."""

    version: str = "1.0"
    scope: dict[str, Any]
    status: QCStatus
    summary: dict[str, int]
    files: list[FileQCResult]
    warnings: list[str] = Field(default_factory=list)
