"""Standalone faster-whisper worker used by the small frozen FACUT EXE."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import site
import sys


_DLL_HANDLES: list[object] = []


def _configure_cuda() -> list[str]:
    roots: list[Path] = []
    explicit = os.environ.get("FACUT_CUDA_DLL_DIRS", "")
    roots.extend(Path(item) for item in explicit.split(os.pathsep) if item)
    try:
        packages = [Path(item) for item in site.getsitepackages()]
    except AttributeError:
        packages = []
    packages.extend(
        [
            Path(sys.prefix) / "Lib" / "site-packages",
            Path(sys.base_prefix) / "Lib" / "site-packages",
        ]
    )
    for package in packages:
        roots.extend(
            [
                package / "nvidia" / "cudnn" / "bin",
                package / "nvidia" / "cublas" / "bin",
                package / "nvidia" / "cuda_runtime" / "bin",
            ]
        )
    available: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        available.append(resolved)
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            try:
                _DLL_HANDLES.append(os.add_dll_directory(str(resolved)))
            except OSError:
                pass
    if available:
        os.environ["PATH"] = os.pathsep.join(
            [*(str(path) for path in available), os.environ.get("PATH", "")]
        )
    return [str(path) for path in available]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?")
    parser.add_argument("model", nargs="?")
    parser.add_argument("--language")
    parser.add_argument("--word-timestamps", action="store_true")
    parser.add_argument("--probe", action="store_true")
    arguments = parser.parse_args()
    dlls = _configure_cuda()
    import ctranslate2
    from faster_whisper import WhisperModel

    cuda_devices = ctranslate2.get_cuda_device_count()
    if arguments.probe:
        print(
            json.dumps(
                {
                    "status": "success",
                    "python": sys.executable,
                    "ctranslate2_version": getattr(ctranslate2, "__version__", None),
                    "faster_whisper_available": True,
                    "cuda_devices": cuda_devices,
                    "cuda_dll_directories": dlls,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    if not arguments.source or not arguments.model:
        parser.error("source and model are required unless --probe is used")
    device = "cuda" if cuda_devices > 0 else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    whisper = WhisperModel(
        str(Path(arguments.model).expanduser().resolve()),
        device=device,
        compute_type=compute_type,
        local_files_only=True,
    )
    segments, info = whisper.transcribe(
        str(Path(arguments.source).expanduser().resolve()),
        language=arguments.language,
        word_timestamps=arguments.word_timestamps,
    )
    payload = {
        "source": str(Path(arguments.source).expanduser().resolve()),
        "language": info.language,
        "language_probability": info.language_probability,
        "device": device,
        "compute_type": compute_type,
        "cuda_devices": cuda_devices,
        "cuda_dll_directories": dlls,
        "model": Path(arguments.model).name,
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "confidence": math.exp(segment.avg_logprob),
                "words": [
                    {
                        "start": word.start,
                        "end": word.end,
                        "text": word.word,
                        "confidence": word.probability,
                    }
                    for word in (segment.words or [])
                ],
            }
            for segment in segments
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "type": error.__class__.__name__, "message": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
