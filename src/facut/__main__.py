"""Module entry point for ``python -m facut``."""

from __future__ import annotations

import sys

from facut import __version__

if len(sys.argv) == 2 and sys.argv[1] == "--version":
    print(f"facut {__version__}")
    raise SystemExit(0)

from facut.cli.main import main


if __name__ == "__main__":
    main()
