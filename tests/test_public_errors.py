from __future__ import annotations

from facut.cli.common import public_error
from facut.render.ffmpeg_backend import RenderError


def test_render_detail_is_preserved_in_public_json_error() -> None:
    internal = RenderError(
        "FFmpeg rejected a media, filter, or output parameter.",
        command=["ffmpeg", "-filter_complex", "bad"],
        detail="[Parsed_crop] Invalid too big or non positive size",
    )
    public = public_error(internal)
    assert public.code == "RENDER_FAILED"
    assert "Parsed_crop" in public.details["stderr"]
