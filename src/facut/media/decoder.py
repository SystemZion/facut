"""Frame extraction facade.

Decoding remains streaming and is delegated to FFmpeg.  Keeping this facade
separate lets future native decoders implement the same API.
"""

from .thumbnail import generate_thumbnail

extract_frame = generate_thumbnail

__all__ = ["extract_frame", "generate_thumbnail"]
