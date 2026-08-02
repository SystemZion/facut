"""Constant-memory subprocess execution for long-running media checks."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import time

from facut.media.tools import MediaToolError


@dataclass(frozen=True, slots=True)
class StreamResult:
    returncode: int
    stderr_tail: list[str]
    elapsed_seconds: float


def run_streaming(
    arguments: Sequence[str | Path],
    *,
    on_stderr: Callable[[str], None] | None = None,
    timeout: float | None = None,
    tail_lines: int = 200,
) -> StreamResult:
    """Run argv without a shell and drain stderr incrementally.

    Only a bounded tail is retained, so diagnostics do not grow with media
    duration. A reader thread prevents pipe backpressure while the main thread
    remains able to enforce timeouts and handle cancellation.
    """

    argv = [str(argument) for argument in arguments]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise MediaToolError(
            f'Could not start media tool "{Path(argv[0]).name}".',
            command=argv,
            stderr=str(exc),
        ) from exc

    # A bounded queue guarantees constant memory even if a media tool emits
    # diagnostics faster than the parser can consume them.
    queue: Queue[str | None] = Queue(maxsize=256)

    def drain() -> None:
        assert process.stderr is not None
        try:
            for line in process.stderr:
                queue.put(line.rstrip("\r\n"))
        finally:
            queue.put(None)

    reader = Thread(target=drain, name="facut-qc-stderr", daemon=True)
    reader.start()
    tail: deque[str] = deque(maxlen=max(1, tail_lines))
    stream_closed = False
    try:
        while process.poll() is None or not stream_closed:
            if timeout is not None and time.monotonic() - started > timeout:
                process.kill()
                process.wait()
                raise MediaToolError(
                    f"{Path(argv[0]).name} timed out during QC.",
                    command=argv,
                    stderr="\n".join(tail),
                    suggestion="Increase --timeout or inspect the input for decoder stalls.",
                )
            try:
                line = queue.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                stream_closed = True
                continue
            tail.append(line)
            if on_stderr is not None:
                on_stderr(line)
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        if process.stderr is not None:
            process.stderr.close()
        reader.join(timeout=1)
    return StreamResult(
        returncode=int(process.returncode or 0),
        stderr_tail=list(tail),
        elapsed_seconds=time.monotonic() - started,
    )
