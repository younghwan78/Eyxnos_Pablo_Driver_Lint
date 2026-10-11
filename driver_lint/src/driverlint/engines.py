"""Optional external tools, always pinned to repository configuration."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .model import Change, Diagnostic, LintError, Policy
from .source import Source, patch_for


def checkpatch(policy: Policy, changes: list[Change], mode: str) -> tuple[list[Diagnostic], str]:
    if mode == 'off' or (mode == 'auto' and any(c.audit for c in changes)):
        return [], 'disabled explicitly' if mode == 'off' else 'not requested for full-file audit; use --checkpatch on'
    if not changes:
        return [], 'no changed files'
    script = policy.safe_path(policy.data['checkpatch']['script'])
    if not script.is_file() or not shutil.which('perl'):
        raise LintError('checkpatch requires Perl and the repository kernel-6.12/scripts/checkpatch.pl. Use --checkpatch off only for an explicit reduced check.')
    source_changes = [c for c in changes if c.after is not None or c.before is not None]
    output = []
    options = ['--no-tree', '--no-signoff', '--terse', '--showfile', '--show-types', '--no-summary',
               '--strict', '--color=never', f'--max-line-length={policy.data["max_line_length"]}',
               '--ignore', ','.join(policy.data['checkpatch']['ignore'])]
    with tempfile.TemporaryDirectory(prefix='driver-checkpatch-') as directory:
        temp = Path(directory)
        # The bundled script searches cwd before HOME. An empty local config
        # makes results independent of the developer's ~/.checkpatch.conf.
        (temp / '.checkpatch.conf').write_text('', encoding='utf-8')
        audit = any(c.audit for c in source_changes)
        tasks = []
        if audit:
            for index, change in enumerate(source_changes):
                if change.after is None:
                    continue
                file = temp / f'{index}-{Path(change.path).name}'
                file.write_bytes(change.after)
                tasks.append(([*options, '--file', str(file)], {str(file): change.path}, [change]))
        else:
            patch = ''.join(patch_for(c) for c in source_changes)
            if not patch:
                return [], 'no textual diff'
            file = temp / 'changes.patch'
            file.write_text(patch, encoding='utf-8')
            tasks.append(([*options, str(file)], {}, source_changes))
        for args, mapping, selected in tasks:
            try:
                process = subprocess.run(['perl', str(script), *args], cwd=temp, capture_output=True, text=True,
                                         timeout=policy.data['checkpatch']['timeout_seconds'])
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LintError(f'checkpatch failed: {exc}') from exc
            if process.returncode not in (0, 1):
                raise LintError(f'checkpatch failed ({process.returncode}): {process.stderr.strip() or process.stdout[:1000]}')
            count = 0
            after = {c.path: c.after for c in selected}
            sources = {path: Source(data or b'') for path, data in after.items()}
            for raw in process.stdout.splitlines():
                match = re.match(r'(.+?):(\d+):\s*(ERROR|WARNING|CHECK):([A-Z0-9_]+):\s*(.*)', raw)
                if not match:
                    continue
                path, line, level, kind, message = match.groups()
                path = mapping.get(path, path)
                if path.startswith(('a/', 'b/')):
                    path = path[2:]
                if path not in after:
                    # A patch-wide diagnostic still needs a visible location.
                    path = selected[0].path
                number = max(1, int(line))
                evidence = sources[path].evidence(number)
                severity = {'ERROR': 'error', 'WARNING': 'warning', 'CHECK': 'info'}[level]
                output.append(Diagnostic(path, number, 'CHECKPATCH.' + kind, severity, message.strip(),
                                         'Consult the bundled Linux checkpatch rule and surrounding driver style.', evidence.strip()[:500]))
                count += 1
            if process.returncode == 1 and not count:
                raise LintError(f'checkpatch returned findings that could not be parsed: {process.stdout[:1500]} {process.stderr[:500]}')
            if process.stderr.strip():
                # Do not hide Perl/parser errors behind a successful gate.
                raise LintError(f'checkpatch produced stderr: {process.stderr.strip()[:1500]}')
    return output, 'bundled checkpatch completed'


def clang_format(policy: Policy, changes: list[Change], enabled: bool) -> tuple[list[Diagnostic], str]:
    if not enabled:
        return [], 'not requested (read-only optional formatting comparison)'
    executable = shutil.which('clang-format')
    if not executable:
        raise LintError('clang-format was requested but is not installed')
    config = policy.safe_path('kernel-6.12/.clang-format')
    if not config.is_file():
        raise LintError('Repository kernel-6.12/.clang-format is missing')
    output = []
    for change in changes:
        if not change.path.endswith(('.c', '.h')) or change.after is None:
            continue
        args = [executable, f'--style=file:{config}', '--output-replacements-xml',
                f'--assume-filename={policy.safe_path(change.path)}']
        if not change.audit and change.added_lines:
            # Limit formatting comparisons to new/modified lines.
            lines = sorted(change.added_lines)
            start = end = lines[0]
            for line in lines[1:]:
                if line == end + 1:
                    end = line
                else:
                    args.append(f'--lines={start}:{end}')
                    start = end = line
            args.append(f'--lines={start}:{end}')
        elif not change.audit:
            continue
        try:
            process = subprocess.run(args, input=change.after, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LintError(f'clang-format failed: {exc}') from exc
        if process.returncode:
            raise LintError(f'clang-format failed: {process.stderr.decode("utf-8", "replace")[:1000]}')
        from xml.etree import ElementTree
        try:
            root = ElementTree.fromstring(process.stdout)
            if root.attrib.get('incomplete_format') == 'true':
                raise LintError(f'clang-format could not completely parse {change.path}')
            replacements = root.findall('replacement')
        except ElementTree.ParseError as exc:
            raise LintError(f'Invalid clang-format XML: {exc}') from exc
        if replacements:
            line = change.after[:int(replacements[0].attrib['offset'])].count(b'\n') + 1
            output.append(Diagnostic(change.path, line, 'CLANG_FORMAT', 'warning',
                                     f'{len(replacements)} formatting differences using the kernel .clang-format',
                                     'Review a small formatting diff manually; this linter never writes driver files.', '<formatting>'))
    return output, 'kernel .clang-format comparison completed'
