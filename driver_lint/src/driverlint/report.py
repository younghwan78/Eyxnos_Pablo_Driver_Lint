"""Plain text, JSON, SARIF, and offline HTML reports with stable rule IDs."""

from __future__ import annotations

import html
import json
from collections import Counter
from urllib.parse import quote

from . import __version__
from .model import Policy, Result


def document(result: Result, policy: Policy, fail_on: str) -> dict:
    return {
        'schema_version': 1,
        'component': policy.component,
        'target': policy.data['target'],
        'profile': {'id': policy.profile['id'], 'path': str(policy.profile_path), 'selections': policy.profile['version_domains']},
        'policy_sha256': policy.hash,
        'mode': result.mode,
        'fail_on': fail_on,
        'exit_code': result.exit_code(fail_on),
        'files': result.files,
        'counts': dict(Counter(d.severity for d in result.diagnostics)),
        'existing_findings_excluded': result.existing_count,
        'diagnostics': [d.to_dict() for d in result.diagnostics],
        'suppressed': result.suppressions,
        'engines': result.engines,
        'notes': result.notes,
        'elapsed_seconds': result.elapsed_seconds,
    }


def text_report(result: Result, fail_on: str, limit: int | None = None) -> str:
    lines = []
    selected = result.diagnostics if limit is None else result.diagnostics[:limit]
    for d in selected:
        lines.append(f'{d.path}:{d.line}: {d.severity.upper()} {d.rule}: {d.message}')
        if d.hint:
            lines.append(f'  fix: {d.hint}')
    if limit is not None and len(result.diagnostics) > limit:
        lines.append(f'... {len(result.diagnostics) - limit} additional diagnostics; run the CLI for the full report.')
    for entry in result.suppressions if limit is None else result.suppressions[:limit]:
        d = entry['diagnostic']
        lines.append(f'{d["path"]}:{d["line"]}: SUPPRESSED {d["rule"]}: owner={entry["owner"]}; reason={entry["reason"]}')
    counts = Counter(d.severity for d in result.diagnostics)
    state = 'FAIL' if result.exit_code(fail_on) else 'PASS'
    lines.append(f'{state} [{result.mode}] {len(result.files)} files; {counts["error"]} errors, '
                 f'{counts["warning"]} warnings, {counts["info"]} info; '
                 f'{result.existing_count} existing findings excluded, {len(result.suppressions)} exceptions; '
                 f'{result.elapsed_seconds:.3f}s')
    for name, status in result.engines.items():
        lines.append(f'engine {name}: {status}')
    lines.extend('note: ' + note for note in result.notes)
    return '\n'.join(lines) + '\n'


def sarif_report(result: Result, policy: Policy, fail_on: str) -> dict:
    ids = sorted({d.rule for d in result.diagnostics})
    definitions = []
    for rule in ids:
        definition = policy.rules.get(rule, {})
        entry = {'id': rule, 'shortDescription': {'text': definition.get('title', rule)}}
        if rule.startswith('CHECKPATCH.'):
            entry['helpUri'] = 'https://docs.kernel.org/dev-tools/checkpatch.html'
        else:
            entry['help'] = {'text': definition.get('why', '') + '\n' + definition.get('fix', '')}
        definitions.append(entry)
    return {
        '$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
        'version': '2.1.0',
        'runs': [{
            'tool': {'driver': {'name': 'Exynos Driver Lint', 'version': __version__, 'rules': definitions}},
            'properties': {'component': policy.component, 'profile': policy.data['target'], 'policy_sha256': policy.hash},
            'invocations': [{'executionSuccessful': True, 'exitCode': result.exit_code(fail_on)}],
            'results': [{'ruleId': d.rule, 'level': {'error': 'error', 'warning': 'warning', 'info': 'note'}[d.severity],
                         'message': {'text': d.message + ('\n' + d.hint if d.hint else '')},
                         'locations': [{'physicalLocation': {'artifactLocation': {'uri': quote(d.path, safe='/')},
                                                            'region': {'startLine': d.line}}}]} for d in result.diagnostics],
        }],
    }


