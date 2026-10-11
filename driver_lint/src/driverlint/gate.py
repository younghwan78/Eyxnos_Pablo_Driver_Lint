"""Commit/CI gates over Git blobs, including configuration and build metadata."""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .cli import TOOL_ROOT, WORKSPACE
from .components import CHOICES, DIRECTORIES, combine, names, policy_path
from .model import Change, LintError, Policy, Result
from .report import render, text_report
from .runner import run
from .selection import atomic_write
from .source import changed_lines


def git(root: Path, *args: str, data: bytes | None = None, allowed=(0,)) -> bytes:
    try:
        process = subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LintError(f'Git gate could not run: {exc}') from exc
    if process.returncode not in allowed:
        raise LintError(process.stderr.decode('utf-8', 'replace').strip() or 'Git gate failed')
    return process.stdout


def resolve(root: Path, ref: str, unborn=False) -> str | None:
    try:
        return git(root, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()
    except LintError:
        if unborn and not git(root, 'rev-list', '--all').strip():
            return None
        raise


def entries(root: Path, ref: str | None, *, index=False) -> dict[str, tuple[str, str]]:
    if ref is None and not index:
        return {}
    raw = git(root, 'ls-files', '--stage', '-z') if index else git(root, 'ls-tree', '-r', '-z', '--full-tree', ref)
    result = {}
    for record in raw.split(b'\0'):
        if not record:
            continue
        meta, name = record.split(b'\t', 1)
        mode, middle, last = meta.decode().split()
        if index:
            if last != '0':
                raise LintError('Cannot commit an unmerged index')
            oid = middle
        else:
            oid = last
        result[name.decode('utf-8')] = (mode, oid)
    return result


def blobs(root: Path, oids) -> dict[str, bytes]:
    oids = sorted(set(oids))
    if not oids:
        return {}
    raw = git(root, 'cat-file', '--batch', data=('\n'.join(oids) + '\n').encode())
    stream = io.BytesIO(raw)
    result = {}
    for oid in oids:
        header = stream.readline().decode().strip().split()
        if len(header) != 3 or header[:2] != [oid, 'blob']:
            raise LintError(f'Expected Git blob {oid}, got {header}')
        size = int(header[2])
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b'\n':
            raise LintError('Truncated Git blob stream')
        result[oid] = data
    return result


def file_oid(view, rel: str) -> str:
    entry = view.get(rel)
    if entry is None or entry[0] not in ('100644', '100755'):
        raise LintError(f'Git view requires a regular tracked file: {rel}')
    return entry[1]


def write(root: Path, rel: str, data: bytes) -> None:
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise LintError(f'Git view path escapes its workspace: {rel}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def evaluate(root: Path, *, base: str | None = None, head='HEAD', lock_common_to=None,
             checkpatch_mode='auto', common_path='driver_lint/config/policy.json') -> tuple[Result, dict, str]:
    root = root.resolve()
    if Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
        raise LintError('--root must be the actual Git workspace root')
    old_ref = resolve(root, base) if base else resolve(root, 'HEAD', unborn=True)
    new_ref = resolve(root, head) if base else None
    before = entries(root, old_ref)
    after = entries(root, new_ref, index=not bool(base))
    mode = f'commit-range:{old_ref}..{new_ref}' if base else 'commit-staged'
    common_rel = Path(common_path)
    if common_rel.is_absolute() or '..' in common_rel.parts:
        raise LintError('Gate policy must be a workspace-relative tracked path')
    common_bytes = blobs(root, [file_oid(after, common_path)])[file_oid(after, common_path)]
    common = json.loads(common_bytes)
    if lock_common_to:
        trusted = entries(root, resolve(root, lock_common_to))
        oid = file_oid(trusted, common_path)
        original = json.loads(blobs(root, [oid])[oid])
        # New projects can select a new profile. Changes to common enforcement
        # go through the team's separately reviewed tool-maintenance process.
        if {k: v for k, v in common.items() if k != 'profile'} != {k: v for k, v in original.items() if k != 'profile'}:
            raise LintError('Common policy changed under --lock-common-to; use the reviewed tool-maintenance process')
    with tempfile.TemporaryDirectory(prefix='driver-lint-git-view-') as directory:
        view_root = Path(directory).resolve()
        write(view_root, common_path, common_bytes)
        selected = common.get('profile')
        if not isinstance(selected, str) or not selected:
            raise LintError('Committed common policy needs a profile path')
        profile_path = (view_root / common_path).parent.joinpath(selected).resolve()
        if not profile_path.is_relative_to(view_root):
            raise LintError('Committed profile must be inside the Git workspace')
        rel = profile_path.relative_to(view_root).as_posix()
        oid = file_oid(after, rel)
        write(view_root, rel, blobs(root, [oid])[oid])
        policy = Policy(view_root, view_root / common_path)
        changed = sorted(p for p in before.keys() | after.keys()
                         if policy.in_scope(p) and before.get(p) != after.get(p))
        required = policy.protected_paths()
        # New-source lookup must see indexed build files, even when they have
        # unrelated unstaged edits. No worktree source/build fallback is used.
        build_files = {p for p in after if policy.in_scope(p) and
                       (Path(p).name == 'Makefile' or Path(p).suffix in ('.bazel', '.bzl'))}
        materialize = required | build_files | {p for p in changed if p in after}
        new_oids = {p: file_oid(after, p) for p in materialize}
        old_oids = {p: file_oid(before, p) for p in changed if p in before}
        data = blobs(root, [*new_oids.values(), *old_oids.values()])
        for p, oid in new_oids.items():
            write(view_root, p, data[oid])
        changes = [changed_lines(Change(p, data[old_oids[p]] if p in old_oids else None,
                                       data[new_oids[p]] if p in new_oids else None)) for p in changed]
        if changes and checkpatch_mode != 'off':
            # The runner's checkout supplies the tool. CI can execute a runner
            # from its protected base; submitted code cannot replace that Perl.
            script = policy.data['checkpatch']['script']
            source = TOOL_ROOT.parent / script
            if not source.is_file():
                raise LintError(f'Trusted runner is missing bundled checkpatch: {source}')
            for name in (source.name, 'spelling.txt', 'const_structs.checkpatch'):
                file = source.parent / name
                if file.is_file():
                    write(view_root, str(Path(script).parent / name), file.read_bytes())
        mapped = run(policy, 'profile', [Change(p, None, data[new_oids[p]], audit=True) for p in sorted(required)],
                     checkpatch_mode='off')
        source_result = run(policy, mode, changes, checkpatch_mode=checkpatch_mode)
        diagnostics = list(dict.fromkeys([*mapped.diagnostics, *source_result.diagnostics]))
        diagnostics.sort(key=lambda d: (d.path, d.line, d.rule))
        result = Result(mode, sorted(required | set(changed)), diagnostics,
                        existing_count=source_result.existing_count,
                        suppressions=[*mapped.suppressions, *source_result.suppressions],
                        notes=[policy.describe(), 'Configuration, source and build contracts were read from Git blobs; unstaged worktree bytes were not used.',
                               f'Profile contracts: {len(required)} files; source diffs: {len(changes)} files.'],
                        engines={**source_result.engines, 'profile_contracts': 'completed'},
                        elapsed_seconds=round(mapped.elapsed_seconds + source_result.elapsed_seconds, 3))
        rendered = json.loads(render(result, policy, 'error', 'json'))
        rendered['profile']['path'] = rel
        rendered['component'] = policy.component
        rendered['git_view'] = {'base': old_ref, 'head': new_ref, 'index': not bool(base), 'config_from': 'Git blobs'}
        return result, rendered, render(result, policy, 'error', 'html')


def main(argv=None, *, default_component='all') -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=WORKSPACE)
    parser.add_argument('--base', help='CI: inspect committed BASE..HEAD instead of the index')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--lock-common-to', help='CI: permit only profile selection to differ from common policy at REF')
    parser.add_argument('--component', choices=CHOICES, default=default_component)
    parser.add_argument('--policy', help='Explicit workspace-relative policy; advanced/legacy single-policy operation')
    parser.add_argument('--checkpatch', choices=('auto', 'off'), default='auto', help='off is a reduced diagnostic run; installed hook uses auto')
    parser.add_argument('--format', choices=('text', 'json', 'html'), default='text')
    parser.add_argument('--output', type=Path, help='Optional report under driver_lint/reports')
    args = parser.parse_args(argv)
    try:
        outputs = []
        documents = {}
        selectors = (('custom', args.policy),) if args.policy else tuple((name, policy_path(name)) for name in names(args.component))
        for name, path in selectors:
            try:
                result, document, html = evaluate(args.root, base=args.base, head=args.head,
                    lock_common_to=args.lock_common_to, checkpatch_mode=args.checkpatch, common_path=path)
                documents[name] = document
                content = {'text': text_report(result, 'error'), 'json': json.dumps(document, ensure_ascii=False, indent=2) + '\n', 'html': html}[args.format]
                outputs.append((name, result.exit_code('error'), content))
            except (LintError, OSError, ValueError, KeyError, TypeError) as exc:
                message = f'[{name}] driver-lint gate: ERROR: {exc}'
                documents[name] = {'exit_code': 2, 'error': message}
                outputs.append((name, 2, json.dumps(documents[name]) if args.format == 'json' else message + '\n'))
        content = outputs[0][2] if len(outputs) == 1 else combine(outputs, args.format)
        if args.output:
            root = args.root.resolve()
            output = (root / args.output).resolve()
            reports = root / ('driver_lint/reports' if args.component == 'all' or args.policy else
                              f'driver_lint/{DIRECTORIES[args.component]}/reports')
            if reports.resolve() != reports.absolute() or not output.is_relative_to(reports) or output.suffix not in ('.json', '.txt', '.html'):
                raise LintError('Gate report must be below driver_lint/reports without a symlink')
            atomic_write(output, content.encode())
            for name, code, _ in outputs:
                print(f'{name}: exit {code}')
                for diagnostic in documents[name].get('diagnostics', [])[:20]:
                    print(f"{diagnostic['path']}:{diagnostic['line']}: {diagnostic['rule']}: {diagnostic['message']}")
                if documents[name].get('error'):
                    print(documents[name]['error'], file=sys.stderr)
        else:
            print(content, end='')
            for document in documents.values():
                if document.get('error'):
                    print(document['error'], file=sys.stderr)
        return max(code for _, code, _ in outputs)
    except (LintError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'driver-lint gate: ERROR: {exc}', file=sys.stderr)
        return 2
