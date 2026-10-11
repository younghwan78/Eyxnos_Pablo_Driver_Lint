#!/usr/bin/env python3
"""Install independent Claude hooks and instructions, preserving team settings."""
import argparse
import copy
import json
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
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    source = root / 'driver_lint/integration'
    settings = root / '.claude/settings.json'
    try:
        template = json.loads((source / 'claude-settings.json').read_text())
        current = json.loads(settings.read_text()) if settings.exists() else {}
        if not isinstance(current, dict) or not isinstance(current.get('hooks', {}), dict):
            raise LintError('Existing Claude settings/hooks must be JSON objects')
        selected = names(args.component)
        pending = {}
        legacy_rule = root / '.claude/rules/driver-lint.md'
        if legacy_rule.exists():
            if legacy_rule.read_bytes() == (source / 'driver-lint-rule.md.v1').read_bytes():
                pending[legacy_rule] = None
            elif not args.remove:
                raise LintError('Legacy Claude rule differs; merge it before migration')
        for component in selected:
            rule = root / f'.claude/rules/{component}-lint.md'
            content = (source / f'{component}-rule.md').read_bytes()
            if rule.exists() and rule.read_bytes() != content:
                if args.remove:
                    continue
                raise LintError(f'Existing {component} rule differs; merge the reviewed rule manually')
            pending[rule] = None if args.remove else content
        hooks = current.setdefault('hooks', {})
        for event, incoming in template['hooks'].items():
            existing = hooks.get(event, [])
            if not isinstance(existing, list):
                raise LintError('Claude event hooks must be lists')
            base = incoming[0]['hooks'][0]['command']
            owned = {base, *(base + ' --component ' + name for name in selected)}
            kept = []
            for group in existing:
                if not isinstance(group, dict) or not isinstance(group.get('hooks'), list) or any(not isinstance(h, dict) for h in group['hooks']):
                    raise LintError('Invalid Claude hook group')
                retained = [h for h in group['hooks'] if h.get('command') not in owned]
                if retained:
                    kept.append({**group, 'hooks':retained})
            if not args.remove:
                for component in selected:
                    for group in copy.deepcopy(incoming):
                        for handler in group['hooks']:
                            handler['command'] += ' --component ' + component
                        kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if not hooks:
            current.pop('hooks', None)
        pending[settings] = (json.dumps(current,ensure_ascii=False,indent=2)+'\n').encode()
        for path in pending:
            if path.is_symlink() or path.resolve() != path.absolute():
                raise LintError(f'Configuration symlink is unsupported: {path}')
        for path, content in pending.items():
            if content is None:
                if path.exists():
                    path.unlink()
            elif not path.exists() or path.read_bytes() != content:
                if path.exists():
                    atomic_write(path.with_name(path.name+'.driver-lint-backup'),path.read_bytes())
                atomic_write(path,content)
        print(('Removed' if args.remove else 'Installed')+f' Claude driver hooks: {", ".join(selected)}')
        print('Use /hooks in a new Claude session to confirm the selected driver hooks.')
        return 0
    except (OSError, ValueError, TypeError, KeyError, LintError) as exc:
        print(f'driver-lint install: ERROR: {exc}',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
