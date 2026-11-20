import json
import pytest
from facut.core.performance import StageTimer


def ticking(*values):
    return iter(values).__next__


def test_stage_total():
    timer = StageTimer(clock=ticking(0, 1, 4, 8))
    with timer.stage("extract"):
        pass
    r = timer.report()
    assert r["total_wall_seconds"] == 8
    assert r["stages"][0]["wall_seconds"] == 3
    assert r["stages"][0]["ffmpeg_metrics"] is None
    json.dumps(r, allow_nan=False)


def test_failure():
    timer = StageTimer(clock=ticking(0, 1, 3, 4))
    with pytest.raises(RuntimeError, match="original"):
        with timer.stage("decode"):
            raise RuntimeError("original")
    assert timer.report()["stages"][0] == {
        "name": "decode", "status": "failed", "wall_seconds": 2,
        "ffmpeg_metrics": None}


def test_null_and_detached():
    timer = StageTimer(clock=ticking(0, 1, 2))
    for status in ("not_measured", "not_applicable", "cached"):
        timer.mark(status, status, reason="explicit reason")
    r = timer.report()
    assert all(s["wall_seconds"] is None for s in r["stages"])
    r["stages"][0]["name"] = "changed"
    assert timer.report()["stages"][0]["name"] == "not_measured"


def test_nested():
    timer = StageTimer(clock=ticking(0, 1, 2, 4, 6, 7))
    with timer.stage("outer"):
        with timer.stage("inner"):
            pass
    assert [s["wall_seconds"] for s in timer.report()["stages"]] == [5, 2]


def test_invalid():
    timer = StageTimer()
    with pytest.raises(ValueError):
        with timer.stage(" "):
            pass
    with pytest.raises(ValueError):
        timer.mark("x", "completed", reason="not measured")
    with pytest.raises(ValueError):
        timer.mark("x", "cached", reason="")


def test_running_snapshot():
    timer = StageTimer()
    with timer.stage("in_flight"):
        r = timer.report()["stages"][0]
        assert r["status"] == "running" and r["wall_seconds"] is None
    assert timer.report()["stages"][0]["status"] == "completed"
