#!/usr/bin/env python3
"""Codex adapter with an explicit project root, independent of tool cwd."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.hooks import main
from driverlint.components import CHOICES, dispatch_hook

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client', choices=('claude', 'codex'), required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--component', choices=CHOICES)
    args = p.parse_args()
    raise SystemExit(dispatch_hook(client=args.client, root=args.root.resolve(), component=args.component)
                     if args.component else main(client=args.client, root_override=args.root))
