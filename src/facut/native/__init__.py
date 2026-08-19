"""Optional C++ sidecar discovery and protocol client."""

from .client import NativeClient, compact_batch_result, discover_native, scan_project_media

__all__ = ["NativeClient", "compact_batch_result", "discover_native", "scan_project_media"]
