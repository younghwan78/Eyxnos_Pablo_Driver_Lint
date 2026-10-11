"""Small, inspectable rules grounded in this checkout's layer boundaries."""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import PurePosixPath

from .model import Change, Diagnostic, Policy
from .source import Source, decode_source


RAW_IO = re.compile(
    r'\b(?:read[blqw](?:_relaxed)?|write[blqw](?:_relaxed)?|'
    r'ioread(?:8|16|32|64)(?:be)?|iowrite(?:8|16|32|64)(?:be)?|'
    r'(?:PMIO|GDC)_(?:SET|GET)_[FR]|pmio_(?:read|write|field_read|field_write))\s*\('
)
SLEEP = re.compile(
    r'\b(?:mutex_lock(?:_interruptible|_killable)?|msleep(?:_interruptible)?|usleep_range|'
    r'fsleep|schedule(?:_timeout(?:_interruptible|_uninterruptible)?)?|'
    r'wait_event\w*|wait_for_completion\w*|copy_(?:from|to)_user)\s*\(|'
    r'\b(?:k[czm]alloc|kmalloc_array|pablo_[zv]?alloc)\s*\([^;{}]*\bGFP_KERNEL\b'
)
LOCK = re.compile(r'\bspin_(?P<unlock>un)?lock(?:_irqsave|_irqrestore|_irq|_bh)?\s*\(')
EXPORT = re.compile(r'\b(?:EXPORT_SYMBOL(?:_GPL)?|KUNIT_EXPORT_SYMBOL)\s*\(')
REGISTER_HEADER = re.compile(r'(?:^|/)(?:is-sfr-|pablo-sfr-|pablo-hw-reg-)[^/]+\.h$')


