#!/usr/bin/env python3
"""Install independent Codex/OpenCode adapters and preserve team instructions."""
import argparse
import copy
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from driverlint.components import CHOICES, names
from driverlint.model import LintError
from driverlint.selection import atomic_write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--component', choices=CHOICES, default='all')
    parser.add_argument('--codex', action='store_true')
    parser.add_argument('--opencode', choices=('v1','v2'))
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args(argv)
    if not args.codex and not args.opencode:
        parser.error('Select --codex and/or --opencode v1|v2')
    root = args.root.resolve()
    source = root / 'driver_lint/integration'
    try:
        selected = names(args.component)
        pending = {}
        rule = (source/'agents-rule.md').read_text()
        legacy = (source/'agents-rule.md.v1').read_text()
        agents_path = root/'AGENTS.md'
        original = agents_path.read_text() if agents_path.exists() else ''
        pattern = re.compile(r'<!-- driver-lint:start -->.*?<!-- driver-lint:end -->\n?',re.DOTALL)
        blocks = list(pattern.finditer(original))
        if len(blocks)>1 or (blocks and blocks[0].group().strip() not in (rule.strip(),legacy.strip())):
            raise LintError('Existing managed AGENTS.md block differs; merge it manually')
        if args.codex:
            path = root/'.codex/hooks.json'
            current = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(current,dict) or not isinstance(current.get('hooks',{}),dict):
                raise LintError('Codex hooks must be JSON objects')
            template = json.loads((source/'codex-hooks.json').read_text())
            base = 'python3 '+shlex.quote(str(root/'driver_lint/scripts/agent_hook.py'))+' --client codex --root '+shlex.quote(str(root))
            hooks = current.setdefault('hooks',{})
            for event,groups in template['hooks'].items():
                existing = hooks.get(event,[])
                if not isinstance(existing,list):
                    raise LintError('Codex event hooks must be lists')
                kept = []
                for group in existing:
                    if not isinstance(group,dict) or not isinstance(group.get('hooks'),list) or any(not isinstance(h,dict) for h in group['hooks']):
                        raise LintError('Invalid Codex hook group')
                    handlers = []
                    for handler in group['hooks']:
                        command = handler.get('command','')
                        managed = isinstance(command,str) and 'driver_lint/scripts/agent_hook.py' in command and '--client codex' in command
                        match = re.search(r'--component\s+(all|pablo-is|gdc)(?:\s|$)',command) if managed else None
                        owned = managed and (not match or match[1]=='all' or match[1] in selected)
                        if not owned:
                            handlers.append(handler)
                    if handlers:
                        kept.append({**group,'hooks':handlers})
                if not args.remove:
                    for component in selected:
                        for group in copy.deepcopy(groups):
                            for handler in group['hooks']:
                                handler['command'] = base+' --component '+component
                            kept.append(group)
                if kept:
                    hooks[event] = kept
                else:
                    hooks.pop(event,None)
            if not hooks:
                current.pop('hooks',None)
            pending[path] = (json.dumps(current,ensure_ascii=False,indent=2)+'\n').encode()
        if args.opencode:
            # A modified pre-split plugin must be reviewed, never silently lost.
            old = root/'.opencode/plugins/driver-lint.js'
            if old.exists():
                known = [(source/f'opencode-{v}.js.v1').read_bytes() for v in ('v1','v2')]
                if old.read_bytes() not in known:
                    raise LintError('Legacy OpenCode adapter differs; merge it manually')
                pending[old] = None
            template = (source/f'opencode-{args.opencode}.js').read_text()
            for component in selected:
                path = root/f'.opencode/plugins/driver-lint-{component}.js'
                content = template.replace('@DRIVER_LINT_COMPONENT@',component).encode()
                if path.exists() and path.read_bytes()!=content:
                    raise LintError(f'Existing {component} OpenCode adapter differs; remove its prior API adapter or merge manually')
                pending[path] = None if args.remove else content
        codex_path = root/'.codex/hooks.json'
        codex = pending.get(codex_path,codex_path.read_bytes() if codex_path.exists() else b'{}')
        active = 'driver_lint/scripts/agent_hook.py' in (codex or b'').decode()
        for path in (root/'.opencode/plugins').glob('driver-lint*.js'):
            active = active or pending.get(path,path.read_bytes()) is not None
        active = active or any(path.parent==root/'.opencode/plugins' and data is not None for path,data in pending.items())
        remove = args.remove and not active
        stripped = pattern.sub('',original).rstrip()
        updated = stripped if remove else (stripped+'\n\n'+rule.strip()).lstrip()
        pending[agents_path] = (updated+'\n').encode() if updated else None
        for path in pending:
            if path.is_symlink() or path.resolve()!=path.absolute():
                raise LintError(f'Configuration symlink is unsupported: {path}')
        for path,data in pending.items():
            if data is None:
                if path.exists():
                    path.unlink()
            elif not path.exists() or path.read_bytes()!=data:
                if path.exists():
                    atomic_write(path.with_name(path.name+'.driver-lint-backup'),path.read_bytes())
                atomic_write(path,data)
        print(('Removed' if args.remove else 'Installed')+f' selected adapters: {", ".join(selected)}')
        if args.codex and not args.remove:
            print('Codex: restart and review/trust the changed driver definitions with /hooks. Product policy requires this; global trust is unchanged.')
        if args.opencode and not args.remove:
            print(f'OpenCode: restart the matching {args.opencode.upper()} runtime; Git/CI enforce commits/merges.')
        return 0
    except (OSError,ValueError,KeyError,TypeError,LintError) as exc:
        print(f'driver-lint agent install: ERROR: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
