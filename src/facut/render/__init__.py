"""facut render backend."""

from .cache import RenderCache, cache_key
from .ffmpeg_backend import FFmpegBackend, RenderError, RenderResult
from .graph_builder import FilterGraph, GraphBuilder
from .hardware import EncoderChoice, choose_h264_encoder
from .incremental import (
    IncrementalRenderer,
    IncrementalRenderResult,
    incremental_eligibility,
)

__all__ = [
    "EncoderChoice",
    "FFmpegBackend",
    "FilterGraph",
    "GraphBuilder",
    "IncrementalRenderer",
    "IncrementalRenderResult",
    "RenderCache",
    "RenderError",
    "RenderResult",
    "cache_key",
    "choose_h264_encoder",
    "incremental_eligibility",
]
