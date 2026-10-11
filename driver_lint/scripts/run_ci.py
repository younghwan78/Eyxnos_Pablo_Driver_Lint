#!/usr/bin/env python3
"""Run the committed change gate with the linter from a trusted review base.

CI must load this bootstrap from its protected base/job configuration, rather
than executing a copy submitted by the branch under review. No candidate Python,
Perl or build code is executed. Candidate profiles remain subject to HW review.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def git(root, *args):
    process = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=60)
    if process.returncode:
        raise ValueError(process.stderr.decode('utf-8', 'replace').strip() or 'Git failed')
    return process.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--base', required=True, help='Trusted, fetched review/merge base commit')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--runner-ref', help='Server-approved tool revision; default is --base. Never take this from PR input.')
    parser.add_argument('--component', choices=('all', 'pablo-is', 'gdc'), default='all')
    parser.add_argument('--policy', help='Explicit workspace-relative single policy (advanced/legacy)')
    parser.add_argument('--format', choices=('text', 'json', 'html'), default='text')
    parser.add_argument('--output', help='Optional report below driver_lint/reports')
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        base = git(root, 'rev-parse', '--verify', '--end-of-options', args.base + '^{commit}').decode().strip()
        head = git(root, 'rev-parse', '--verify', '--end-of-options', args.head + '^{commit}').decode().strip()
        runner = git(root, 'rev-parse', '--verify', '--end-of-options', args.runner_ref + '^{commit}').decode().strip() if args.runner_ref else base
        policies = [args.policy] if args.policy else [f'driver_lint/{name}/config/policy.json' for name in
                    (('pablo_is','gdc') if args.component == 'all' else ('pablo_is' if args.component == 'pablo-is' else 'gdc',))]
        paths = ['driver_lint/src', 'driver_lint/config/rules.json', 'driver_lint/scripts/commit_gate.py']
        for path in policies:
            common = json.loads(git(root, 'show', runner + ':' + path))
            script = PurePosixPath(common['checkpatch']['script'])
            if script.is_absolute() or '..' in script.parts:
                raise ValueError('Trusted checkpatch path must be relative to its workspace')
            paths.extend((str(script), str(script.parent / 'spelling.txt'), str(script.parent / 'const_structs.checkpatch')))
        paths = list(dict.fromkeys(paths))
        archive = git(root, 'archive', '--format=tar', runner, '--', *paths)
        with tempfile.TemporaryDirectory(prefix='driver-lint-trusted-runner-') as folder:
            trusted = Path(folder).resolve()
            # Extract only ordinary files/directories. Do not follow archive links.
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as stream:
                for member in stream:
                    path = trusted / member.name
                    if not path.resolve().is_relative_to(trusted):
                        raise ValueError('Trusted runner archive contains an escaping path')
                    if member.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        data = stream.extractfile(member)
                        if data is None:
                            raise ValueError('Trusted runner archive file is missing')
                        with data:
                            path.write_bytes(data.read())
                    else:
                        raise ValueError('Trusted runner archive contains a link or special file')
            command = [sys.executable, '-I', str(trusted / 'driver_lint/scripts/commit_gate.py'),
                       '--root', str(root), '--base', base, '--head', head, '--lock-common-to', runner,
                       '--format', args.format]
            command += ['--policy', args.policy] if args.policy else ['--component', args.component]
            if args.output:
                command += ['--output', args.output]
            # Avoid checkpatch personal settings and Python import injection.
            env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
            print(f'driver-lint CI: trusted runner {runner}; review base {base}; candidate {head}', file=sys.stderr)
            return subprocess.run(command, cwd=trusted, env=env, timeout=180).returncode
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print(f'driver-lint CI: ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