def check_source(policy: Policy, path: str, data: bytes) -> list[Diagnostic]:
    source = Source(data)
    layer = policy.layer(path)
    c_source = path.endswith(('.c', '.h'))
    exempt_long_line = any(fnmatch.fnmatchcase(path, p) for p in policy.data.get('long_line_exempt_paths', []))
    output = []

    def emit(rule: str, line: int, message: str, evidence: str | None = None):
        output.append(policy.diagnostic(rule, path, line, message,
                                        source.evidence(line) if evidence is None else evidence))

    # These text checks intentionally inspect comments and strings, too.
    previous = ''
    for line, raw in enumerate(source.text.split('\n'), 1):
        clean = raw.rstrip('\r')
        if clean.endswith((' ', '\t')):
            emit('STYLE001', line, 'Trailing whitespace')
        if '\r' in raw:
            emit('STYLE004', line, 'Use LF line endings')
        if re.match(r'^(?:<{7}|>{7}|\|{7})(?:\s|$)|^={7}$', clean):
            emit('STYLE005', line, 'Unresolved conflict marker')
        if not exempt_long_line and len(clean.expandtabs(8)) > policy.data['max_line_length']:
            emit('STYLE003', line, f"Line exceeds {policy.data['max_line_length']} columns")
        if c_source and re.match(r'^ {8,}\S', clean):
            stripped = clean.strip()
            continuation = previous.rstrip().endswith(('(', ',', '\\', '=', '&&', '||', '+', '?', ':'))
            if not continuation and not stripped.startswith(('*', '//', '/*', '.', '}', ')', '#')):
                emit('STYLE002', line, 'Space-only base indentation; review tabs versus continuation alignment')
        if clean.strip():
            previous = clean
    if source.text and not source.text.endswith('\n'):
        emit('STYLE007', max(1, len(source.lines)), 'File lacks final newline', '<EOF>')
    if c_source and not any('SPDX-License-Identifier:' in raw for raw in source.lines[:5]):
        emit('STYLE006', 1, 'Missing SPDX identifier in the first five lines', '<SPDX>')
    if not c_source:
        output.extend(check_build(policy, path, source))
        return output

    for line, header, _ in source.includes():
        basename = PurePosixPath(header).name
        if layer != 'test' and header.endswith('.c'):
            emit('ARCH001', line, f'Implementation include crosses a translation unit boundary: {header}')
        if layer in policy.data['forbid_register_include_layers'] and REGISTER_HEADER.search(header):
            emit('ARCH002', line, f'{layer} includes register layout: {header}')
        if layer in ('hw-api', 'gdc-api') and basename in policy.data['upper_layer_headers']:
            emit('ARCH004', line, f'Hardware API depends on an upper manager header: {header}')
        if layer != 'test' and (re.search(r'(?:^|/)testing/', header) or
                                header in ('kunit/test.h', 'kunit/mock.h') or '-kunit-test.' in header) and not source.positive_test_guard(line):
            emit('ARCH005', line, f'Production source includes a test dependency: {header}')
        local = policy.camera_relative(path)
        for domain in policy.profile['version_domains']:
            api = re.search(domain['implementation_pattern'], local) if domain.get('implementation_pattern') else None
            register = re.search(domain['register_pattern'], header) if domain.get('register_pattern') else None
            if api and register:
                compatible = domain.get('register_compatibility', {}).get(api.group(1), {}).get('versions', [api.group(1)])
                if register.group(1) not in compatible:
                    emit('ARCH006', line, f"{domain['name']} API v{api.group(1)} includes incompatible register v{register.group(1)}")
        pattern = policy.family_domain['include_pattern'] if policy.family_domain else policy.data.get('family_include_pattern')
        family = re.search(pattern, header) if pattern else None
        if family:
            owner = policy.version_domain(local)
            if owner and owner[0]['kind'] == 'family':
                domain, version = owner
                allowed = domain.get('include_compatibility', {}).get(version, {}).get('versions', [version])
                if family.group(1) not in allowed:
                    emit('ARCH008', line, f"{domain['name']} {version} includes incompatible family {family.group(1)}")
            elif owner and policy.family_domain and policy.version_selected(owner[0], owner[1], local):
                allowed = owner[0].get('camera_families', policy.family_domain['selected_versions'])
                if family.group(1) not in allowed:
                    emit('ARCH008', line, f"Selected {owner[0]['name']} implementation includes incompatible family {family.group(1)}")
            elif not owner and layer in policy.data.get('common_family_include_layers', []) and not any(
                fnmatch.fnmatchcase(local, selector['path']) for selector in policy.profile.get('common_family_selectors', [])
            ):
                emit('ARCH008', line, f'Common {layer} source explicitly depends on hardware family {family.group(1)}')

    if layer in policy.data['raw_register_layers']:
        for match in RAW_IO.finditer(source.code):
            emit('ARCH003', source.line_at(match.start()), 'GDC core bypasses the hardware API for register access')
    if layer == 'test':
        return output
    for match in re.finditer(r'\b(?:BUG|BUG_ON|panic)\s*\(', source.code):
        emit('SAFE001', source.line_at(match.start()), 'Production code can halt the whole system')
    for match in re.finditer(r'\(\s*(?:u32|unsigned\s+int)\s*\)\s*[A-Za-z_]\w*(?:->\w+|\.\w+)*', source.code):
        if re.search(r'(?:addr|dva|iova|dma|ptr|pointer)', match.group(), re.IGNORECASE):
            emit('SAFE006', source.line_at(match.start()), 'Possible address narrowing to 32 bits')
    if layer == 'gdc-core':
        for match in re.finditer(r'\b(?:ALIGN|PTR_ALIGN|round_up|round_down)\s*\([^,;\n]+,\s*(?:4096|0[xX]1000)\s*\)', source.code):
            emit('SAFE007', source.line_at(match.start()), 'Literal 4K alignment in GDC core; review page size and HW granule')
    ignored_calls = [
        ('SAFE008', r'pm_runtime_(?:get_sync|resume_and_get)', 'Runtime PM return value is discarded'),
        ('SAFE009', r'copy_(?:from|to)_user', 'User copy return value is discarded'),
    ]
    for rule, names, message in ignored_calls:
        for match in re.finditer(r'^[ \t]*(?:' + names + r')\s*\([^;{}]*\)\s*;', source.code, re.MULTILINE):
            emit(rule, source.line_at(match.start()), message)
    for match in re.finditer(r'\b(?:kmalloc|kzalloc)\s*\(([^,;{}]+),', source.code):
        if '*' in match.group(1) and 'sizeof' in match.group(1):
            emit('SAFE011', source.line_at(match.start()), 'Allocation multiplies a sizeof value; review overflow handling')

    if layer == 'register':
        return output
    for func in source.functions():
        body = source.code[func.body:func.end]
        signature = source.code[func.start:func.body]
        irq = bool(re.search(r'\birqreturn_t\b', signature))
        if func.end_line - func.line + 1 > policy.data['max_function_lines']:
            emit('STYLE008', func.line, f'Function {func.name} exceeds {policy.data["max_function_lines"]} lines',
                 f'{func.name}: {func.end_line - func.line + 1} lines')
        if layer in ('gdc-core', 'gdc-api') and not func.name.startswith(('gdc_', 'camerapp_', 'pablo_', '__gdc_')):
            emit('STYLE008', func.line, f'Function {func.name} differs from the GDC naming convention', func.name)
        locks = sorted([(m.start(), 'unlock' if m.group('unlock') else 'lock') for m in LOCK.finditer(body)] +
                       [(m.start(), 'sleep') for m in SLEEP.finditer(body)])
        lock_depth = 0
        for offset, kind in locks:
            if kind == 'lock':
                lock_depth += 1
            elif kind == 'unlock':
                lock_depth = max(0, lock_depth - 1)
            else:
                line = source.line_at(func.body + offset)
                if irq:
                    emit('SAFE002', line, f'Possible sleep in IRQ function {func.name}')
                if lock_depth:
                    emit('SAFE003', line, f'Possible sleep after spin_lock in {func.name}; check branches and context')
        if irq:
            for match in re.finditer(r'\b(?:pr_info|pr_err|printk|dev_info|dev_err)\s*\(', body):
                emit('SAFE005', source.line_at(func.body + match.start()), f'Direct log in IRQ function {func.name}')
        dma_maps = list(re.finditer(r'\bdma_map_(?:single|page)\s*\(', body))
        if dma_maps and not re.search(r'\bdma_mapping_error\s*\(', body):
            for match in dma_maps:
                emit('SAFE010', source.line_at(func.body + match.start()), f'No visible dma_mapping_error check in {func.name}')
        for match in re.finditer(r'\b(?:while\s*\(\s*(?:1|true)\s*\)|for\s*\(\s*;\s*;\s*\))\s*\{', body):
            start = match.end() - 1
            depth = 0
            stop = len(body)
            for brace in re.finditer(r'[{}]', body[start:]):
                depth += 1 if brace.group() == '{' else -1
                if depth == 0:
                    stop = start + brace.end()
                    break
            loop = body[start:stop]
            if RAW_IO.search(loop) and not re.search(r'\b(?:break|return|timeout|time_after|time_before|jiffies)\b', loop):
                emit('SAFE004', source.line_at(func.body + match.start()), f'No visible termination in register polling loop of {func.name}')
    return output


