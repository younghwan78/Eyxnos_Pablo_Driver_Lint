#!/usr/bin/env python3
"""Generate a standalone Korean HTML guide from rules and validation receipt."""

import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from driverlint.model import Policy
from driverlint.components import DIRECTORIES


def portable_links(page, target):
    """Keep package links; display source-release and report paths as text."""
    def replace(match):
        href = html.unescape(match.group('href'))
        value = urlsplit(href)
        if value.scheme or value.netloc or not value.path:
            return match.group(0)
        linked = (target.parent / unquote(value.path)).resolve()
        if (linked.is_relative_to(ROOT) and linked.is_file()
                and not any(part in {'.state', 'reports'} for part in linked.relative_to(ROOT).parts)):
            return match.group(0)
        title = html.escape('소스 릴리스 경로: ' + href, quote=True)
        return f'<span title="{title}">{match.group("label")}</span>'

    return re.sub(r'<a\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>(?P<label>.*?)</a>',
                  replace, page, flags=re.DOTALL)


def component_guide(component, directory, rules, receipt):
    policy = Policy(ROOT.parent, ROOT / directory / 'config/policy.json')
    esc = html.escape
    title = 'Pablo IS' if component == 'pablo-is' else 'GDC'
    scope = ('IS core/interface/ischain/HW API/register/sensor/CSI/library 및 IS KUnit. '
             'post/gdc와 GDC KUnit은 제외합니다.' if component == 'pablo-is' else
             'post/gdc의 core/HW API/register/HDR10+/VOTF와 GDC KUnit. IS production source 및 Pablo family 선택은 제외합니다.')
    fields = receipt.get('components', {}).get(component, {})
    audit = fields.get('audit', {})
    counts = audit.get('counts', {})
    contracts = policy.profile['build_contracts']
    rows = ''.join('<tr><td>'+esc(kind)+'</td><td>'+str(len(items))+'</td><td>'+
                   '<br>'.join(esc(item['path']) for item in items)+'</td></tr>' for kind,items in contracts.items())
    cards = []
    for rule in rules:
        if component == 'pablo-is' and rule['id'] in ('ARCH003','SAFE007'):
            continue
        severity = policy.common.get('severity_overrides',{}).get(rule['id'],rule['severity'])
        details = ''.join(f'<dt>{label}</dt><dd>{esc(rule[key])}</dd>' for label,key in
                          (('목적','why'),('탐지','detect'),('수정','fix'),('한계','limit')))
        cards.append(f'<article id="rule-{rule["id"]}"><h3>{rule["id"]} · {esc(rule["title"])} <small>{severity}</small></h3><dl>{details}</dl></article>')
    data = esc(json.dumps(policy.profile,ensure_ascii=False,indent=2))
    commands = f'''python3 driver_lint/{directory}/lint.py doctor
python3 driver_lint/{directory}/lint.py scan
python3 driver_lint/{directory}/lint.py scan --profile-only --checkpatch off
python3 driver_lint/{directory}/lint.py scan --format html \\
  --output driver_lint/{directory}/reports/change.html'''
    installs = f'''# 담당 개발자의 client 선택
python3 driver_lint/scripts/install_claude.py --component {component}
python3 driver_lint/scripts/install_agents.py --codex --component {component}
python3 driver_lint/scripts/install_agents.py --opencode v1 --component {component}

# 실제 Git checkout: clone마다 설치
python3 driver_lint/scripts/install_git.py --component {component}
python3 driver_lint/{directory}/commit_gate.py'''
    ci = f'''# 서버가 보호된 base에서 준비한 bootstrap
python3 -I "$temporary/run_ci.py" --root "$PWD" --base "$base" \\
  --component {component} --format json \\
  --output driver_lint/{directory}/reports/ci-gate.json'''
    page = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} 독립 linter 운영 가이드</title>
