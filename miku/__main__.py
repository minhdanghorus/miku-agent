"""Console entry point: `miku` (or `uv run python -m miku`)."""

from __future__ import annotations

import sys

from miku.gateway.cli import main

if __name__ == "__main__":
    sys.exit(main())