def html_report(result: Result, policy: Policy, fail_on: str) -> str:
    escape = html.escape
    rows = ''.join(
        f'<tr data-level="{escape(d.severity)}"><td><span class="tag {escape(d.severity)}">{escape(d.severity)}</span></td>'
        f'<td><code>{escape(d.rule)}</code></td><td><code>{escape(d.path)}:{d.line}</code></td>'
        f'<td><b>{escape(d.message)}</b><p>{escape(d.hint)}</p><pre>{escape(d.evidence)}</pre></td></tr>'
        for d in result.diagnostics
    )
    exceptions = ''.join(
        f'<li><code>{escape(e["diagnostic"]["rule"])}</code> · {escape(e["diagnostic"]["path"])} · '
        f'owner: {escape(e["owner"])} · reason: {escape(e["reason"])}</li>' for e in result.suppressions
    )
    summary = escape(text_report(result, fail_on, limit=0))
    status = 'FAIL' if result.exit_code(fail_on) else 'PASS'
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Exynos Driver Lint — {status}</title><style>
:root{{color-scheme:light}}body{{font:15px/1.65 system-ui,sans-serif;margin:0;background:#f3f6fa;color:#17253b}}
main{{max-width:1400px;margin:auto;padding:36px}}h1{{margin:0}}.sub{{color:#52647d}}
table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:14px;text-align:left;border-bottom:1px solid #dce4ee;vertical-align:top}}
td:nth-child(3){{max-width:400px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
.tag{{padding:3px 8px;border-radius:5px}}.error{{background:#fee2e2;color:#991b1b}}.warning{{background:#fff1cf;color:#854d0e}}.info{{background:#dbeafe;color:#1e40af}}
input,select{{padding:10px;margin:14px 10px 18px 0;border:1px solid #bac9dc;border-radius:6px}}p{{margin:5px 0}}a{{color:#155eaa}}
@media(max-width:800px){{main{{padding:16px}}.table{{overflow-x:auto}}}}@media print{{input,select{{display:none}}}}
</style></head><body><main><p class="sub">{escape(policy.describe())}</p><h1>Driver lint report · {status}</h1>
<p>이 보고서는 소스 규칙 검사 결과입니다. 빌드, KUnit 및 실기 검증 결과는 포함하지 않습니다.</p>
<pre>{summary}</pre><input id="q" aria-label="진단 검색" placeholder="rule / path / message 검색"><select id="level" aria-label="심각도"><option value="">모든 심각도</option><option>error</option><option>warning</option><option>info</option></select>
<div class="table"><table><thead><tr><th>Level</th><th>Rule</th><th>Location</th><th>Finding / fix / evidence</th></tr></thead><tbody>{rows}</tbody></table></div>
<details><summary>Applied exceptions ({len(result.suppressions)})</summary><ul>{exceptions}</ul></details>
<p><a href="../docs/index.html">전체 규칙 및 사용 설명</a></p></main><script>
function filter(){{const q=document.getElementById('q').value.toLowerCase();const l=document.getElementById('level').value;document.querySelectorAll('tbody tr').forEach(r=>{{r.hidden=(!r.textContent.toLowerCase().includes(q)||(l&&r.dataset.level!==l));}});}}
document.getElementById('q').addEventListener('input',filter);document.getElementById('level').addEventListener('change',filter);
</script></body></html>'''


def render(result: Result, policy: Policy, fail_on: str, format: str) -> str:
    if format == 'text':
        return text_report(result, fail_on)
    if format == 'html':
        return html_report(result, policy, fail_on)
    content = sarif_report(result, policy, fail_on) if format == 'sarif' else document(result, policy, fail_on)
    return json.dumps(content, ensure_ascii=False, indent=2) + '\n'
