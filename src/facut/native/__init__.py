"""Optional C++ sidecar discovery and protocol client."""

from .client import (
    NativeClient,
    batch_failure_warnings,
    collect_batch_inputs,
    compact_batch_result,
    discover_native,
    scan_project_media,
)

__all__ = [
    "NativeClient",
    "batch_failure_warnings",
    "collect_batch_inputs",
    "compact_batch_result",
    "discover_native",
    "scan_project_media",
]
