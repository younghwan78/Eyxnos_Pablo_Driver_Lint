"""Claude Code/Codex JSON-stdin adapters; never execute payload strings."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from .cli import TOOL_ROOT
from .model import LintError, Policy
from .report import text_report
from .runner import run
from .selection import Snapshot, has_git, select


def response(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False))


def main(*, client='claude', root_override: Path | None = None, policy_override: Path | None = None) -> int:
    payload = {}
    event = ''
    package = policy_override.parent.parent.name if policy_override else ''
    directory = package if package in ('pablo_is', 'gdc') else ''
    lint_command = 'python3 driver_lint/' + (directory + '/' if directory else '') + 'lint.py'
    contract = 'driver_lint/' + (directory + '/' if directory else '') + 'CLAUDE.md'
    try:
        raw = sys.stdin.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise LintError('Hook input exceeded 2 MiB')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise LintError('Hook input must be a JSON object')
        event = payload.get('hook_event_name', '')
        if event not in ('SessionStart', 'PostToolUse', 'PostToolUseFailure', 'Stop'):
            raise LintError(f'Unsupported hook event: {event}')
        root = (root_override or Path(os.environ.get('CLAUDE_PROJECT_DIR', str(TOOL_ROOT.parent)))).resolve()
        policy = Policy(root, policy_override or root / 'driver_lint/config/policy.json')
        if policy.component in ('pablo-is', 'gdc'):
            directory = 'pablo_is' if policy.component == 'pablo-is' else 'gdc'
            lint_command = f'python3 driver_lint/{directory}/lint.py'
            contract = f'driver_lint/{directory}/CLAUDE.md'
        if event == 'SessionStart':
            ready = has_git(policy)
            if not ready:
                Snapshot(policy, Path(policy.data['snapshot']))
            response({'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
                'Driver lint is active. Follow ' + contract + '. Selected ' + policy.describe() + '. '
                'Preserve common layer/API contracts; implement hardware differences in the versions selected by this project profile. '
                'Pablo family and individual IP versions are independent. For a new project, review and update the profile from hardware/build evidence. '
                'Run ' + lint_command + ' scan before claiming completion. Warnings require review evidence; '
                'a passing lint does not replace builds for ' + ', '.join(policy.profile['review_variants']) + ' or hardware validation.'}})
            return 0
        if event in ('PostToolUse', 'PostToolUseFailure') and payload.get('tool_name') not in ('Bash', 'exec_command', 'apply_patch'):
            inputs = payload.get('tool_input')
            if not isinstance(inputs, dict):
                raise LintError('File-tool hook input is missing tool_input')
            name = inputs.get('file_path') or inputs.get('path')
            if not isinstance(name, str):
                raise LintError('File-tool hook input is missing file_path')
            candidate = Path(name)
            if not candidate.is_absolute():
                # Current tools use absolute paths. Accept older clients using
                # cwd, anchored and validated before passing to selection.
                candidate = Path(payload.get('cwd') or root) / candidate
            candidate = candidate.resolve()
            if not candidate.is_relative_to(root) or not policy.in_scope(candidate.relative_to(root).as_posix()):
                return 0
        args = SimpleNamespace(all=False, files=None, staged=False, base=None, since=None, changed=True)
        mode, changes, notes = select(policy, args)
        result = run(policy, mode, changes, checkpatch_mode='auto', notes=notes)
        if not result.diagnostics:
            if event == 'Stop' and client == 'codex':
                response({})
            return 0
        report = text_report(result, 'error', limit=18)
        report = report[:12000]
        failed = bool(result.exit_code('error'))
        if event == 'Stop':
            if failed and not payload.get('stop_hook_active', False):
                response({'decision': 'block', 'reason':
                    'Driver architecture/style lint failed. Fix the errors and rerun ' + lint_command + ' scan.\n' + report})
            elif failed:
                # One repair continuation is permitted. If it still fails, make
                # that visible without creating an endless Stop hook loop.
                response({'systemMessage':
                    'Driver lint still FAILS. The Stop retry guard allows this response to finish; CI must still reject it.\n' + report})
            else:
                response({'systemMessage': 'Driver lint warnings require human review.\n' + report})
        elif failed and event == 'PostToolUse':
            response({'decision': 'block', 'reason': report,
                      'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
                          'The tool already edited the source. Repair these lint errors. Update HW selections only as part of an intended, evidenced project/HW migration.\n' + report}})
        else:
            response({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': report}})
        return 0
    except (LintError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        message = f'driver-lint hook could not verify the source: {exc}'
        if event == 'SessionStart':
            response({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': message +
                '\nBefore editing a release checkout, run ' + lint_command + ' snapshot create; never create/reset a snapshot to hide an already-made change.'}})
            return 0
        if event == 'Stop':
            if payload.get('stop_hook_active', False):
                response({'systemMessage': message + '\nStop retry guard permits finishing; verification remains incomplete.'})
            else:
                response({'decision': 'block', 'reason': message})
            return 0
        print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
