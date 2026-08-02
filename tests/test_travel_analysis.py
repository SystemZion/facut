from __future__ import annotations

from facut.analysis.travel import analyze_travel_metadata
from facut.core.models import MediaTechnicalInfo


def test_travel_analysis_reports_evidence_and_uncertainty(tmp_path) -> None:
    source = tmp_path / "日本旅行" / "酒店" / "DJI_Mavic_日落.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"placeholder")
    technical = MediaTechnicalInfo(
        creation_time="2026-07-20T18:30:00+09:00",
        timezone_offset="+09:00",
        camera_make="DJI",
        camera_model="Mavic 4 Pro",
        color_transfer="bt709",
        dynamic_range="sdr",
    )
    result = analyze_travel_metadata(source, technical)
    labels = {item["label"] for item in result["candidates"]}
    assert {"hotel", "aerial", "drone", "sunset-window", "sdr"} <= labels
    assert all(item["evidence"] for item in result["candidates"])
    assert result["visual_semantics"]["status"] == "plugin_required"


def test_daypart_confidence_drops_without_timezone(tmp_path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    result = analyze_travel_metadata(
        source, MediaTechnicalInfo(creation_time="2026-07-20T18:30:00")
    )
    daypart = next(
        item for item in result["candidates"] if item["dimension"] == "daypart"
    )
    assert daypart["confidence"] < 0.6
    assert daypart["warning"]
