#!/usr/bin/env python3
"""Independent pablo-is staged/committed gate."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.gate import main
if __name__ == '__main__':
    raise SystemExit(main(default_component='pablo-is'))
