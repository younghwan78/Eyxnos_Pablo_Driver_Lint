#!/usr/bin/env python3
"""Thin launcher; all rules live in the versionable driver_lint package."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from driverlint.hooks import main
from driverlint.components import CHOICES, dispatch_hook

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--component', choices=CHOICES)
    args = p.parse_args()
    root = Path(os.environ.get('CLAUDE_PROJECT_DIR', str(Path(__file__).resolve().parents[2]))).resolve()
    raise SystemExit(dispatch_hook(client='claude', root=root, component=args.component) if args.component else main())
