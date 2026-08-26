"""Domain exceptions and stable process exit codes."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Public exit-code contract used by the CLI."""

    SUCCESS = 0
    INVALID_ARGUMENT = 2
    FILE_NOT_FOUND = 3
    MEDIA_PROBE_FAILED = 4
    TIMELINE_CONFLICT = 5
    RENDER_FAILED = 6
    DEPENDENCY_MISSING = 7
    INTERNAL_ERROR = 1


class FacutError(Exception):
    """Base class for expected, user-facing failures."""

    code = "FACUT_ERROR"
    exit_code = ExitCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}


class InvalidArgumentError(FacutError):
    code = "INVALID_ARGUMENT"
    exit_code = ExitCode.INVALID_ARGUMENT


class ResourceNotFoundError(FacutError):
    code = "FILE_NOT_FOUND"
    exit_code = ExitCode.FILE_NOT_FOUND


class MediaProbeError(FacutError):
    code = "MEDIA_PROBE_FAILED"
    exit_code = ExitCode.MEDIA_PROBE_FAILED


class TimelineConflictError(FacutError):
    code = "TIMELINE_CONFLICT"
    exit_code = ExitCode.TIMELINE_CONFLICT


class RenderError(FacutError):
    code = "RENDER_FAILED"
    exit_code = ExitCode.RENDER_FAILED


class DependencyMissingError(FacutError):
    code = "DEPENDENCY_MISSING"
    exit_code = ExitCode.DEPENDENCY_MISSING


class NotImplementedFacutError(FacutError):
    code = "NOT_IMPLEMENTED"
    exit_code = ExitCode.INVALID_ARGUMENT


class ReviewRequiredError(FacutError):
    """Automation stopped because evidence or an explicit review is still required."""

    code = "REVIEW_REQUIRED"
    exit_code = ExitCode.INVALID_ARGUMENT


class LicenseReviewRequiredError(ReviewRequiredError):
    code = "LICENSE_REVIEW_REQUIRED"


class SourceInteractionRequiredError(ReviewRequiredError):
    code = "SOURCE_INTERACTION_REQUIRED"