def literal_list(node) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in node.elts):
        return [n.value for n in node.elts]
    return None


def check_build(policy: Policy, path: str, source: Source) -> list[Diagnostic]:
    out = []

    def emit(rule, line, message, evidence=None):
        out.append(policy.diagnostic(rule, path, line, message, evidence if evidence is not None else source.evidence(line)))

    for fragment in policy.profile['fragments']:
        if path != fragment['path']:
            continue
        values = {}
        for line, raw in enumerate(source.lines, 1):
            match = re.match(r'\s*(CONFIG_\w+)=(\S+)\s*$', raw)
            disabled = re.match(r'\s*#\s+(CONFIG_\w+) is not set\s*$', raw)
            if match:
                values.setdefault(match.group(1), []).append((line, match.group(2)))
            elif disabled:
                values.setdefault(disabled.group(1), []).append((line, 'n'))
        for key, expected in fragment['requirements'].items():
            entries = values.get(key, [])
            if len(entries) != 1 or entries[0][1] != expected:
                emit('BUILD003', entries[0][0] if entries else 1,
                     f"Profile {policy.profile['id']} requires exactly one {key}={expected}", key)
        for group in fragment.get('exclusive_groups', []):
            for key, entries in values.items():
                if re.fullmatch(group['pattern'], key) and key not in group['selected']:
                    for line, value in entries:
                        if value in ('y', 'm'):
                            emit('BUILD004', line, f'Implementation outside profile selection enabled: {key}')
    contracts = policy.profile['build_contracts']
    for contract in contracts['kconfig']:
        if path != contract['path']:
            continue
        symbol = contract['symbol']
        blocks = list(re.finditer(r'^(?:menu)?config ' + re.escape(symbol) + r'\s*\n(.*?)(?=^(?:menu)?config |\Z)',
                                  source.text, re.MULTILINE | re.DOTALL))
        if len(blocks) != 1:
            emit('BUILD005', 1, f'Expected one Kconfig symbol {symbol}', symbol)
        elif 'depends_on' in contract:
            dependencies = re.findall(r'^\s*depends on ([^\n]+)', blocks[0].group(1), re.MULTILINE)
            expected = ' '.join(contract['depends_on'].split())
            if expected not in [' '.join(d.split()) for d in dependencies]:
                emit('BUILD005', source.line_at(blocks[0].start()), f'{symbol} must depend on {expected}', f'{symbol} -> {expected}')
    for contract in contracts['kbuild']:
        if path != contract['path']:
            continue
        # Join logical lines before examining a literal object selection.
        logical = re.sub(r'\\\r?\n', ' ', source.text)
        pattern = r'^' + re.escape(contract['target']) + r'-\$\(' + re.escape(contract['config']) + r'\)\s*\+?=\s*([^\n]*)'
        assignments = re.findall(pattern, logical, re.MULTILINE)
        selected = [v for assignment in assignments for v in assignment.split('#', 1)[0].split()]
        expected = contract['sources']
        foreign = [v for v in selected if contract.get('versioned_source_pattern') and
                   re.search(contract['versioned_source_pattern'], v) and v not in expected]
        valid = sorted(selected) == sorted(expected) if contract.get('exact', False) else set(expected).issubset(selected)
        if not valid or foreign:
            emit('BUILD005', 1, f"{contract['config']} Kbuild mapping must select {', '.join(expected)}", contract['config'])
    bazel_contracts = [c for kind in ('ddk', 'selectors', 'literals') for c in contracts.get(kind, []) if c['path'] == path]
    if bazel_contracts:
        try:
            tree = ast.parse(source.text)
        except SyntaxError as exc:
            emit('BUILD005', exc.lineno or 1, 'Cannot inspect Bazel literal structure; validate syntax and target mapping')
            return out
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {k.arg: k.value for k in node.keywords if k.arg is not None}
            calls.append((node, keywords))
        for contract in contracts['ddk']:
            if path != contract['path']:
                continue
            modules = [(n, k) for n, k in calls if isinstance(k.get('name'), ast.Constant) and
                       k['name'].value == contract['module']]
            if len(modules) != 1:
                emit('BUILD005', 1, f"Expected one {contract['module']} DDK target", contract['module'])
                continue
            node, keywords = modules[0]
            sources = keywords.get('conditional_srcs')
            branches = []
            if isinstance(sources, ast.Dict):
                for key, value in zip(sources.keys, sources.values):
                    if isinstance(key, ast.Constant) and key.value == contract['config']:
                        # Only True branch is the selected implementation.
                        if isinstance(value, ast.Dict):
                            for enabled, files in zip(value.keys, value.values):
                                if isinstance(enabled, ast.Constant) and enabled.value is True:
                                    entries = literal_list(files)
                                    if entries is not None:
                                        branches.append(entries)
            branch = branches[0] if len(branches) == 1 else []
            expected = contract['sources']
            foreign = [v for v in branch if contract.get('versioned_source_pattern') and
                       re.search(contract['versioned_source_pattern'], v) and v not in expected]
            if not set(expected).issubset(branch) or foreign:
                emit('BUILD005', node.lineno, f"{contract['module']} / {contract['config']} DDK branch must select {', '.join(expected)}",
                     contract['module'] + ' / ' + contract['config'])
        for contract in contracts['selectors']:
            if path != contract['path']:
                continue
            groups = [(n, k) for n, k in calls if isinstance(k.get('name'), ast.Constant) and k['name'].value == contract['name']]
            boards = literal_list(groups[0][1].get(contract.get('keyword', 'match_any'))) if len(groups) == 1 else None
            if boards is None or not set(contract['boards']).issubset(boards):
                emit('BUILD005', 1, f"{contract['name']} selection must cover {', '.join(contract['boards'])}", contract['name'])
        for contract in contracts.get('literals', []):
            if path != contract['path']:
                continue
            targets = [(n, k) for n, k in calls if isinstance(k.get('name'), ast.Constant) and k['name'].value == contract['name']]
            value = targets[0][1].get(contract['keyword']) if len(targets) == 1 else None
            if 'selector' in contract:
                selected = []
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'select' and len(value.args) == 1:
                    mapping = value.args[0]
                    if isinstance(mapping, ast.Dict):
                        selected = [v for k, v in zip(mapping.keys, mapping.values) if isinstance(k, ast.Constant) and k.value == contract['selector']]
                value = selected[0] if len(selected) == 1 else None
            elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'glob' and len(value.args) == 1:
                value = value.args[0]
            entries = literal_list(value)
            foreign = [v for v in (entries or []) if contract.get('versioned_value_pattern') and
                       re.search(contract['versioned_value_pattern'], v) and v not in contract['values']]
            if entries is None or not set(contract['values']).issubset(entries) or foreign:
                emit('BUILD005', targets[0][0].lineno if targets else 1,
                     f"{contract['name']} / {contract['keyword']} must select {', '.join(contract['values'])}",
                     contract['name'] + ' / ' + contract['keyword'])
    return out


