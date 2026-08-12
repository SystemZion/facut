"""Hermetic media-tool selection for integration tests."""

from __future__ import annotations

import os

from facut.media.tools import find_executable


def pytest_configure() -> None:
    """Use FACUT's resolver so a legacy system PATH cannot poison the suite."""

    ffmpeg = find_executable("ffmpeg", os.environ.get("FACUT_TEST_FFMPEG"))
    ffprobe = find_executable("ffprobe", os.environ.get("FACUT_TEST_FFPROBE"))
    os.environ["FACUT_TEST_FFMPEG"] = ffmpeg
    os.environ["FACUT_TEST_FFPROBE"] = ffprobe
    os.environ["FACUT_FFMPEG"] = ffmpeg
    os.environ["FACUT_FFPROBE"] = ffprobe
