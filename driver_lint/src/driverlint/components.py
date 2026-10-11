"""Independent driver packages with a shared engine and optional coordinator."""
from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import sys
from pathlib import Path

from .model import LintError

DIRECTORIES = {'pablo-is': 'pablo_is', 'gdc': 'gdc'}
CHOICES = ('all', *DIRECTORIES)


@contextlib.contextmanager
def stdin(data):
    previous = sys.stdin
    try:
        sys.stdin = io.StringIO(data)
        yield
    finally:
        sys.stdin = previous


def names(selector='all'):
    if selector not in CHOICES:
        raise LintError(f'Unknown driver component: {selector}')
    return tuple(DIRECTORIES) if selector == 'all' else (selector,)


def policy_path(component):
    return f'driver_lint/{DIRECTORIES[component]}/config/policy.json'


def combine(outputs, format):
    """Keep each owner's outcome visible, including operational failures."""
    if format in ('json', 'sarif'):
        records = {}
        for name, code, output in outputs:
            try:
                data = json.loads(output)
            except ValueError:
                data = {'exit_code': code, 'error': output.strip()}
            records[name] = data
        if format == 'sarif' and all('runs' in d for d in records.values()):
            return json.dumps({'$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
                               'version': '2.1.0', 'runs': [r for d in records.values() for r in d['runs']]}, indent=2) + '\n'
        return json.dumps({'components': records, 'exit_code': max(code for _, code, _ in outputs)},
                          ensure_ascii=False, indent=2) + '\n'
    if format == 'html':
        sections = ''.join(f'<section><h2>{html.escape(name)} · exit {code}</h2>'
                           f'<iframe title="{html.escape(name)} report" style="width:100%;height:75vh;border:1px solid #ccd" '
                           f'srcdoc="{html.escape(output, quote=True)}"></iframe></section>' for name, code, output in outputs)
        return '<!doctype html><html lang="ko"><meta charset="utf-8"><title>Driver lint results</title><body><h1>Pablo IS / GDC</h1>' + sections + '</body></html>\n'
    return '\n'.join(f'[{name}]\n{output.rstrip()}\n' for name, _, output in outputs)


def dispatch_cli(argv=None):
    from .cli import TOOL_ROOT, WORKSPACE, main
    from .selection import atomic_write
    argv = list(sys.argv[1:] if argv is None else argv)
    # Preserve explicit external/legacy policy use. Normal operation is split.
    if any(a == '--policy' or a.startswith('--policy=') for a in argv):
        return main(argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--component', choices=CHOICES, default='all')
    parser.add_argument('--output', type=Path)
    args, remaining = parser.parse_known_args(argv)
    read = argparse.ArgumentParser(add_help=False)
    read.add_argument('--root', type=Path, default=WORKSPACE)
    read.add_argument('--format', default='text')
    details, _ = read.parse_known_args(remaining)
    if any(a in ('-h', '--help') for a in remaining):
        print('Independent packages: driver_lint/pablo_is/lint.py and driver_lint/gdc/lint.py.\n'
              'Coordinator: --component all|pablo-is|gdc (default all). Each package has its own policy/profile/reference.\n')
        return main(remaining, default_policy=TOOL_ROOT / DIRECTORIES[names(args.component)[0]] / 'config/policy.json')
    outputs = []
    for component in names(args.component):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = main(remaining, default_policy=TOOL_ROOT / DIRECTORIES[component] / 'config/policy.json')
        outputs.append((component, code, stream.getvalue()))
    content = combine(outputs, details.format)
    try:
        if args.output:
            root = details.root.resolve()
            reports = root / 'driver_lint/reports'
            path = (root / args.output).resolve()
            if reports.resolve() != reports.absolute() or not path.is_relative_to(reports) or path.suffix not in ('.txt', '.json', '.html', '.sarif'):
                raise LintError('Coordinator reports must be below driver_lint/reports without symlinks')
            atomic_write(path, content.encode())
            print(f'Report: {path}')
            for name, code, _ in outputs:
                print(f'{name}: exit {code}')
        else:
            print(content, end='')
        return max(code for _, code, _ in outputs)
    except (OSError, LintError) as exc:
        print(f'driver-lint coordinator: ERROR: {exc}', file=sys.stderr)
        return 2


def dispatch_hook(*, client, root, component):
    from .hooks import main
    payload = sys.stdin.read(2 * 1024 * 1024 + 1)
    replies = []
    for name in names(component):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), stdin(payload):
            code = main(client=client, root_override=root, policy_override=root / policy_path(name))
        if code:
            print(f'[{name}] {error.getvalue() or output.getvalue()}', file=sys.stderr)
            return code
        if output.getvalue().strip():
            replies.append((name, json.loads(output.getvalue())))
    if len(names(component)) == 1:
        if replies:
            print(json.dumps(replies[0][1], ensure_ascii=False))
        return 0
    response = {}
    context = []
    reasons = []
    messages = []
    for name, reply in replies:
        if reply.get('decision') == 'block':
            reasons.append(f'[{name}] ' + reply['reason'])
        specific = reply.get('hookSpecificOutput')
        if specific:
            context.append(f'[{name}] ' + specific.get('additionalContext', ''))
        if reply.get('systemMessage'):
            messages.append(f'[{name}] ' + reply['systemMessage'])
    if reasons:
        response.update(decision='block', reason='\n'.join(reasons))
    if context:
        response['hookSpecificOutput'] = {'hookEventName': json.loads(payload)['hook_event_name'], 'additionalContext':'\n'.join(context)}
    if messages:
        response['systemMessage'] = '\n'.join(messages)
    if response or client == 'codex':
        print(json.dumps(response, ensure_ascii=False))
    return 0
