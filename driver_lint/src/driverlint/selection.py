"""Git and release-snapshot change selection, including additions and deletions."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .model import Change, LintError, Policy, digest
from .source import changed_lines, decode_source


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def scope_hash(policy: Policy) -> str:
    fields = ('scope', 'extensions', 'filenames', 'exclude', 'scope_include')
    return digest(json.dumps({k: policy.data[k] for k in fields if k in policy.data},
                             sort_keys=True).encode())


def create_snapshot(policy: Policy, output: Path, replace: bool = False) -> dict:
    output = policy.safe_path(output)
    if output.suffix != '.json' or policy.in_scope(output.relative_to(policy.root).as_posix()) or output == policy.path:
        raise LintError('Snapshot output must be a JSON manifest outside driver source and policy')
    if output.exists() and not replace:
        raise LintError(f"Snapshot already exists: {output}. Use --replace only after reviewing the source changes.")
    files = policy.files()
    if not files:
        raise LintError("Snapshot scope is empty")
    objects = output.parent / 'objects'
    manifest = {}
    total = 0
    for rel in files:
        data = policy.safe_path(rel).read_bytes()
        # Decode now: a corrupted source should not become a trusted reference.
        decode_source(data)
        if b'\x00' in data:
            raise LintError(f"NUL byte in source: {rel}")
        sha = digest(data)
        obj = objects / (sha + '.gz')
        if not obj.exists():
            atomic_write(obj, gzip.compress(data, compresslevel=6, mtime=0))
        manifest[rel] = sha
        total += len(data)
    document = {
        'schema_version': 1,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'policy_sha256': policy.hash,
        'scope_sha256': scope_hash(policy),
        'objects': 'objects',
        'files': manifest,
        'source_bytes': total,
    }
    if output.exists():
        atomic_write(output.with_name(output.name + '.previous'), output.read_bytes())
    atomic_write(output, (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode())
    return {'path': str(output), 'files': len(files), 'source_bytes': total,
            'created_utc': document['created_utc']}


class Snapshot:
    def __init__(self, policy: Policy, path: Path):
        self.policy = policy
        self.path = policy.safe_path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise LintError(f"Snapshot unavailable: {self.path}. Run 'python3 driver_lint/lint.py snapshot create' before editing. {exc}") from exc
        if self.data.get('schema_version') != 1 or not isinstance(self.data.get('files'), dict):
            raise LintError("Invalid snapshot manifest")
        if self.data.get('scope_sha256') != scope_hash(policy):
            raise LintError("Snapshot scope differs from policy. Review the new scope, then explicitly create a replacement snapshot.")
        self.objects = policy.safe_path(self.path.parent / self.data.get('objects', 'objects'))
        for rel, sha in self.data['files'].items():
            if not policy.in_scope(rel) or policy.relative(rel) != rel or not isinstance(sha, str) or len(sha) != 64 or any(
                ch not in '0123456789abcdef' for ch in sha
            ):
                raise LintError(f"Invalid snapshot entry: {rel}")

    def before(self, rel: str) -> bytes | None:
        sha = self.data['files'].get(rel)
        if sha is None:
            return None
        try:
            content = gzip.decompress((self.objects / (sha + '.gz')).read_bytes())
        except (OSError, EOFError) as exc:
            raise LintError(f"Missing/corrupt snapshot object for {rel}: {exc}") from exc
        if digest(content) != sha:
            raise LintError(f"Snapshot object hash mismatch: {rel}")
        return content

    def changes(self) -> list[Change]:
        current = set(self.policy.files(allow_missing=True))
        result = []
        for rel in sorted(current | self.data['files'].keys()):
            after = self.policy.safe_path(rel).read_bytes() if rel in current else None
            if after is not None and digest(after) == self.data['files'].get(rel):
                continue
            result.append(changed_lines(Change(rel, self.before(rel), after)))
        return result


def git(policy: Policy, *args: str, allowed_codes: tuple[int, ...] = (0,)) -> bytes:
    try:
        process = subprocess.run(['git', '-C', str(policy.root), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LintError(f"Git failed: {exc}") from exc
    if process.returncode not in allowed_codes:
        raise LintError(f"Git failed ({process.returncode}): {process.stderr.decode('utf-8', 'replace').strip()}")
    return process.stdout


def has_git(policy: Policy) -> bool:
    try:
        top = git(policy, 'rev-parse', '--show-toplevel').decode().strip()
        return Path(top).resolve() == policy.root
    except LintError:
        return False


def git_reference(policy: Policy, ref: str) -> str:
    return git(policy, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def git_changes(policy: Policy, mode: str, ref: str = 'HEAD') -> list[Change]:
    if not has_git(policy):
        raise LintError("This workspace root is not a Git repository. Use --since SNAPSHOT or --all.")
    unresolved = git(policy, 'diff', '--name-only', '--diff-filter=U', '-z').split(b'\x00')
    scoped_conflicts = [p.decode() for p in unresolved if p and policy.in_scope(p.decode())]
    if scoped_conflicts:
        raise LintError(f"Unmerged source paths: {', '.join(scoped_conflicts)}")
    # In an unborn repository --changed and --staged still lint new files.
    try:
        base = git_reference(policy, ref)
    except LintError:
        if mode == 'base' or git(policy, 'rev-list', '--all').strip():
            raise
        base = None
    if base:
        args = ['diff', '--name-only', '--no-renames', '-z']
        if mode == 'staged':
            args.append('--cached')
        names = git(policy, *args, base, '--').split(b'\x00')
    else:
        names = git(policy, 'ls-files', '--cached', '-z').split(b'\x00')
    if mode != 'staged':
        names += git(policy, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\x00')
        # Include ignored source additions too: a .gitignore must not hide code
        # from the architecture gate. Only configured source scope is traversed.
        tracked = set(git(policy, 'ls-files', '--cached', '-z').split(b'\x00'))
        names += [rel.encode() for rel in policy.files(allow_missing=True) if rel.encode() not in tracked]
    paths = sorted({p.decode('utf-8') for p in names if p and policy.in_scope(p.decode('utf-8'))})
    result = []
    for rel in paths:
        original = policy.root / rel
        path = policy.safe_path(rel)
        if original.is_symlink() or original.resolve() != original.absolute():
            raise LintError(f"Source symlink is not supported: {rel}")
        old_spec = f'{base}:{rel}' if base else None
        old_exists = base and subprocess.run(['git', '-C', str(policy.root), 'cat-file', '-e', old_spec],
                                            capture_output=True, timeout=60).returncode == 0
        before = git(policy, 'show', old_spec) if old_exists else None
        if mode == 'staged':
            exists = subprocess.run(['git', '-C', str(policy.root), 'cat-file', '-e', ':' + rel],
                                    capture_output=True, timeout=60).returncode == 0
            after = git(policy, 'show', ':' + rel) if exists else None
        else:
            after = path.read_bytes() if path.is_file() else None
        if before != after:
            result.append(changed_lines(Change(rel, before, after)))
    return result


def select(policy: Policy, args) -> tuple[str, list[Change], list[str]]:
    if getattr(args, 'profile_only', False):
        changes = []
        for rel in sorted(policy.protected_paths()):
            path = policy.root / rel
            if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
                raise LintError(f'Profile requires a regular source/build file: {rel}')
            if not policy.in_scope(rel):
                raise LintError(f'Profile contract is excluded from lint scope: {rel}')
            changes.append(Change(rel, None, path.read_bytes(), audit=True))
        return 'profile', changes, ['Only ARCH006/ARCH008 and BUILD003/BUILD004/BUILD005 selection contracts are checked. Run normal scan for source changes.']
    if args.all:
        return 'audit', [Change(rel, None, policy.safe_path(rel).read_bytes(), audit=True) for rel in policy.files()], []
    if args.files:
        found = set()
        for name in args.files:
            original = policy.root / name
            if original.is_symlink() or original.resolve() != original.absolute():
                raise LintError(f'Source symlink is not supported: {name}')
            path = policy.safe_path(name)
            if not path.exists():
                raise LintError(f"Requested path does not exist: {name}. Use --changed/--since for deletions.")
            candidates = path.rglob('*') if path.is_dir() else [path]
            for candidate in candidates:
                if candidate.is_file():
                    rel = policy.relative(candidate)
                    if policy.in_scope(rel):
                        if candidate.is_symlink():
                            raise LintError(f"Source symlink is not supported: {candidate}")
                        found.add(rel)
        if not found:
            raise LintError("Requested paths contain no files in the configured driver scope")
        return 'files', [Change(rel, None, policy.safe_path(rel).read_bytes(), audit=True) for rel in sorted(found)], [
            'Explicit --files checks full files, including existing findings. Use --changed or --since for a regression gate.'
        ]
    if args.staged:
        return 'staged', git_changes(policy, 'staged'), []
    if args.base:
        return f'base:{args.base}', git_changes(policy, 'base', args.base), []
    if args.since or not has_git(policy):
        snapshot = Snapshot(policy, Path(args.since or policy.data['snapshot']))
        notes = []
        if snapshot.data.get('policy_sha256') != policy.hash:
            notes.append('Common policy or project profile changed since snapshot creation. Run --profile-only for HW/build mappings and --all for rule impact; the original source reference remains unchanged.')
        return 'snapshot', snapshot.changes(), notes
    return 'changed', git_changes(policy, 'changed'), []
