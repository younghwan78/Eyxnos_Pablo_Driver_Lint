#!/usr/bin/env python3
"""Derive owner references from approved original objects, never current files."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.components import CHOICES, names, policy_path
from driverlint.model import LintError, Policy, digest
from driverlint.selection import Snapshot, atomic_write, scope_hash


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--component', choices=CHOICES, default='all')
    parser.add_argument('--source', type=Path, default=Path('driver_lint/.state/reference.json'))
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        original_policy = Policy(root, root / 'driver_lint/config/policy.json')
        source = Snapshot(original_policy, args.source)
        receipts = {}
        for component in names(args.component):
            policy = Policy(root, root / policy_path(component))
            output = policy.safe_path(policy.data['snapshot'])
            if output.exists():
                Snapshot(policy, output)
                receipts[component] = 'already initialized; unchanged'
                continue
            selected = {p: sha for p, sha in source.data['files'].items() if policy.in_scope(p)}
            if not selected or not policy.protected_paths().issubset(selected):
                raise LintError(f'Approved reference lacks {component} source/contracts')
            # Verify the approved compressed objects; do not read worktree bytes.
            size = sum(len(source.before(p)) for p in selected)
            data = {'schema_version':1, 'created_utc':source.data['created_utc'],
                    'derived_utc':datetime.now(timezone.utc).isoformat(), 'component':component,
                    'derived_from_sha256':digest(source.path.read_bytes()),
                    'policy_sha256':policy.hash, 'scope_sha256':scope_hash(policy),
                    'objects':os.path.relpath(source.objects, output.parent), 'files':selected,
                    'source_bytes':size}
            atomic_write(output, (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
            receipts[component] = {'files':len(selected), 'source_bytes':size, 'source':'approved original objects'}
        print(json.dumps(receipts,ensure_ascii=False,indent=2))
        return 0
    except (OSError, ValueError, LintError) as exc:
        print(f'driver-lint reference split: ERROR: {exc}',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