<style>body{{font:15px/1.8 system-ui,sans-serif;margin:0;background:#f3f6fa;color:#24364b}}main{{max-width:1100px;margin:auto;padding:35px}}h1{{font-size:34px}}h2{{font-size:24px}}h3{{font-size:18px}}nav{{display:flex;gap:18px;flex-wrap:wrap}}a{{color:#165ea3}}section{{padding:26px 0;border-bottom:1px solid #d2deea}}pre{{background:#152d45;color:#eef5ff;padding:20px;overflow:auto;border-radius:8px}}code{{font:13px/1.7 monospace;overflow-wrap:anywhere}}table{{border-collapse:collapse;background:white;width:100%}}th,td{{border-bottom:1px solid #d2deea;text-align:left;vertical-align:top;padding:12px;overflow-wrap:anywhere}}article{{padding:16px 20px;margin:12px 0;background:white;border:1px solid #d2deea;border-radius:8px}}dt{{font-weight:650}}dd{{margin:4px 0 12px}}small{{color:#57718a}}.note{{padding:18px;background:#e6eff9;border-radius:8px}}@media(max-width:650px){{main{{padding:18px}}h1{{font-size:27px}}}}</style></head><body><main>
<header><small>OWNER: {component} · SHARED ENGINE 1.3</small><h1>{title} 독립 linter</h1><p>{esc(scope)}</p>
<nav><a href="#usage">실행</a><a href="#ownership">책임 경계</a><a href="#profile">HW profile</a><a href="#hooks">Hooks / Commit</a><a href="#ci">CI / Review</a><a href="#rules">규칙</a><a href="#verified">검증과 한계</a><a href="../../docs/index.html">공통 상세 문서</a></nav></header>
<section id="usage"><h2>담당 드라이버만 검사합니다</h2><pre><code>{esc(commands)}</code></pre>
<p><a href="../lint.py">CLI</a> · <a href="../commit_gate.py">Commit gate</a> · <a href="../config/policy.json">담당 policy</a> · <a href="../CLAUDE.md">개발 계약</a></p>
<p>공유 Python 검사 엔진은 <code>driver_lint/src/driverlint</code>입니다. 이 패키지의 policy/profile/reference를 읽고 다른 담당의 policy/profile은 읽지 않습니다. 기본 gate는 error / 검증 불가를 차단하고 warning은 리뷰 정보로 표시합니다. 명확한 스타일을 강제하려면 이 패키지 policy의 severity_overrides로 해당 rule을 error로 승격합니다. exit 0 통과 / 1 위반 / 2 검증 불가.</p></section>
<section id="ownership"><h2>소스와 공유 metadata의 책임</h2><p>{esc(scope)}</p><p>공유 파일은 camera Kconfig, BUILD.bazel, post/Makefile, board fragment입니다. 이 linter는 profile의 해당 담당 계약만 검증합니다. 다른 담당의 HW 버전 불일치를 자신의 오류로 판정하지 않습니다. 공유 파일 문법/스타일과 cross-driver helper/API 변경은 양쪽 책임에 영향을 줄 수 있습니다.</p>
<table><thead><tr><th>분리 항목</th><th>이 담당의 경로 / 기준</th></tr></thead><tbody>
<tr><td>Architecture/style/예외</td><td>{directory}/config/policy.json · severity_overrides / suppressions 독립</td></tr>
<tr><td>HW profile</td><td>{directory}/config/profiles/ · policy.profile로 선택</td></tr>
<tr><td>Reference</td><td>{directory}/.state/reference.json · 승인된 원본 object에서 파생</td></tr>
<tr><td>Reports</td><td>{directory}/reports/</td></tr>
<tr><td>검증 기록의 Source 개수</td><td>{fields.get('scope_files', '기록 없음')} files (공유 metadata 포함; 현재 checkout 검사 결과가 아님)</td></tr>
</tbody></table><p>새 release에서 code 수정 전에 담당 <code>snapshot create</code>를 실행합니다. 현재 파생 manifest는 원본 shared <code>driver_lint/.state/objects</code>를 읽으므로 이 store를 유지합니다. 기준선 재생성으로 실패를 숨기면 안 됩니다.</p></section>
<section id="profile"><h2>차기 SoC/HW 변경</h2><p>{esc(policy.describe())}</p>
<p>{'Pablo family와 각 IS IP version은 독립입니다. HW 차이는 선택한 family/IP 구현에 반영합니다.' if component=='pablo-is' else 'GDC IP/API/register version을 독립 선택합니다. Pablo family domain과 camera_families 선택은 이 profile에 넣지 않습니다. IS의 family 변경만으로 GDC HW version을 변경하지 않습니다.'}</p>
<ol><li>이 패키지의 s947b.json을 새 과제 profile로 복사하고 HW spec/build 근거를 확인합니다.</li><li>HW 구현/register, scope, compatibility, build contract와 보호 source를 수정합니다. 재사용은 reason/evidence를 기록합니다.</li><li>이 패키지의 profile-only와 normal scan을 실행하고 build/KUnit/HW 근거를 확인합니다.</li><li>이 policy의 profile 경로를 새 과제로 바꿔 hooks/CLI/CI가 같은 과제를 사용하게 합니다.</li></ol>
<table><thead><tr><th>계약</th><th>개수</th><th>파일</th></tr></thead><tbody>{rows}</tbody></table>
<details><summary>현재 HW profile 전체 보기</summary><pre><code>{data}</code></pre></details></section>
<section id="hooks"><h2>Client feedback와 commit 차단</h2><pre><code>{esc(installs)}</code></pre>
<p>Claude/Codex는 <code>--component {component}</code> handler를 설치하며 OpenCode는 <code>driver-lint-{component}.js</code>로 구분합니다. 다른 담당 adapter는 유지하며 같은 selector의 --remove로 제거합니다. Codex는 바뀐 정의를 새 세션 /hooks에서 검토·신뢰해야 합니다. <a href="https://learn.chatgpt.com/docs/hooks">공식 hook 정책</a> · <a href="https://code.claude.com/docs/en/hooks">Claude 공식 hooks</a> · <a href="https://opencode.ai/docs/plugins/">OpenCode plugins</a></p>
<p>OpenCode V2 runtime에서는 v1 대신 v2 adapter를 선택합니다. API 전환은 이전 API/동일 component로 --remove 후 설치합니다. 실제 OpenCode 실행 파일은 이 환경에 없습니다.</p>
<p>Git은 pre-commit 하나를 사용하므로 선택한 담당 gate 또는 all을 연결합니다. 이전 owned driver gate로 교체할 때 중복 실행하지 않으며 기존 team hook은 먼저 실행하고 제거 시 복원합니다. commit gate는 Git index의 실제 source/settings와 등록된 build contract를 검사합니다. unstaged 수정으로 stage 오류를 숨길 수 없고 commit --only의 임시 index도 읽습니다. Hook은 --no-verify로 우회할 수 있으므로 서버 CI가 필수입니다. <a href="https://git-scm.com/docs/githooks">Git 공식 hooks</a></p>
<p class="note">현재 release root는 Git 저장소가 없어 실제 Git hook은 미설치입니다. 임시 실제 Git 저장소에서 담당별 commit 차단을 검증했습니다.</p></section>
<section id="ci"><h2>필수 status: driver-lint-{component}</h2><pre><code>{esc(ci)}</code></pre>
<p>서버가 준비한 protected/base bootstrap을 실행하고 충분히 fetch한 review base를 지정합니다. 제출 branch의 엔진을 실행하지 않고 승인된 엔진/catalog/checkpatch로 committed BASE..HEAD와 이 담당의 제출된 policy/profile/build 계약을 검사합니다. CI의 설정은 committed HEAD에서 읽습니다. 이 담당 common policy를 approved revision에 고정하며 HW profile 선택은 근거와 review로 변경할 수 있습니다.</p>
<p>cross-driver branch는 각 담당의 required job으로 모든 변경을 검증합니다. common engine/catalog는 도구 담당, {directory}/config와 HW profile은 해당 driver 담당이 리뷰합니다. Shared metadata는 양쪽 담당을 배정하고 필요한 양쪽 승인은 리뷰 서버의 절차로 요구합니다. GitHub CODEOWNERS 여러 명만으로 두 팀 모두의 승인이 강제되지는 않습니다. <a href="https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners">공식 CODEOWNERS 설명</a></p>
<p>도구 변경은 별도 reviewed maintenance job의 server-approved runner-ref를 사용합니다. PR 입력으로 runner SHA/skip/pass override를 선택하지 않습니다. multi-repository Android repo는 repository별 scope와 승인된 manifest 기반 통합 CI를 준비해야 하며, 이 gate는 독립 Git index들을 자동 합치지 않습니다.</p></section>
<section id="rules"><h2>공유 engine 규칙 · 담당 범위에 적용</h2><p>이 패키지의 layer/scope와 HW domain에 적용되는 규칙만 실행됩니다. 아래 severity는 현재 담당 policy 기준입니다. GDC 전용 core register/4K rule은 Pablo IS 문서에서 제외합니다. compiler AST/effective preprocessor나 HW specification 의미를 검증하지 않습니다.</p>{''.join(cards)}</section>
<section id="verified"><h2>검증과 남은 확인</h2><p>전체 regression + owner isolation 테스트: {esc(receipt.get('checks',[{}])[0].get('result','검증 기록 확인'))}</p>
<p>담당 전체 audit: {audit.get('files',0)} files / {counts.get('error',0)} existing errors / {counts.get('warning',0)} existing warnings. 기존 진단을 포함하는 현황이며 정상 변경 gate와 다릅니다.</p>
<p>서로 다른 driver source/profile/build/fragment 오류가 상대 gate를 실패시키지 않는지, future GDC IP 변경이 Pablo family를 변경하지 않는지, component hook/installer/CI 분리를 테스트했습니다. 원본 driver는 수정하지 않았습니다.</p>
<p>Login된 Claude/Codex UI/자동 재작업, Codex trust와 OpenCode 실제 runtime은 미검증입니다. 서버 branch protection 미설정. Driver compile/KUnit/IRQ/PM/DMA/HW 실기는 미실행입니다. lint PASS가 이 동작의 정확성을 의미하지 않습니다.</p>
<p><a href="../../docs/validation.json">검증 receipt</a> · <a href="../../reports/tests.txt">전체 테스트 로그</a> · <a href="../../docs/index.html">공통 상세 운영·source 근거</a></p></section>
<footer><small>{title} linter 1.3 · Independent owner policy + shared engine · 2026-10-09</small></footer>
</main></body></html>'''
    target = ROOT / directory / 'docs/index.html'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(portable_links(page, target),encoding='utf-8')


def generate():
    rules = json.loads((ROOT / 'config/rules.json').read_text(encoding='utf-8'))
    resolved = Policy(ROOT.parent, ROOT / 'pablo_is/config/policy.json')
    gdc = Policy(ROOT.parent, ROOT / 'gdc/config/policy.json')
    policy = resolved.common
    escape = html.escape
    cards = []
    for rule in rules:
        level = rule['severity']
        details = ''.join(f'<dt>{label}</dt><dd>{escape(rule[key])}</dd>' for label, key in (
            ('목적', 'why'), ('탐지', 'detect'), ('수정 방향', 'fix'), ('제한', 'limit')
        ))
        cards.append(f'<article id="rule-{escape(rule["id"])}" data-group="{escape(rule["group"])}" data-level="{escape(level)}">'
                     f'<div class="rule-head"><code>{escape(rule["id"])}</code><span class="tag {escape(level)}">{escape(level)}</span></div>'
                     f'<h3>{escape(rule["title"])}</h3><dl>{details}</dl></article>')
    validation_path = ROOT / 'docs/validation.json'
    validation = json.loads(validation_path.read_text(encoding='utf-8')) if validation_path.exists() else {}
    checks = validation.get('checks', [])
    rows = ''.join(f'<tr><td>{escape(c["name"])}</td><td>{escape(c["result"])}</td><td>{escape(c["details"])}</td></tr>' for c in checks)
    verified = '<div class="table"><table><thead><tr><th>검증</th><th>결과</th><th>근거와 범위</th></tr></thead><tbody>' + rows + '</tbody></table></div>' if checks else '<p>검증 기록을 아직 생성하지 않았습니다.</p>'
    audit = validation.get('audit', {})
    counts = audit.get('counts', {})
    audit_summary = ('<p>분리 이전 legacy combined <code>scan --all --checkpatch off</code>: '
                     f'{audit.get("files", 0)} files · {counts.get("error", 0)} errors · '
                     f'{counts.get("warning", 0)} warnings · {audit.get("elapsed_seconds", 0)}s. '
                     '외부 checkpatch/formatter를 실행한 결과는 포함하지 않습니다.</p>')
    catalog = {r['id']: r for r in rules}
    audit_rows = ''.join(f'<tr><td><code>{escape(rule)}</code></td><td>{number}</td><td>{escape(catalog.get(rule, {}).get("title", rule))}</td></tr>'
                         for rule, number in audit.get('rule_counts', {}).items())
    audit_table = '<div class="table"><table><thead><tr><th>Rule</th><th>전체 기존 진단 수</th><th>설명</th></tr></thead><tbody>' + audit_rows + '</tbody></table></div>' if audit_rows else ''
    component_rows = []
    for component, directory in DIRECTORIES.items():
        owner_audit = validation.get('components', {}).get(component, {}).get('audit', {})
        owner_counts = owner_audit.get('counts', {})
        component_rows.append(
            f'<tr><td>{escape(component)}</td><td>{owner_audit.get("files", 0)}</td>'
            f'<td>{owner_counts.get("error", 0)}</td><td>{owner_counts.get("warning", 0)}</td>'
            f'<td><a href="../{directory}/reports/audit.json">담당 audit JSON</a></td></tr>')
    component_audit = ('<div class="table"><table><thead><tr><th>담당</th><th>Files</th>'
                       '<th>기존 errors</th><th>기존 warnings</th><th>근거</th></tr></thead><tbody>'
                       + ''.join(component_rows) + '</tbody></table></div>'
                       '<p>각 담당의 <code>scan --all --checkpatch off</code> 결과입니다. '
                       '공유 metadata는 양쪽 검사에 포함되므로 count를 합쳐 unique 진단 수로 해석하지 않습니다. '
                       '외부 checkpatch/formatter는 이 audit에서 실행하지 않았습니다.</p>')
    template = (ROOT / 'docs/guide.html.in').read_text(encoding='utf-8')
    for marker, value in {
        '{{RULE_COUNT}}': str(len(rules)),
        '{{PROFILE_ID}}': escape(resolved.profile['id']),
        '{{PROFILE_LABEL}}': escape(resolved.describe() + ' | ' + gdc.describe()),
        '{{PROFILE_JSON}}': escape(json.dumps(resolved.profile, ensure_ascii=False, indent=2)),
        '{{GDC_PROFILE_JSON}}': escape(json.dumps(gdc.profile,ensure_ascii=False,indent=2)),
        '{{SOURCE_FILE_COUNT}}': str(audit.get('files', 1813)),
        '{{CATALOG}}': '\n'.join(cards),
        '{{VALIDATION}}': verified,
        '{{AUDIT_SUMMARY}}': audit_summary,
        '{{AUDIT_TABLE}}': audit_table,
        '{{COMPONENT_AUDIT}}': component_audit,
    }.items():
        template = template.replace(marker, value)
    if '{{' in template:
        raise ValueError('Unresolved guide placeholder')
    target = ROOT / 'docs/index.html'
    target.write_text(portable_links(template, target), encoding='utf-8')
    for component,directory in DIRECTORIES.items():
        component_guide(component,directory,rules,validation)
    print(f'Generated {target} ({len(rules)} rules, {target.stat().st_size} bytes)')


if __name__ == '__main__':
    generate()
