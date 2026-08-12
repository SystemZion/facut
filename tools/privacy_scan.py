"""Fail a release when tracked files contain private runtime artifacts."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_SUFFIXES = {".wav", ".mp3", ".flac", ".pt", ".pth", ".onnx", ".safetensors", ".part"}
PRIVATE_PARTS = {"demos", "cache", "previews", "renders", "voices", "models"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".spec", ".ps1", ".txt"}
PATTERNS = (
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+"),
    re.compile(r"(?i)/home/[^/\s]+"),
    re.compile(r"voice_[A-F0-9]{12,}"),
)


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    failures: list[str] = []
    for relative in filter(None, tracked):
        path = ROOT / relative
        parts = {part.casefold() for part in Path(relative).parts}
        if path.suffix.casefold() in PRIVATE_SUFFIXES or parts & PRIVATE_PARTS:
            failures.append(f"private artifact tracked: {relative}")
            continue
        if path == Path(__file__).resolve() or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                failures.append(f"private identifier/path in: {relative}")
                break
    if failures:
        print("FACUT privacy scan failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in failures), file=sys.stderr)
        return 1
    print(f"FACUT privacy scan passed ({len(tracked)} tracked paths checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
