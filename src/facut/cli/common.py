"""Shared helpers for project-aware CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from facut.core.project_manager import ProjectManager
from facut.exceptions import (
    FacutError,
    InvalidArgumentError,
    MediaProbeError,
    RenderError,
    ResourceNotFoundError,
    TimelineConflictError,
)


def manager_for(state: Any, *, load: bool = True) -> ProjectManager:
    """Resolve the explicit project option or discover a project from cwd."""

    if state.project is not None:
        manager = ProjectManager(state.project)
        if load:
            manager.load()
        return manager
    if load:
        return ProjectManager.discover(Path.cwd())
    return ProjectManager(Path.cwd())


def public_error(error: Exception) -> FacutError:
    """Translate internal domain exceptions to the stable public error contract."""

    if isinstance(error, FacutError):
        return error
    code = getattr(error, "code", "")
    suggestion = getattr(error, "suggestion", None)
    details: dict[str, Any] = {}
    stderr = getattr(error, "stderr", None) or getattr(error, "detail", None)
    if stderr:
        details["stderr"] = str(stderr)[-4000:]
    if log_path := getattr(error, "log_path", None):
        details["log_path"] = str(log_path)
    message = str(error) or error.__class__.__name__
    if isinstance(error, (FileNotFoundError,)):
        return ResourceNotFoundError(message, suggestion=suggestion)
    if code in {"MEDIA_PROBE_FAILED", "MEDIA_TOOL_ERROR"}:
        return MediaProbeError(message, suggestion=suggestion, details=details)
    if code in {"RENDER_FAILED", "FFMPEG_FAILED"}:
        return RenderError(message, suggestion=suggestion, details=details)
    if code in {"TIMELINE_CONFLICT"} or error.__class__.__name__ == "TimelineConflictError":
        return TimelineConflictError(message, suggestion=suggestion, details=details)
    if code in {"PROJECT_NOT_FOUND", "FILE_NOT_FOUND"}:
        return ResourceNotFoundError(message, suggestion=suggestion)
    return InvalidArgumentError(message, suggestion=suggestion, details=details)


def compact_project(document: Any) -> dict[str, Any]:
    """Return an AI-friendly project snapshot without derived Python objects."""

    payload = document.model_dump(mode="json")
    payload["project"]["duration"] = document.recompute_duration()
    payload["warnings"] = []
    payload["missing_media"] = [
        asset.id for asset in document.media if getattr(asset, "offline", False)
    ]
    payload["recent_operations"] = payload["history"][-20:]
    return payload
