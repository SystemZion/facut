"""Opt-in wall-time measurement; never infers AI or FFmpeg CPU time."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from time import perf_counter
from typing import Callable, Iterator


class StageTimer:
    """One operation's process-local timer; durations are inclusive, not additive.

    Caller owns opt-in output/persistence. Do not share across threads.
    """
    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._start = clock()
        self._stages: list[dict] = []

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not name or not name.strip():
            raise ValueError("Stage name must not be empty")
        start = self._clock()
        record = {"name": name, "status": "running", "wall_seconds": None,
                  "ffmpeg_metrics": None}
        self._stages.append(record)
        try:
            yield
        except BaseException:
            record["status"] = "failed"
            raise
        else:
            record["status"] = "completed"
        finally:
            record["wall_seconds"] = max(0.0, self._clock() - start)

    def mark(self, name: str, status: str, *, reason: str) -> None:
        """Record unmeasured work without presenting zero as a measurement."""
        if not name or not name.strip() or not reason or not reason.strip():
            raise ValueError("Stage name and reason must not be empty")
        if status not in {"not_measured", "not_applicable", "cached"}:
            raise ValueError("Status must be not_measured, not_applicable or cached")
        self._stages.append({"name": name, "status": status, "reason": reason,
                             "wall_seconds": None, "ffmpeg_metrics": None})

    def report(self) -> dict:
        """Detached snapshot at call time; total includes gaps between stages."""
        return {"schema": "facut.performance.v1", "clock": "perf_counter",
                "duration_semantics": "inclusive_wall_time",
                "total_wall_seconds": max(0.0, self._clock() - self._start),
                "stages": deepcopy(self._stages)}