def check_change(policy: Policy, change: Change, build_text: str = '') -> list[Diagnostic]:
    if change.audit:
        return []
    out = []
    path = change.path
    layer = policy.layer(path)
    local = policy.camera_relative(path)
    line = min(change.added_lines) if change.added_lines else 1
    changed_count = len(change.added_lines) + len(change.deleted_lines)

    def emit(rule, message, evidence=''):
        out.append(policy.diagnostic(rule, path, line, message, evidence))

    if change.after is not None:
        _, after_encoding = decode_source(change.after)
        if change.new and after_encoding != 'utf-8':
            emit('STYLE009', 'New source must use UTF-8', after_encoding)
        elif change.before is not None:
            _, before_encoding = decode_source(change.before)
            # ASCII-only data is valid in both encodings; deleting all legacy
            # comments is not itself an encoding conversion.
            if before_encoding != after_encoding and any(b >= 128 for b in change.before) and any(b >= 128 for b in change.after):
                emit('STYLE009', f'Source encoding changed from {before_encoding} to {after_encoding}', f'{before_encoding} -> {after_encoding}')

    owner = policy.version_domain(local)
    if owner and not policy.version_selected(owner[0], owner[1], local):
        emit('ARCH007', f"Changed {owner[0]['name']} version {owner[1]} is outside profile {policy.profile['id']} selection; review shared/other-project impact", local)
    if local and path.endswith('.h') and any(fnmatch.fnmatchcase(local, p) for p in policy.data['public_header_patterns']):
        emit('ABI001', 'Shared header contract changed; review callers and layout', local)
    if layer == 'register' or any(fnmatch.fnmatchcase(path, p) for p in policy.data.get('long_line_exempt_paths', [])):
        emit('REVIEW001', 'Register/sensor table changed; attach hardware validation evidence', local)
    if changed_count > policy.data['max_patch_lines']:
        emit('REVIEW002', f'Large file diff: {changed_count} added/deleted lines', str(changed_count))
    if path.endswith(('.bzl', '.bazel', '.fragment')) or PurePosixPath(path).name in ('Kconfig', 'Makefile'):
        emit('BUILD002', 'Build/config selection changed; review variants: ' + ', '.join(policy.profile['review_variants']), path)
    if change.new and path.endswith('.c') and layer != 'test':
        filename = PurePosixPath(path).name
        if filename not in build_text and filename[:-2] + '.o' not in build_text:
            emit('BUILD001', 'New C source has no visible literal Kbuild/DDK entry; check glob selection', filename)
    if path.endswith(('.c', '.h')):
        for data, sign in ((change.before, 'removed'), (change.after, 'added')):
            if data is None:
                continue
            source = Source(data)
            if sign == 'added':
                lines = change.added_lines
                for match in EXPORT.finditer(source.code):
                    pos = source.line_at(match.start())
                    if pos in lines:
                        out.append(policy.diagnostic('ABI002', path, pos, 'Export declaration added or changed', source.evidence(pos)))
            else:
                for raw in change.deleted_lines:
                    if EXPORT.search(raw) and not raw.lstrip().startswith(('*', '//', '/*')):
                        emit('ABI002', 'Export declaration removed or changed', raw)
    if change.deleted:
        if path in policy.protected_paths():
            emit('BUILD005', 'Profile build contract or selected HW implementation was deleted', path)
    return out
