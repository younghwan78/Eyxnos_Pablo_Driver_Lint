#!/usr/bin/env python3
"""Repository-local Exynos camera/GDC lint entry point (Python 3.10+)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from driverlint.components import dispatch_cli as main

if __name__ == "__main__":
    raise SystemExit(main())
