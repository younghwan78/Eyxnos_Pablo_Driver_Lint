"""Command interface. Exit 0=pass, 1=violations, 2=operational failure."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .model import LintError, Policy
from .report import render, text_report
from .runner import run
from .selection import Snapshot, atomic_write, create_snapshot, has_git, select


TOOL_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = TOOL_ROOT.parent


def parser(default_policy: Path | None = None) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--root', type=Path, default=WORKSPACE, help='Workspace root (default: parent of driver_lint)')
    common.add_argument('--policy', type=Path, default=default_policy or TOOL_ROOT / 'config/policy.json', help='Rule policy JSON')
    common.add_argument('--profile', help='Project profile name (config/profiles/<name>.json) or workspace-relative JSON path; default policy.profile')
    root = argparse.ArgumentParser(description='Profile-driven camera/GDC architecture and style lint')
    root.add_argument('--version', action='version', version=__version__)
    commands = root.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('scan', parents=[common], help='Read-only source lint (default command)')
    mode = scan.add_mutually_exclusive_group()
    mode.add_argument('--all', action='store_true', help='Audit all configured source files, including existing findings')
    mode.add_argument('--profile-only', action='store_true', help='Verify registered HW/build selection contracts in full; not a source regression/style gate')
    mode.add_argument('--files', nargs='+', help='Check complete named files/directories, relative to --root')
    mode.add_argument('--changed', action='store_true', help='Git HEAD versus worktree, or default release snapshot when not in Git')
    mode.add_argument('--staged', action='store_true', help='Git HEAD versus index bytes only')
    mode.add_argument('--base', help='Git commit/ref versus worktree, including source additions')
    mode.add_argument('--since', type=Path, help='Release snapshot versus current source')
    scan.add_argument('--checkpatch', choices=['auto', 'on', 'off'], default='auto', help='auto: check diffs; full-file audits require on')
    scan.add_argument('--clang-format', action='store_true', help='Read-only comparison using kernel-6.12/.clang-format (requires clang-format)')
    scan.add_argument('--fail-on', choices=['error', 'warning', 'info'], default='error', help='Gate threshold; default error')
    scan.add_argument('--format', choices=['text', 'json', 'html', 'sarif'], default='text')
    scan.add_argument('--output', type=Path, help='Write report below driver_lint/reports in the workspace')
    doctor = commands.add_parser('doctor', parents=[common], help='Check tools, scope, and snapshot readiness')
    doctor.add_argument('--format', choices=['text', 'json'], default='text')
    rules = commands.add_parser('rules', parents=[common], help='List the local rule catalog')
    rules.add_argument('--format', choices=['text', 'json'], default='text')
    snapshot = commands.add_parser('snapshot', parents=[common], help='Create or inspect a reference for a non-Git release')
    snapshot.add_argument('action', choices=['create', 'info'])
    snapshot.add_argument('--output', type=Path, help='Snapshot manifest location; default policy.snapshot')
    snapshot.add_argument('--replace', action='store_true', help='Explicitly replace reference after reviewing changes; preserves previous manifest')
    return root


def doctor(policy: Policy) -> dict:
    files = policy.files()
    git_ready = has_git(policy)
    status = {
        'profile': policy.describe(),
        'profile_path': str(policy.profile_path),
        'policy_and_profile_sha256': policy.hash,
        'python': sys.version.split()[0],
        'perl': shutil.which('perl'),
        'bundled_checkpatch': policy.safe_path(policy.data['checkpatch']['script']).is_file(),
        'clang_format_optional': shutil.which('clang-format'),
        'git_workspace': git_ready,
        'scope_files': len(files),
        'rule_count': len(policy.rules),
        'profile_contract_files_missing': sorted(p for p in policy.protected_paths() if not policy.safe_path(p).is_file()),
        'snapshot': 'not needed for Git mode' if git_ready else 'missing',
    }
    ready = bool(status['perl'] and status['bundled_checkpatch'] and files and not status['profile_contract_files_missing'])
    if policy.safe_path(policy.data['snapshot']).is_file():
        try:
            snapshot = Snapshot(policy, Path(policy.data['snapshot']))
            status['snapshot'] = {'files': len(snapshot.data['files']), 'created_utc': snapshot.data['created_utc']}
        except LintError as exc:
            status['snapshot'] = str(exc)
            ready = False
    elif not git_ready:
        ready = False
    status['ready'] = ready
    return status


def main(argv: list[str] | None = None, *, default_policy: Path | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ('scan', 'doctor', 'rules', 'snapshot', '-h', '--help', '--version'):
        argv.insert(0, 'scan')
    args = parser(default_policy).parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            raise LintError('Python 3.10 or newer is required')
        policy = Policy(args.root, args.policy if args.policy.is_absolute() else args.root / args.policy, args.profile)
        if args.command == 'doctor':
            status = doctor(policy)
            print(json.dumps(status, ensure_ascii=False, indent=2) if args.format == 'json' else '\n'.join(f'{k}: {v}' for k, v in status.items()))
            return 0 if status['ready'] else 2
        if args.command == 'rules':
            print(json.dumps(list(policy.rules.values()), ensure_ascii=False, indent=2) if args.format == 'json' else '\n'.join(
                f'{r["id"]:12} {policy.data.get("severity_overrides", {}).get(r["id"], r["severity"]):8} {r["title"]}' for r in policy.rules.values()))
            return 0
        if args.command == 'snapshot':
            output = args.output or Path(policy.data['snapshot'])
            if args.action == 'create':
                result = create_snapshot(policy, output, args.replace)
            else:
                if args.replace:
                    raise LintError('--replace is only valid with snapshot create')
                snap = Snapshot(policy, output)
                result = {k: v for k, v in snap.data.items() if k != 'files'}
                result['files'] = len(snap.data['files'])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        mode, changes, notes = select(policy, args)
        result = run(policy, mode, changes, checkpatch_mode=args.checkpatch, formatting=args.clang_format, notes=notes)
        content = render(result, policy, args.fail_on, args.format)
        if args.output:
            path = policy.safe_path(args.output)
            # A report must never replace a driver outside the configured
            # scope, the linter itself, Claude settings, or a source reference.
            report_root = policy.root / policy.data.get('reports', 'driver_lint/reports')
            if report_root.resolve() != report_root.absolute() or not path.is_relative_to(report_root) or path == report_root:
                raise LintError(f'Report output must be below {report_root}, with no source/reference symlink')
            if path.suffix not in ('.txt', '.json', '.html', '.sarif'):
                raise LintError('Report output must use .txt, .json, .html, or .sarif')
            atomic_write(path, content.encode('utf-8'))
            print(text_report(result, args.fail_on), end='')
            print(f'Report: {path}')
        else:
            print(content, end='' if content.endswith('\n') else '\n')
        return result.exit_code(args.fail_on)
    except (LintError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(f'driver-lint: ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
