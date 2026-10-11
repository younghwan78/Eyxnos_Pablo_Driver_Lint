#!/usr/bin/env python3
"""Independent gdc linter; shared checks, owner-specific policy/profile."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.cli import main
if __name__ == '__main__':
    raise SystemExit(main(default_policy=Path(__file__).resolve().parent / 'config/policy.json'))
