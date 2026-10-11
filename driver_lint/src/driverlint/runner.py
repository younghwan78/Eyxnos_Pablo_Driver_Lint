"""Regression filtering, auditable exceptions, and engine orchestration."""

from __future__ import annotations

import fnmatch
import time
from collections import Counter

from .checks import check_change, check_source
from .engines import checkpatch, clang_format
from .model import Change, LintError, Policy, Result


def run(policy: Policy, mode: str, changes: list[Change], *, checkpatch_mode='auto', formatting=False, notes=None) -> Result:
    start = time.monotonic()
    diagnostics = []
    existing_count = 0
    build_text = ''
    if any(c.new and c.path.endswith('.c') for c in changes):
        # Include staged content, not working-tree content, when checking the
        # staging area. Keep unchanged build metadata from the working tree only
        # for the advisory literal-source lookup.
        content = {c.path: c.after for c in changes}
        for rel in policy.files(allow_missing=True):
            if rel.endswith('.bazel') or rel.rsplit('/', 1)[-1] == 'Makefile':
                data = content.get(rel, policy.safe_path(rel).read_bytes())
                build_text += (data or b'').decode('utf-8') + '\n'
    for change in changes:
        if change.after is not None and b'\x00' in change.after:
            raise LintError(f'NUL byte in source: {change.path}')
        findings = check_source(policy, change.path, change.after) if change.after is not None else []
        if mode == 'profile':
            findings = [d for d in findings if d.rule in ('ARCH006', 'ARCH008', 'BUILD003', 'BUILD004', 'BUILD005')]
        if change.before is not None and not change.audit:
            old = Counter(d.identity() for d in check_source(policy, change.path, change.before))
            new = []
            # Match unchanged occurrences first, so an additional identical bad
            # statement is attributed to the added occurrence.
            findings.sort(key=lambda d: (d.line in change.added_lines, d.line))
            for diagnostic in findings:
                identity = diagnostic.identity()
                if old[identity]:
                    old[identity] -= 1
                    existing_count += 1
                else:
                    new.append(diagnostic)
            findings = new
        # Text style is about changed lines; file-wide and architecture rules
        # are compared across the complete old/new file so removals are caught.
        if not change.audit:
            findings = [d for d in findings if d.rule not in ('STYLE001', 'STYLE002', 'STYLE003', 'STYLE004') or d.line in change.added_lines]
        diagnostics.extend(findings)
        diagnostics.extend(check_change(policy, change, build_text))
    external, cp_status = checkpatch(policy, changes, checkpatch_mode)
    diagnostics.extend(external)
    external, cf_status = clang_format(policy, changes, formatting)
    diagnostics.extend(external)
    exceptions = []
    retained = []
    for diagnostic in diagnostics:
        matched = None
        for item in policy.data.get('suppressions', []):
            if item['rule'] == diagnostic.rule and fnmatch.fnmatchcase(diagnostic.path, item['path']) and (
                'evidence' not in item or item['evidence'] in diagnostic.evidence
            ):
                matched = item
                break
        if matched:
            exceptions.append({'diagnostic': diagnostic.to_dict(), 'reason': matched['reason'], 'owner': matched['owner']})
        else:
            retained.append(diagnostic)
    # Local rules and checkpatch may describe the same style issue; retain their
    # distinct IDs but eliminate exact duplicates from repeated export lines.
    retained = list(dict.fromkeys(retained))
    retained.sort(key=lambda d: (d.path, d.line, d.rule, d.message))
    result = Result(mode, [c.path for c in changes], retained, existing_count=existing_count,
                    suppressions=exceptions, notes=[policy.describe(), *list(notes or [])],
                    engines={'driver_rules': 'completed', 'checkpatch': cp_status, 'clang_format': cf_status})
    if not changes:
        result.notes.append('No driver changes in the configured scope; this is not a full-source or build verification.')
    result.elapsed_seconds = round(time.monotonic() - start, 3)
    return result
