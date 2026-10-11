#!/usr/bin/env python3
"""Install the commit gate, preserving the repository's existing hook."""
import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.model import LintError
from driverlint.selection import atomic_write
from driverlint.components import CHOICES


def command(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise LintError('No usable Git repository at --root; install in the actual source Git workspace. ' + p.stderr.strip())
    return p.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--component', choices=CHOICES, default='all', help='Select the independent commit gate(s)')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if Path(command(root, 'rev-parse', '--show-toplevel')).resolve() != root:
            raise LintError('--root must be the Git workspace root')
        common = (root / command(root, 'rev-parse', '--git-common-dir')).resolve()
        hooks = (root / command(root, 'rev-parse', '--git-path', 'hooks')).resolve()
        if not hooks.is_relative_to(common) and not hooks.is_relative_to(root):
            raise LintError('Effective core.hooksPath is shared outside this repository; integrate the reviewed hook there manually')
        path = hooks / 'pre-commit'
        backup = hooks / 'pre-commit.driver-lint-original'
        raw = (root / 'driver_lint/integration/pre-commit').read_text()
        template = raw.replace('@DRIVER_LINT_COMPONENT@', args.component).encode()
        owned = {raw.replace('@DRIVER_LINT_COMPONENT@', name).encode() for name in CHOICES}
        legacy = root / 'driver_lint/integration/pre-commit.v1'
        if legacy.is_file():
            owned.add(legacy.read_bytes())
        if path.is_symlink() or backup.is_symlink():
            raise LintError('Existing hook/backup is a symlink; integrate it manually')
        if args.remove:
            if path.is_file() and path.read_bytes() in owned:
                path.unlink()
                if backup.exists():
                    os.replace(backup, path)
            elif path.exists():
                raise LintError('Installed hook was modified; preserve and review it manually')
        elif path.is_file() and path.read_bytes() == template:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        elif path.is_file() and path.read_bytes() in owned:
            atomic_write(path, template)
            path.chmod(0o755)
        else:
            if backup.exists():
                raise LintError('A hook backup already exists; refusing to overwrite it')
            if path.exists():
                os.replace(path, backup)
            try:
                atomic_write(path, template)
                path.chmod(0o755)
            except OSError:
                if backup.exists():
                    os.replace(backup, path)
                raise
        print(('Removed' if args.remove else 'Installed') + f' commit gate: {path}')
        print('Lint errors and verification failures reject git commit. Local --no-verify bypasses hooks; require the CI check for merges.')
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired, LintError) as exc:
        print(f'driver-lint Git install: ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
