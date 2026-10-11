"""Behavior tests for enforcement, regression selection, hooks, and reports."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from driverlint.checks import check_source
from driverlint.cli import WORKSPACE, main
from driverlint.engines import checkpatch, clang_format
from driverlint.hooks import main as hook_main
from driverlint.model import Change, LintError, Policy
from driverlint.report import render
from driverlint.runner import run
from driverlint.selection import Snapshot, create_snapshot, git_changes, select
from driverlint.source import Source, changed_lines, patch_for


CAMERA = 'exynos/external-modules/camera/camera'
CORE = CAMERA + '/post/gdc/pablo-gdc-core.c'
API = CAMERA + '/post/gdc/pablo-hw-api-gdc-v1400.c'
REG = CAMERA + '/post/gdc/pablo-hw-reg-gdc-v1400.h'
FRAGMENT = 'exynos/device/erd/erd9965/arch/arm64/configs/erd9965.fragment'
SPDX = '// SPDX-License-Identifier: GPL-2.0\n'
VALID = SPDX + 'static int gdc_run(void)\n{\n\treturn 0;\n}\n'


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='driver-lint-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = json.loads((WORKSPACE / 'driver_lint/config/policy.json').read_text())
        self.project = json.loads((WORKSPACE / 'driver_lint/config/profiles/s947b.json').read_text())
        self.policy_path = self.root / 'driver_lint/config/policy.json'
        self.write_policy()
        self.write(CORE, VALID)
        self.write(API, SPDX + '#include "pablo-hw-reg-gdc-v1400.h"\n' + VALID[len(SPDX):])
        self.write(REG, '/* SPDX-License-Identifier: GPL-2.0 */\n#define GDC_REG 1\n')
        self.write_build_fixture(self.project)
        self.policy = Policy(self.root, self.policy_path)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
        return target

    def write_policy(self):
        self.write('driver_lint/config/policy.json', json.dumps(self.settings))
        self.write('driver_lint/config/profiles/s947b.json', json.dumps(self.project))

    def write_build_fixture(self, project):
        for fragment in project['fragments']:
            self.write(fragment['path'], '\n'.join(k + '=' + v for k, v in fragment['requirements'].items()) + '\n')
        sources = {}
        for c in project['build_contracts']['kconfig']:
            sources.setdefault(c['path'], []).append('config ' + c['symbol'] + '\n' +
                ('\tdepends on ' + c['depends_on'] + '\n' if 'depends_on' in c else '\tdefault n\n'))
        for c in project['build_contracts']['kbuild']:
            sources.setdefault(c['path'], []).append(c['target'] + '-$(' + c['config'] + ') += ' + ' '.join(c['sources']) + '\n')
        for c in project['build_contracts']['ddk']:
            sources.setdefault(c['path'], []).append('ddk_module(name=' + repr(c['module']) +
                ', conditional_srcs=' + repr({c['config']: {True: c['sources']}}) + ')\n')
        for c in project['build_contracts']['selectors']:
            sources.setdefault(c['path'], []).append('selects.config_setting_group(name=' + repr(c['name']) +
                ', match_any=' + repr(c['boards']) + ')\n')
        for c in project['build_contracts'].get('literals', []):
            value = 'select(' + repr({c['selector']: c['values']}) + ')' if 'selector' in c else 'glob(' + repr(c['values']) + ')'
            sources.setdefault(c['path'], []).append('filegroup(name=' + repr(c['name']) + ', ' + c['keyword'] + '=' + value + ')\n')
        for path, content in sources.items():
            self.write(path, ''.join(content))
        for path in project['protected_sources']:
            if not (self.root / path).exists():
                self.write(path, VALID if path.endswith('.c') else SPDX + '#define TEST_REG 0\n')

    def snapshot(self):
        create_snapshot(self.policy, Path(self.settings['snapshot']))
        return Snapshot(self.policy, Path(self.settings['snapshot']))

    def findings(self, text, path=CORE):
        return check_source(self.policy, path, text.encode())

    def rules(self, text, path=CORE):
        return {d.rule for d in self.findings(text, path)}

    def regress(self, old, new, path=CORE):
        before = old.encode() if old is not None else None
        after = new.encode() if new is not None else None
        change = changed_lines(Change(path, before, after))
        return run(self.policy, 'test', [change], checkpatch_mode='off')

    def command(self, *args):
        process = subprocess.run(list(args), cwd=self.root, capture_output=True, text=True)
        if process.returncode:
            raise AssertionError(process.stderr)
        return process.stdout

    def git_init(self, commit=True):
        self.command('git', 'init', '-q')
        self.command('git', 'add', '.')
        if commit:
            self.commit()

    def commit(self):
        self.command('git', '-c', 'user.name=Driver Lint Test', '-c', 'user.email=lint@example.invalid',
                     'commit', '-q', '-m', 'test reference')


class ArchitectureTests(WorkspaceTest):
    def test_illegal_layer_edges(self):
        cases = [
            (CORE, '#include "other.c"', 'ARCH001'),
            (CORE, '#include "pablo-hw-reg-gdc-v1400.h"', 'ARCH002'),
            (CORE, 'void gdc_run(void) { writel(1, base); }', 'ARCH003'),
            (API, '#include "is-groupmgr.h"', 'ARCH004'),
            (CORE, '#include "testing/mock.h"', 'ARCH005'),
            (API, '#include "pablo-hw-reg-gdc-v1500.h"', 'ARCH006'),
            (API, '#include "include/v15_0_0/is-param.h"', 'ARCH008'),
        ]
        for path, text, rule in cases:
            with self.subTest(rule=rule):
                self.assertIn(rule, self.rules(SPDX + text + '\n', path))

    def test_existing_supported_dependencies_are_allowed(self):
        self.assertNotIn('ARCH004', self.rules(SPDX + '#include "pablo-gdc.h"\n#include "is-hw.h"\n', API))
        test = CAMERA + '/testing/kunit/post/test.c'
        self.assertNotIn('ARCH001', self.rules(SPDX + '#include "pablo-gdc-core.c"\n', test))
        self.assertNotIn('ARCH003', self.rules(SPDX + 'void gdc_hw(void) { writel(1, base); }\n', API))

    def test_comments_strings_and_character_literals_do_not_trigger_calls(self):
        text = SPDX + '/* writel(1, base); BUG(); */\n// readl(base);\n' + 'const char *a = "writel(1, base); BUG();";\nchar c = \'"\';\n'
        self.assertNotIn('ARCH003', self.rules(text))
        self.assertNotIn('SAFE001', self.rules(text))

    def test_spliced_line_comment_is_ignored(self):
        self.assertNotIn('ARCH003', self.rules(SPDX + '// hidden \\\nwritel(1, base);\n'))

    def test_include_with_comment_is_parsed(self):
        self.assertIn('ARCH002', self.rules(SPDX + '#include /* reason */ "pablo-hw-reg-gdc-v1400.h"\n'))
        self.assertNotIn('ARCH002', self.rules(SPDX + '/* #include "pablo-hw-reg-gdc-v1400.h" */\n'))

    def test_non_target_change_warns_but_other_ip_reuse_is_allowed(self):
        old = VALID
        new = VALID.replace('return 0;', 'return 1;')
        result = self.regress(old, new, CAMERA + '/ischain/is-v15_0_0/is-subdev.c')
        self.assertIn('ARCH007', {d.rule for d in result.diagnostics})
        result = self.regress(old, new, CAMERA + '/hardware/api/is-hw-api-yuvp-v13_0.c')
        self.assertNotIn('ARCH007', {d.rule for d in result.diagnostics})

    def test_gdc_mmio_version_match(self):
        self.assertNotIn('ARCH006', self.rules(SPDX + '#include "pablo-hw-reg-gdc-mmio-v1400.h"\n', API))

    def test_existing_gdc_1201_compatibility_is_allowed(self):
        path = API.replace('v1400.c', 'v1200.c')
        self.assertNotIn('ARCH006', self.rules(SPDX + '#include "pablo-hw-reg-gdc-v1201.h"\n', path))
        self.assertIn('ARCH006', self.rules(SPDX + '#include "pablo-hw-reg-gdc-v1500.h"\n', path))

    def test_simple_positive_kunit_guard_is_allowed(self):
        for guard in ('#if IS_ENABLED(CONFIG_PABLO_KUNIT_TEST)', '#ifdef CONFIG_KUNIT'):
            with self.subTest(guard=guard):
                self.assertNotIn('ARCH005', self.rules(SPDX + guard + '\n#include <kunit/test.h>\n#endif\n'))
        self.assertIn('ARCH005', self.rules(SPDX + '#if !IS_ENABLED(CONFIG_KUNIT)\n#include <kunit/test.h>\n#endif\n'))
        self.assertIn('ARCH005', self.rules(SPDX + '#ifdef CONFIG_KUNIT\n#else\n#include <kunit/test.h>\n#endif\n'))

    def test_generated_table_length_exemption_keeps_change_review(self):
        text = SPDX + ('0x00, ' * 50) + '\n'
        self.assertNotIn('STYLE003', self.rules(text, REG))
        self.assertIn('STYLE003', self.rules(text, CORE))
        result = self.regress(None, text, REG)
        self.assertIn('REVIEW001', {d.rule for d in result.diagnostics})


class ProfileTests(WorkspaceTest):
    def alternate_profile(self):
        # Synthetic test data, not a specification for an unreleased SoC.
        content = json.dumps(self.project).replace('PABLO_V14_0_0', 'PABLO_V99_0_0')
        content = content.replace('GDC_V14_0_0', 'GDC_V77_0_0').replace('v14_0_0', 'v99_0_0')
        content = content.replace('1400', '7700').replace('S5E9965', 'TEST_SOC').replace('ERD9965', 'TEST_BOARD')
        content = content.replace('14.0.0', '99.0.0')
        content = content.replace('erd9965', 'testboard')
        project = json.loads(content)
        project.update(id='synthetic', device='Synthetic test fixture', soc='TEST_SOC')
        self.write('driver_lint/config/profiles/synthetic.json', json.dumps(project))
        self.write_build_fixture(project)
        api = CAMERA + '/post/gdc/pablo-hw-api-gdc-v7700.c'
        self.write(api, SPDX + '#include "pablo-hw-reg-gdc-v7700.h"\n' + VALID[len(SPDX):])
        return Policy(self.root, self.policy_path, 'synthetic'), project, api

    def test_common_policy_contains_no_project_hw_values(self):
        self.assertNotIn('target', self.settings)
        self.assertNotIn('scope', self.settings)
        self.assertNotIn('fragment_requirements', self.settings)
        self.assertNotIn('1400', json.dumps(self.settings))
        self.assertEqual(self.policy.profile['id'], 's947b')

    def test_new_family_and_independent_ip_versions_pass_without_checker_edits(self):
        policy, project, api = self.alternate_profile()
        args = SimpleNamespace(profile_only=True)
        mode, changes, notes = select(policy, args)
        result = run(policy, mode, changes, checkpatch_mode='off', notes=notes)
        self.assertEqual(result.exit_code('error'), 0, result.diagnostics)
        versions = policy.data['target']['versions']
        self.assertEqual(versions['pablo-family'], ['v99_0_0'])
        self.assertEqual(versions['gdc'], ['7700'])
        self.assertEqual(versions['yuvp'], ['13_0'])
        text = SPDX + '#include "include/v99_0_0/is-param.h"\n'
        self.assertNotIn('ARCH008', {d.rule for d in check_source(policy, api, text.encode())})
        ischain = CAMERA + '/ischain/is-v99_0_0/is-subdev.c'
        change = changed_lines(Change(ischain, VALID.encode(), VALID.replace('return 0;', 'return 1;').encode()))
        self.assertNotIn('ARCH007', {d.rule for d in run(policy, 'test', [change], checkpatch_mode='off').diagnostics})

    def test_new_profile_rejects_wrong_ip_register_and_family(self):
        policy, project, api = self.alternate_profile()
        wrong_register = SPDX + '#include "pablo-hw-reg-gdc-v9900.h"\n'
        wrong_family = SPDX + '#include "include/v14_0_0/is-param.h"\n'
        self.assertIn('ARCH006', {d.rule for d in check_source(policy, api, wrong_register.encode())})
        self.assertIn('ARCH008', {d.rule for d in check_source(policy, api, wrong_family.encode())})
        fragment = project['fragments'][0]
        text = (self.root / fragment['path']).read_text().replace('GDC_V77_0_0', 'GDC_V14_0_0')
        self.assertTrue({'BUILD003', 'BUILD004'}.issubset({d.rule for d in check_source(policy, fragment['path'], text.encode())}))

    def test_profile_driven_yuvp_register_pairing(self):
        api = CAMERA + '/hardware/api/is-hw-api-yuvp-v13_0.c'
        self.assertNotIn('ARCH006', self.rules(SPDX + '#include "sfr/is-sfr-yuvp-v13_0.h"\n', api))
        self.assertIn('ARCH006', self.rules(SPDX + '#include "sfr/is-sfr-yuvp-v14_0.h"\n', api))

    def test_existing_yuvp_3_0_conditional_register_reuse_is_allowed(self):
        api = CAMERA + '/hardware/api/is-hw-api-yuvp-v3_0.c'
        for header in ('is-sfr-yuvp-v3_0.h', 'is-sfr-yuvp-v3_1.h', 'is-sfr-yuvp-mmio-v3_0.h'):
            with self.subTest(header=header):
                self.assertNotIn('ARCH006', self.rules(SPDX + '#include "sfr/' + header + '"\n', api))
        self.assertIn('ARCH006', self.rules(SPDX + '#include "sfr/is-sfr-yuvp-v13_0.h"\n', api))

    def test_family_include_and_glob_selection_follow_profile(self):
        path = 'exynos/external-modules/camera/BUILD.bazel'
        good = (self.root / path).read_text()
        self.assertNotIn('BUILD005', self.rules(good, path))
        for before, after in (('camera/ischain/is-v14_0_0', 'camera/ischain/is-v15_0_0'),
                              ('is-hw-api-yuvp-v13_0.c', 'is-hw-api-yuvp-v14_0.c'),
                              ('match_any=', 'unrelated_data=')):
            with self.subTest(before=before):
                self.assertIn('BUILD005', self.rules(good.replace(before, after), path))

    def test_common_code_uses_shared_contract_without_explicit_hw_family(self):
        self.assertIn('ARCH008', self.rules(SPDX + '#include "include/v14_0_0/is-param.h"\n', CORE))
        self.assertNotIn('ARCH008', self.rules(SPDX + '#include "pablo-hw-api-gdc.h"\n', CORE))

    def test_explicit_common_selector_needs_evidence(self):
        self.project['common_family_selectors'] = [{'path':'include/is-config.h', 'reason':'Reviewed compile-time family dispatch',
                                                   'evidence':'Kconfig/compiler include selection reviewed'}]
        self.write_policy()
        self.policy = Policy(self.root, self.policy_path)
        path = CAMERA + '/include/is-config.h'
        self.assertNotIn('ARCH008', self.rules(SPDX + '#include "v14_0_0/is-config.h"\n', path))
        del self.project['common_family_selectors'][0]['evidence']
        self.write_policy()
        with self.assertRaises(LintError):
            Policy(self.root, self.policy_path)

    def test_compatibility_changes_require_reason_and_evidence(self):
        entry = self.project['version_domains'][1]['register_compatibility']['1200']
        del entry['reason']
        self.write_policy()
        with self.assertRaises(LintError):
            Policy(self.root, self.policy_path)

    def test_multiple_selected_family_versions_allow_intentional_shared_work(self):
        self.project['version_domains'][0]['selected_versions'].append('v15_0_0')
        path = CAMERA + '/ischain/is-v15_0_0/is-subdev.c'
        self.project['protected_sources'].append(path)
        self.write(path, VALID)
        self.write_policy()
        self.policy = Policy(self.root, self.policy_path)
        result = self.regress(VALID, VALID.replace('return 0;', 'return 1;'), CAMERA + '/ischain/is-v15_0_0/is-subdev.c')
        self.assertNotIn('ARCH007', {d.rule for d in result.diagnostics})

    def test_profile_version_label_cannot_disagree_with_build_sources(self):
        self.project['version_domains'][1]['selected_versions'] = ['7700']
        self.write_policy()
        with self.assertRaisesRegex(LintError, 'selection conflicts'):
            Policy(self.root, self.policy_path)

    def test_evidenced_api_register_reuse_is_valid_for_selected_hw(self):
        self.project['version_domains'][1]['register_compatibility']['1400'] = {
            'versions':['1400','1201'], 'reason':'Synthetic compatible HW test', 'evidence':'Synthetic register contract receipt'}
        reg = REG.replace('v1400.h', 'v1201.h')
        self.project['protected_sources'].append(reg)
        self.write(reg, SPDX + '#define TEST_REG 1\n')
        self.write_policy()
        self.policy = Policy(self.root, self.policy_path)
        self.assertNotIn('ARCH006', self.rules(SPDX + '#include "pablo-hw-reg-gdc-v1201.h"\n', API))
        result = self.regress(SPDX + '#define TEST_REG 1\n', SPDX + '#define TEST_REG 2\n', reg)
        self.assertNotIn('ARCH007', {d.rule for d in result.diagnostics})

    def test_profile_change_affects_hash_without_resetting_source_reference(self):
        snap = self.snapshot()
        before = snap.path.read_bytes()
        self.project['soc'] = 'Reviewed new project label'
        self.write_policy()
        policy = Policy(self.root, self.policy_path)
        self.assertNotEqual(policy.hash, self.policy.hash)
        self.assertEqual(Snapshot(policy, snap.path).changes(), [])
        self.assertEqual(snap.path.read_bytes(), before)

    def test_invalid_profile_regex_is_operational_error(self):
        self.project['version_domains'][1]['path_pattern'] = '('
        self.write_policy()
        with self.assertRaises(LintError):
            Policy(self.root, self.policy_path)

    def test_named_cli_profile_override_verifies_current_build_contracts(self):
        self.alternate_profile()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['scan', '--root', str(self.root), '--policy', str(self.policy_path),
                         '--profile', 'synthetic', '--profile-only', '--checkpatch', 'off', '--format', 'json'])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['profile']['id'], 'synthetic')
        self.assertEqual(result['mode'], 'profile')
        self.assertIn('TEST_SOC', render(run(self.policy, 'test', [], checkpatch_mode='off'),
                                       Policy(self.root, self.policy_path, 'synthetic'), 'error', 'html'))

    def test_profile_only_detects_unchanged_build_mismatch_and_missing_artifact(self):
        self.snapshot()
        path = CAMERA + '/post/gdc/Makefile'
        self.write(path, (self.root / path).read_text().replace('v1400.o', 'v1500.o'))
        mode, changes, notes = select(self.policy, SimpleNamespace(profile_only=True))
        result = run(self.policy, mode, changes, checkpatch_mode='off', notes=notes)
        self.assertIn('BUILD005', {d.rule for d in result.diagnostics})
        (self.root / REG).unlink()
        with self.assertRaises(LintError):
            select(self.policy, SimpleNamespace(profile_only=True))


class RegressionAndSafetyTests(WorkspaceTest):
    def test_existing_finding_does_not_block_unrelated_edit(self):
        old = VALID + 'void gdc_bad(void) { writel(1, base); }\n'
        result = self.regress(old, old.replace('return 0;', 'return 1;'))
        self.assertEqual(result.exit_code('error'), 0)
        self.assertGreater(result.existing_count, 0)

    def test_added_identical_bad_statement_is_not_hidden(self):
        bad = '\twritel(1, base);\n'
        old = SPDX + 'void gdc_run(void)\n{\n' + bad + '}\n'
        result = self.regress(old, old.replace(bad, bad + bad))
        issues = [d for d in result.diagnostics if d.rule == 'ARCH003']
        self.assertEqual(len(issues), 1)
        self.assertIn(issues[0].line, changed_lines(Change(CORE, old.encode(), old.replace(bad, bad + bad).encode())).added_lines)

    def test_new_violation_in_existing_function_is_reported(self):
        result = self.regress(VALID, VALID.replace('return 0;', 'writel(1, base);\n\treturn 0;'))
        self.assertEqual(result.exit_code('error'), 1)
        self.assertIn('ARCH003', {d.rule for d in result.diagnostics})

    def test_removed_spdx_and_final_newline_are_reported(self):
        result = self.regress(VALID, VALID[len(SPDX):].rstrip('\n'))
        self.assertTrue({'STYLE006', 'STYLE007'}.issubset({d.rule for d in result.diagnostics}))

    def test_crlf_conversion_is_a_new_error(self):
        result = self.regress(VALID, VALID.replace('\n', '\r\n'))
        self.assertIn('STYLE004', {d.rule for d in result.diagnostics})

    def test_spinlock_sleep_and_irq_logging(self):
        text = SPDX + '''static irqreturn_t gdc_irq(int irq, void *data)
{
\tspin_lock(&lock);
\tmsleep(10);
\tpr_info("IRQ");
\tspin_unlock(&lock);
\treturn IRQ_HANDLED;
}
'''
        self.assertTrue({'SAFE002', 'SAFE003', 'SAFE005'}.issubset(self.rules(text)))

    def test_safe_patterns_are_reported(self):
        examples = {
            'SAFE001': 'void gdc_run(void) { BUG(); }',
            'SAFE004': 'void gdc_run(void) { while (1) { readl(base); } }',
            'SAFE006': 'void gdc_run(void) { u32 value = (u32)dma_addr; }',
            'SAFE007': 'void gdc_run(void) { n = ALIGN(size, 4096); }',
            'SAFE008': 'void gdc_run(void)\n{\n\tpm_runtime_get_sync(dev);\n}',
            'SAFE009': 'void gdc_run(void)\n{\n\tcopy_from_user(dst, src, size);\n}',
            'SAFE010': 'void gdc_run(void) { dma_map_single(dev, ptr, len, DMA_TO_DEVICE); }',
            'SAFE011': 'void gdc_run(void) { a = kzalloc(count * sizeof(*a), GFP_KERNEL); }',
        }
        for rule, text in examples.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, self.rules(SPDX + text + '\n'))

    def test_timeout_and_mapping_error_checks_avoid_heuristic_warning(self):
        text = SPDX + 'void gdc_run(void) { while (1) { readl(base); if (timeout) break; }\n' + \
            'a = dma_map_single(dev, ptr, n, DMA_TO_DEVICE); if (dma_mapping_error(dev, a)) return; }\n'
        self.assertNotIn('SAFE004', self.rules(text))
        self.assertNotIn('SAFE010', self.rules(text))

    def test_warning_threshold_is_configurable(self):
        result = self.regress(VALID, VALID.replace('return 0;', 'return 0;   '))
        self.assertEqual(result.exit_code('error'), 0)
        self.assertEqual(result.exit_code('warning'), 1)

    def test_exception_needs_owner_reason_and_keeps_receipt(self):
        self.settings['suppressions'] = [{'rule': 'ARCH003', 'path': CORE, 'reason': 'explicit hardware exception', 'owner': 'reviewer', 'evidence': 'writel'}]
        self.write_policy()
        self.policy = Policy(self.root, self.policy_path)
        result = self.regress(VALID, VALID.replace('return 0;', 'writel(1, base);\n\treturn 0;'))
        self.assertEqual(result.exit_code('error'), 0)
        self.assertEqual(result.suppressions[0]['owner'], 'reviewer')
        del self.settings['suppressions'][0]['owner']
        self.write_policy()
        with self.assertRaises(LintError):
            Policy(self.root, self.policy_path)


class SnapshotTests(WorkspaceTest):
    def test_unchanged_snapshot_is_empty(self):
        self.assertEqual(self.snapshot().changes(), [])

    def test_snapshot_detects_addition_modification_and_deletion(self):
        snap = self.snapshot()
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        new = CAMERA + '/lib/pablo-new.c'
        self.write(new, VALID)
        (self.root / REG).unlink()
        changes = {c.path: c for c in snap.changes()}
        self.assertEqual(set(changes), {CORE, new, REG})
        self.assertTrue(changes[new].new)
        self.assertTrue(changes[REG].deleted)
        self.assertTrue(changes[CORE].added_lines)

    def test_required_fragment_deletion_is_reported(self):
        snap = self.snapshot()
        (self.root / FRAGMENT).unlink()
        result = run(self.policy, 'snapshot', snap.changes(), checkpatch_mode='off')
        self.assertIn('BUILD005', {d.rule for d in result.diagnostics})

    def test_snapshot_is_not_reset_implicitly(self):
        self.snapshot()
        with self.assertRaises(LintError):
            create_snapshot(self.policy, Path(self.settings['snapshot']))
        create_snapshot(self.policy, Path(self.settings['snapshot']), replace=True)
        self.assertTrue((self.root / (self.settings['snapshot'] + '.previous')).is_file())

    def test_corrupt_snapshot_object_fails(self):
        snap = self.snapshot()
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        obj = snap.objects / (snap.data['files'][CORE] + '.gz')
        obj.write_bytes(b'bad gzip')
        with self.assertRaises(LintError):
            snap.changes()

    def test_scope_mismatch_fails(self):
        self.snapshot()
        self.settings['exclude'].append('*/vendor/*')
        self.write_policy()
        with self.assertRaises(LintError):
            Snapshot(Policy(self.root, self.policy_path), Path(self.settings['snapshot']))

    def test_outside_workspace_and_symlinks_fail(self):
        with self.assertRaises(LintError):
            self.policy.safe_path('../outside.c')
        alias = self.root / (CAMERA + '/alias.c')
        alias.symlink_to(self.root / CORE)
        with self.assertRaises(LintError):
            self.policy.files()
        args = SimpleNamespace(all=False, files=[str(alias)], staged=False, base=None, since=None)
        with self.assertRaises(LintError):
            select(self.policy, args)

    def test_missing_reference_is_operational_failure(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(['scan', '--root', str(self.root), '--policy', str(self.policy_path)])
        self.assertEqual(code, 2)

    def test_source_cannot_be_snapshot_output(self):
        with self.assertRaises(LintError):
            create_snapshot(self.policy, Path(CORE), replace=True)

    def test_legacy_cp949_is_preserved_and_conversion_reported(self):
        old = (SPDX + '/* 기존 주석 */\n').encode('cp949')
        self.write(REG, old)
        snap = self.snapshot()
        self.assertEqual(snap.changes(), [])
        after = (SPDX + '/* 새 주석 */\n').encode('cp949')
        self.write(REG, after)
        result = run(self.policy, 'snapshot', snap.changes(), checkpatch_mode='off')
        self.assertNotIn('STYLE009', {d.rule for d in result.diagnostics})
        self.write(REG, after.decode('cp949').encode('utf-8'))
        result = run(self.policy, 'snapshot', snap.changes(), checkpatch_mode='off')
        self.assertIn('STYLE009', {d.rule for d in result.diagnostics})


@unittest.skipUnless(shutil.which('git'), 'Git is required for Git selection tests')
class GitTests(WorkspaceTest):
    def test_staged_bytes_are_independent_of_worktree(self):
        self.git_init()
        staged = VALID.replace('return 0;', 'return 7;')
        self.write(CORE, staged)
        self.command('git', 'add', CORE)
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        changes = git_changes(self.policy, 'staged')
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].after, staged.encode())
        self.assertEqual(run(self.policy, 'staged', changes, checkpatch_mode='off').exit_code('error'), 0)
        self.assertEqual(run(self.policy, 'changed', git_changes(self.policy, 'changed'), checkpatch_mode='off').exit_code('error'), 1)

    def test_ignored_source_addition_is_included(self):
        self.git_init()
        self.write('.gitignore', '*-generated.c\n')
        rel = CAMERA + '/lib/pablo-generated.c'
        self.write(rel, VALID)
        self.assertIn(rel, [c.path for c in git_changes(self.policy, 'changed')])

    def test_base_catches_committed_changes(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git', 'add', CORE)
        self.commit()
        self.assertEqual(git_changes(self.policy, 'changed'), [])
        self.assertEqual(run(self.policy, 'base', git_changes(self.policy, 'base', base), checkpatch_mode='off').exit_code('error'), 1)

    def test_deleted_and_renamed_source_are_not_skipped(self):
        self.git_init()
        renamed = CAMERA + '/post/gdc/pablo-renamed.c'
        self.command('git', 'mv', CORE, renamed)
        changes = {c.path: c for c in git_changes(self.policy, 'changed')}
        self.assertTrue(changes[CORE].deleted)
        self.assertTrue(changes[renamed].new)

    def test_unborn_index_and_worktree(self):
        self.git_init(commit=False)
        self.assertTrue(git_changes(self.policy, 'changed'))
        self.assertTrue(git_changes(self.policy, 'staged'))


class BuildTests(WorkspaceTest):
    def test_fragment_version_and_duplicate_selection(self):
        valid = (self.root / FRAGMENT).read_text()
        self.assertNotIn('BUILD003', self.rules(valid, FRAGMENT))
        invalid = valid.replace('CONFIG_CAMERA_PP_GDC_V14_0_0_OBJ=y', 'CONFIG_CAMERA_PP_GDC_V15_0_0_OBJ=y')
        self.assertTrue({'BUILD003', 'BUILD004'}.issubset(self.rules(invalid, FRAGMENT)))
        duplicate = valid + 'CONFIG_CAMERA_PP_GDC_V14_0_0_OBJ=y\n'
        self.assertIn('BUILD003', self.rules(duplicate, FRAGMENT))

    def test_kconfig_target_dependency_is_enforced(self):
        path = CAMERA + '/Kconfig'
        good = 'config PABLO_V14_0_0\n\tdepends on SOC_S5E9965\nconfig CAMERA_PP_GDC_V14_0_0_OBJ\n\tdefault n\n'
        self.assertNotIn('BUILD005', self.rules(good, path))
        self.assertIn('BUILD005', self.rules(good.replace('SOC_S5E9965', 'SOC_S5E9975'), path))

    def test_gdc_kbuild_mapping_is_enforced(self):
        path = CAMERA + '/post/gdc/Makefile'
        good = 'gdc-$(CONFIG_CAMERA_PP_GDC_V14_0_0_OBJ) += pablo-hw-api-gdc-v1400.o\n'
        self.assertNotIn('BUILD005', self.rules(good, path))
        self.assertIn('BUILD005', self.rules(good.replace('v1400.o', 'v1500.o'), path))

    def test_ddk_true_branch_and_board_selector_are_enforced(self):
        path = 'exynos/external-modules/camera/BUILD.bazel'
        good = (self.root / path).read_text()
        self.assertNotIn('BUILD005', self.rules(good, path))
        for bad in (good.replace('v1400.c', 'v1500.c'), good.replace('ERD9965_16K', 'ERD8865'), good.replace('True:', 'False:')):
            with self.subTest(bad=bad):
                self.assertIn('BUILD005', self.rules(bad, path))

    def test_new_source_and_shared_header_review(self):
        result = self.regress(None, VALID, CAMERA + '/lib/pablo-new.c')
        self.assertIn('BUILD001', {d.rule for d in result.diagnostics})
        result = self.regress(VALID, VALID + '\n', CAMERA + '/include/v14_0_0/is-param.h')
        self.assertIn('ABI001', {d.rule for d in result.diagnostics})


class HookAndReportTests(WorkspaceTest):
    def hook(self, payload):
        stdout = io.StringIO()
        stderr = io.StringIO()
        # Hook routing is tested against real snapshot/regression logic. The
        # separate external-engine test exercises the bundled checkpatch parser.
        reduced_run = lambda *a, **k: run(*a, checkpatch_mode='off', notes=k.get('notes'))
        with patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.root)}), \
             patch('sys.stdin', io.StringIO(json.dumps(payload))), \
             patch('driverlint.hooks.run', side_effect=reduced_run), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = hook_main()
        value = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
        return code, value, stderr.getvalue()

    def test_stop_blocks_once_and_reports_retry_failure(self):
        self.snapshot()
        self.write(CORE, VALID.replace('return 0;', 'writel(1, base);\n\treturn 0;'))
        code, result, _ = self.hook({'hook_event_name': 'Stop', 'stop_hook_active': False})
        self.assertEqual(code, 0)
        self.assertEqual(result['decision'], 'block')
        self.assertIn('ARCH003', result['reason'])
        _, result, _ = self.hook({'hook_event_name': 'Stop', 'stop_hook_active': True})
        self.assertNotIn('decision', result)
        self.assertIn('still FAILS', result['systemMessage'])

    def test_post_edit_feedback_and_bash_scan(self):
        self.snapshot()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        for tool, inputs in [('Edit', {'file_path': str(self.root / CORE)}), ('Bash', {'command': 'ignored'})]:
            with self.subTest(tool=tool):
                code, result, _ = self.hook({'hook_event_name': 'PostToolUse', 'tool_name': tool, 'tool_input': inputs})
                self.assertEqual(code, 0)
                self.assertEqual(result['decision'], 'block')
                self.assertIn('SAFE001', result['reason'])

    def test_failed_tool_still_reports_partial_source_changes(self):
        self.snapshot()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        _, result, _ = self.hook({'hook_event_name': 'PostToolUseFailure', 'tool_name': 'Bash', 'tool_input': {'command': 'ignored'}})
        self.assertEqual(result['hookSpecificOutput']['hookEventName'], 'PostToolUseFailure')
        self.assertIn('SAFE001', result['hookSpecificOutput']['additionalContext'])

    def test_unrelated_file_edit_does_not_scan(self):
        code, result, _ = self.hook({'hook_event_name': 'PostToolUse', 'tool_name': 'Edit', 'tool_input': {'file_path': str(self.root / 'README.md')}})
        self.assertEqual((code, result), (0, {}))

    def test_missing_snapshot_stop_is_blocked(self):
        _, result, _ = self.hook({'hook_event_name': 'Stop'})
        self.assertEqual(result['decision'], 'block')
        self.assertIn('Snapshot unavailable', result['reason'])

    def test_session_start_never_creates_a_reference(self):
        code, result, _ = self.hook({'hook_event_name': 'SessionStart'})
        self.assertEqual(code, 0)
        self.assertIn('Before editing', result['hookSpecificOutput']['additionalContext'])
        self.assertFalse((self.root / self.settings['snapshot']).exists())

    def test_session_start_uses_selected_profile_without_fixed_version_message(self):
        policy, project, api = ProfileTests.alternate_profile(self)
        self.settings['profile'] = 'profiles/synthetic.json'
        self.write_policy()
        self.policy = Policy(self.root, self.policy_path)
        self.snapshot()
        _, result, _ = self.hook({'hook_event_name': 'SessionStart'})
        context = result['hookSpecificOutput']['additionalContext']
        self.assertIn('pablo-family=v99_0_0', context)
        self.assertIn('gdc=7700', context)
        self.assertNotIn('target camera is v14_0_0', context)

    def test_reports_escape_source_and_keep_sarif_locations(self):
        result = self.regress(VALID, VALID + '#include "<script>.c"\n')
        html = render(result, self.policy, 'error', 'html')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>.c', html)
        sarif = json.loads(render(result, self.policy, 'error', 'sarif'))
        self.assertEqual(sarif['version'], '2.1.0')
        self.assertEqual(sarif['runs'][0]['results'][0]['locations'][0]['physicalLocation']['artifactLocation']['uri'], CORE)
        data = json.loads(render(result, self.policy, 'error', 'json'))
        self.assertEqual(data['exit_code'], 1)

    def test_explicit_no_scope_and_non_utf8_fail(self):
        self.write('README.md', 'unrelated')
        args = ['scan', '--root', str(self.root), '--policy', str(self.policy_path)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args + ['--files', 'README.md']), 2)
        self.write(CORE, b'\xff\n')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args + ['--files', CORE, '--checkpatch', 'off']), 2)

    def test_report_cannot_overwrite_other_driver_or_reference(self):
        original = self.write('kernel-6.12/drivers/other.c', 'int original_driver;\n')
        self.snapshot()
        reference = self.root / self.settings['snapshot']
        before = reference.read_bytes()
        args = ['scan', '--root', str(self.root), '--policy', str(self.policy_path), '--checkpatch', 'off']
        for output in (str(original), str(reference)):
            with self.subTest(output=output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args + ['--output', output]), 2)
        self.assertEqual(original.read_text(), 'int original_driver;\n')
        self.assertEqual(reference.read_bytes(), before)


class ExternalEngineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('perl'), 'Perl required')
    def test_bundled_checkpatch_parses_real_patch_diagnostics(self):
        policy = Policy(WORKSPACE, WORKSPACE / 'driver_lint/config/policy.json')
        before = SPDX + 'int gdc_run(void)\n{\n\treturn 0;\n}\n'
        after = SPDX + 'int gdc_run(void){\n\tif(1) return 0;\n}\n'
        change = changed_lines(Change(CORE, before.encode(), after.encode()))
        findings, status = checkpatch(policy, [change], 'on')
        self.assertIn('completed', status)
        self.assertTrue(findings)
        self.assertTrue(all(d.path == CORE and d.rule.startswith('CHECKPATCH.') for d in findings))
        self.assertTrue(any(d.severity == 'error' for d in findings))

    def test_patch_handles_missing_newline(self):
        change = Change(CORE, b'int old;\n', b'int new;')
        self.assertIn('\\ No newline at end of file', patch_for(change))

    def test_requested_missing_formatter_is_error(self):
        policy = Policy(WORKSPACE, WORKSPACE / 'driver_lint/config/policy.json')
        with patch('driverlint.engines.shutil.which', return_value=None):
            with self.assertRaises(LintError):
                clang_format(policy, [], True)


class InstallerTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        source = WORKSPACE / 'driver_lint/integration'
        for path in source.iterdir():
            self.write('driver_lint/integration/' + path.name, path.read_bytes())

    def install(self, *args):
        return subprocess.run([sys.executable, str(WORKSPACE / 'driver_lint/scripts/install_claude.py'),
                               '--root', str(self.root), *args], capture_output=True, text=True)

    def test_install_preserves_other_settings_and_is_idempotent(self):
        original = {'permissions': {'allow': ['Read']}, 'hooks': {'Stop': [
            {'hooks': [{'type': 'command', 'command': 'other-team-check'}]}
        ]}}
        settings = self.write('.claude/settings.json', json.dumps(original))
        local = self.write('.claude/settings.local.json', '{"enabledPlugins": {}}\n')
        self.assertEqual(self.install().returncode, 0)
        installed = settings.read_bytes()
        data = json.loads(installed)
        self.assertEqual(data['permissions'], original['permissions'])
        self.assertEqual(len(data['hooks']['Stop']), 3)
        self.assertEqual(local.read_text(), '{"enabledPlugins": {}}\n')
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(settings.read_bytes(), installed)
        self.assertEqual(self.install('--remove').returncode, 0)
        self.assertEqual(json.loads(settings.read_bytes()), original)

    def test_rule_conflict_does_not_partially_install_hooks(self):
        settings = self.write('.claude/settings.json', '{"permissions": {}}\n')
        original = settings.read_bytes()
        self.write('.claude/rules/driver-lint.md', 'team custom instructions\n')
        self.assertEqual(self.install().returncode, 2)
        self.assertEqual(settings.read_bytes(), original)

    def test_removal_preserves_user_modified_rule(self):
        self.assertEqual(self.install().returncode, 0)
        custom = self.write('.claude/rules/driver-lint.md', 'customized driver rules\n')
        self.assertEqual(self.install('--remove').returncode, 0)
        self.assertEqual(custom.read_text(), 'customized driver rules\n')


if __name__ == '__main__':
    unittest.main()
