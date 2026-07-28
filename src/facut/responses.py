"""Unified machine-readable response envelopes."""

from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from facut.exceptions import FacutError

T = TypeVar("T")


class ErrorItem(BaseModel):
    """A stable error object intended for humans and agents."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    suggestion: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel, Generic[T]):
    """The response shape shared by every facut command."""

    model_config = ConfigDict(extra="forbid")

    status: str
    command: str
    data: T | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorItem] = Field(default_factory=list)
    project_revision: int | None = None

    def as_json(self, *, pretty: bool = False) -> str:
        """Serialize without leaking implementation-specific Python objects."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )


def success_response(
    command: str,
    data: T | None = None,
    *,
    warnings: list[str] | None = None,
    project_revision: int | None = None,
) -> Response[T]:
    """Create a success envelope."""

    return Response[T](
        status="success",
        command=command,
        data=data,
        warnings=warnings or [],
        project_revision=project_revision,
    )


def error_response(
    command: str,
    error: FacutError,
    *,
    warnings: list[str] | None = None,
    project_revision: int | None = None,
) -> Response[None]:
    """Convert a public domain exception to an error envelope."""

    item = ErrorItem(
        code=error.code,
        message=error.message,
        suggestion=error.suggestion,
        details=error.details,
    )
    return Response[None](
        status="error",
        command=command,
        warnings=warnings or [],
        errors=[item],
        project_revision=project_revision,
    )
