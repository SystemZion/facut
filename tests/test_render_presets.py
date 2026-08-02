from __future__ import annotations

from facut.render.presets import list_render_presets, resolve_render_preset


def test_youtube_4k_retains_source_fps_and_selects_bitrate_tier() -> None:
    standard = resolve_render_preset("youtube-4k-sdr", source_fps=25)
    high = resolve_render_preset("youtube-4k-sdr", source_fps=60)
    assert standard["fps"] == 25
    assert standard["bitrate"] == "45M"
    assert high["fps"] == 60
    assert high["bitrate"] == "68M"
    assert high["audio_bitrate"] == "384k"
    assert high["audio_sample_rate"] == 48000
    assert high["color_space"] == "bt709"
    assert high["fast_start"] is True


def test_multi_platform_presets_are_machine_discoverable() -> None:
    presets = list_render_presets()
    assert {"youtube-4k-sdr", "bilibili-4k", "shorts-9x16", "community-1x1"} <= set(presets)
    assert presets["shorts-9x16"]["width"] < presets["shorts-9x16"]["height"]
