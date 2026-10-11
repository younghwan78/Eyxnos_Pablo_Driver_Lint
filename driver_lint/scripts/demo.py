#!/usr/bin/env python3
"""Exercise CLI/checkpatch/Stop using an isolated fixture; emit sample reports."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from driverlint.model import Diagnostic, Policy, Result
from driverlint.report import render


TOOL = Path(__file__).resolve().parents[1]
WORKSPACE = TOOL.parent


def main():
    with tempfile.TemporaryDirectory(prefix='exynos-lint-demo-') as directory:
        root = Path(directory)
        (root / 'driver_lint/config').mkdir(parents=True)
        shutil.copy2(TOOL / 'config/policy.json', root / 'driver_lint/config/policy.json')
        shutil.copytree(TOOL / 'config/profiles', root / 'driver_lint/config/profiles')
        settings = Policy(root, root / 'driver_lint/config/policy.json').data
        project = Policy(root, root / 'driver_lint/config/policy.json').profile
        script = root / settings['checkpatch']['script']
        script.parent.mkdir(parents=True)
        for filename in ('checkpatch.pl', 'spelling.txt', 'const_structs.checkpatch'):
            shutil.copy2(WORKSPACE / 'kernel-6.12/scripts' / filename, script.parent / filename)
        core = root / settings['camera_root'] / 'post/gdc/pablo-gdc-core.c'
        core.parent.mkdir(parents=True)
        clean = '// SPDX-License-Identifier: GPL-2.0\nint gdc_run(void)\n{\n\treturn 0;\n}\n'
        core.write_text(clean)
        for contract in project['fragments']:
            fragment = root / contract['path']
            fragment.parent.mkdir(parents=True, exist_ok=True)
            fragment.write_text('\n'.join(k + '=' + v for k, v in contract['requirements'].items()) + '\n')
        shared = ['--root', str(root), '--policy', str(root / 'driver_lint/config/policy.json')]

        def cli(*args):
            return subprocess.run([sys.executable, str(TOOL / 'lint.py'), *args, *shared], capture_output=True, text=True, timeout=90)

        created = cli('snapshot', 'create')
        if created.returncode:
            raise RuntimeError(created.stderr)
        clean_result = cli('scan', '--format', 'json')
        if clean_result.returncode != 0:
            raise RuntimeError('Clean fixture failed: ' + clean_result.stderr)
        core.write_text(clean.replace('\treturn 0;', '\twritel(1, base);\n\treturn 0;'))
        injected = cli('scan', '--format', 'json')
        if injected.returncode != 1:
            raise RuntimeError('Injected violation was not rejected: ' + injected.stdout + injected.stderr)
        data = json.loads(injected.stdout)
        if 'ARCH003' not in {d['rule'] for d in data['diagnostics']}:
            raise RuntimeError('Expected GDC register-boundary diagnostic is missing')
        if data['engines']['checkpatch'] != 'bundled checkpatch completed':
            raise RuntimeError('Bundled checkpatch did not run')
        environment = {**os.environ, 'CLAUDE_PROJECT_DIR': str(root)}

        def stop(active):
            process = subprocess.run([sys.executable, str(TOOL / 'scripts/claude_hook.py')],
                                     env=environment, input=json.dumps({'hook_event_name': 'Stop', 'stop_hook_active': active}),
                                     capture_output=True, text=True, timeout=90)
            if process.returncode:
                raise RuntimeError(process.stderr)
            return json.loads(process.stdout)

        blocked = stop(False)
        retry = stop(True)
        if blocked.get('decision') != 'block' or 'still FAILS' not in retry.get('systemMessage', ''):
            raise RuntimeError('Stop decision or retry guard did not work')
        result = Result(data['mode'], data['files'], [Diagnostic(**d) for d in data['diagnostics']],
                        existing_count=data['existing_findings_excluded'], suppressions=data['suppressed'],
                        notes=['Artificial demonstration fixture; these are not findings in the real driver source.', *data['notes']],
                        engines=data['engines'], elapsed_seconds=data['elapsed_seconds'])
        policy = Policy(root, root / 'driver_lint/config/policy.json')
        reports = TOOL / 'reports'
        reports.mkdir(exist_ok=True)
        for format in ('json', 'html'):
            (reports / ('example-violation.' + format)).write_text(render(result, policy, 'error', format), encoding='utf-8')
        receipt = {'clean_cli_exit': 0, 'injected_cli_exit': 1, 'rule': 'ARCH003',
                   'checkpatch': data['engines']['checkpatch'], 'first_stop': 'block',
                   'repeated_stop': 'explicit FAIL message; no repeat block',
                   'real_driver_source_modified': False}
        (reports / 'demo-verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(f'Example report: {reports / "example-violation.html"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
