"""Real commits, exact indexed configuration/build data and agent adapters."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import test_lint as fixtures
from test_lint import WorkspaceTest, WORKSPACE, CAMERA, CORE, API, VALID, SPDX
from driverlint.gate import evaluate
from driverlint.hooks import main as hook_main
from driverlint.model import LintError
from driverlint.runner import run


class BundledWorkspace(WorkspaceTest):
    def setUp(self):
        super().setUp()
        source = WORKSPACE / 'driver_lint'
        shutil.copytree(source / 'src', self.root / 'driver_lint/src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(source / 'scripts', self.root / 'driver_lint/scripts')
        shutil.copytree(source / 'integration', self.root / 'driver_lint/integration')
        self.write('driver_lint/lint.py', (source / 'lint.py').read_bytes())
        self.write('driver_lint/config/rules.json', (source / 'config/rules.json').read_bytes())
        for directory in ('pablo_is', 'gdc'):
            shutil.copytree(source / directory, self.root / 'driver_lint' / directory,
                            ignore=shutil.ignore_patterns('.state', 'reports', '__pycache__'))
        pablo = json.loads((self.root / 'driver_lint/pablo_is/config/profiles/s947b.json').read_text())
        gdc = json.loads((self.root / 'driver_lint/gdc/config/profiles/s947b.json').read_text())
        for fragment in pablo['fragments']:
            path = self.root / fragment['path']
            path.write_text(path.read_text() + '\n'.join(k+'='+v for k,v in fragment['requirements'].items())+'\n')
        for contract in gdc['build_contracts']['kbuild']:
            path = self.root / contract['path']
            if not path.exists():
                self.write(contract['path'], contract['target']+'-$('+contract['config']+') += '+' '.join(contract['sources'])+'\n')
        for name in ('checkpatch.pl', 'spelling.txt', 'const_structs.checkpatch'):
            self.write('kernel-6.12/scripts/' + name, (WORKSPACE / 'kernel-6.12/scripts' / name).read_bytes())

    def install_git(self, *args):
        return subprocess.run([sys.executable, str(self.root / 'driver_lint/scripts/install_git.py'),
                               '--root', str(self.root), *args], capture_output=True, text=True)

    def install_agents(self, *args):
        return subprocess.run([sys.executable, str(self.root / 'driver_lint/scripts/install_agents.py'),
                               '--root', str(self.root), *args], capture_output=True, text=True)

    def commit_result(self, *extra):
        return subprocess.run(['git', '-c', 'user.name=Driver Lint Test', '-c', 'user.email=lint@example.invalid',
                               'commit', '-q', '-m', 'gate test', *extra], cwd=self.root, capture_output=True, text=True, timeout=90)

    def prepare_hook(self):
        self.git_init()
        self.assertEqual(self.install_git().returncode, 0)


class EnforcementTests(BundledWorkspace):

    def test_real_commit_rejects_lint_error_then_accepts_repair(self):
        self.prepare_hook()
        head = self.command('git', 'rev-parse', 'HEAD')
        self.write(CORE, VALID.replace('return 0;', 'writel(1, base);\n\treturn 0;'))
        self.command('git', 'add', CORE)
        process = self.commit_result()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('ARCH003', process.stdout + process.stderr)
        self.assertEqual(head, self.command('git', 'rev-parse', 'HEAD'))
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        self.command('git', 'add', CORE)
        process = self.commit_result()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('return 1;', self.command('git', 'show', 'HEAD:' + CORE))

    def test_bad_index_and_clean_worktree_still_reject_commit(self):
        self.prepare_hook()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git', 'add', CORE)
        self.write(CORE, VALID)
        self.assertNotEqual(self.commit_result().returncode, 0)

    def test_clean_index_and_bad_worktree_commit_only_clean_bytes(self):
        self.prepare_hook()
        clean = VALID.replace('return 0;', 'return 1;')
        self.write(CORE, clean)
        self.command('git', 'add', CORE)
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        process = self.commit_result()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.command('git', 'show', 'HEAD:' + CORE), clean)

    def test_commit_only_paths_uses_git_temporary_index(self):
        self.prepare_hook()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git', 'add', CORE)
        clean = VALID.replace('return 0;', 'return 2;')
        self.write(CORE, clean)
        process = self.commit_result('--only', CORE)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.command('git', 'show', 'HEAD:' + CORE), clean)

    def test_unstaged_policy_cannot_hide_a_staged_violation(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'writel(1, base);\n\treturn 0;'))
        self.command('git', 'add', CORE)
        self.settings['severity_overrides']['ARCH003'] = 'info'
        self.write_policy()
        result, document, _ = evaluate(self.root, checkpatch_mode='off')
        self.assertEqual(result.exit_code('error'), 1)
        self.assertEqual(document['git_view']['config_from'], 'Git blobs')

    def test_staged_build_error_cannot_be_hidden_by_worktree_repair(self):
        self.git_init()
        path = CAMERA + '/post/gdc/Makefile'
        old = (self.root / path).read_text()
        self.write(path, old.replace('v1400.o', 'v1500.o'))
        self.command('git', 'add', path)
        self.write(path, old)
        result, _, _ = evaluate(self.root, checkpatch_mode='off')
        self.assertIn('BUILD005', {d.rule for d in result.diagnostics})

    def test_unstaged_build_error_does_not_pollute_index_gate(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        self.command('git', 'add', CORE)
        path = CAMERA + '/post/gdc/Makefile'
        self.write(path, (self.root / path).read_text().replace('v1400.o', 'v1500.o'))
        result, _, _ = evaluate(self.root, checkpatch_mode='off')
        self.assertEqual(result.exit_code('error'), 0)

    def test_missing_index_policy_fails_closed(self):
        self.git_init()
        self.command('git', 'rm', 'driver_lint/config/policy.json')
        self.write_policy()
        with self.assertRaisesRegex(LintError, 'regular tracked file'):
            evaluate(self.root, checkpatch_mode='off')

    def test_ci_reads_committed_head_even_when_worktree_is_repaired(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git', 'add', CORE)
        self.commit()
        self.write(CORE, VALID)
        result, document, _ = evaluate(self.root, base=base, checkpatch_mode='off')
        self.assertIn('SAFE001', {d.rule for d in result.diagnostics})
        self.assertFalse(document['git_view']['index'])

    def test_ci_common_lock_detects_rule_weakening(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.settings['severity_overrides']['ARCH003'] = 'info'
        self.write_policy()
        self.command('git', 'add', 'driver_lint/config/policy.json')
        with self.assertRaisesRegex(LintError, 'Common policy changed'):
            evaluate(self.root, lock_common_to=base, checkpatch_mode='off')

    def test_ci_common_lock_allows_intended_project_profile_switch(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        fixtures.ProfileTests.alternate_profile(self)
        self.settings['profile'] = 'profiles/synthetic.json'
        self.write_policy()
        self.command('git', 'add', '.')
        result, document, _ = evaluate(self.root, lock_common_to=base, checkpatch_mode='off')
        self.assertEqual(result.exit_code('error'), 0, result.diagnostics)
        self.assertEqual(document['profile']['id'], 'synthetic')

    def ci_result(self, base, *args):
        bootstrap = self.write('approved-ci-bootstrap.py', self.command(
            'git', 'show', base + ':driver_lint/scripts/run_ci.py'))
        return subprocess.run([sys.executable, '-I', str(bootstrap), '--root', str(self.root),
                               '--base', base, '--policy', 'driver_lint/config/policy.json', *args], capture_output=True, text=True, timeout=90)

    def test_ci_uses_base_runner_when_candidate_gate_is_replaced(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.write('driver_lint/scripts/commit_gate.py', 'raise SystemExit(0)\n')
        self.write('driver_lint/scripts/run_ci.py', 'raise SystemExit(0)\n')
        self.write('driver_lint/src/driverlint/checks.py', 'raise SystemExit(0)\n')
        self.command('git', 'add', '.')
        self.commit()
        process = self.ci_result(base, '--output', 'driver_lint/reports/ci-gate.json', '--format', 'json')
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((self.root / 'driver_lint/reports/ci-gate.json').read_text())
        self.assertIn('SAFE001', {d['rule'] for d in report['diagnostics']})
        self.assertEqual(report['git_view']['base'], base)

    def test_ci_clean_commit_passes_with_trusted_runner_and_checkpatch(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        self.command('git', 'add', CORE)
        self.commit()
        process = self.ci_result(base)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('checkpatch', process.stdout)

    def test_server_ci_shell_propagates_lint_failure(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git', 'add', CORE)
        self.commit()
        job = self.root / 'driver_lint/integration/ci-gate.sh'
        process = subprocess.run(['sh', str(job)], cwd=self.root,
                                 env={**os.environ, 'DRIVER_LINT_REVIEW_BASE':base},
                                 capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertIn('SAFE001', process.stdout)

    def test_common_maintenance_requires_explicit_trusted_tool_revision(self):
        self.git_init()
        base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.settings['max_line_length'] += 1
        self.write_policy()
        self.command('git', 'add', 'driver_lint/config/policy.json')
        self.commit()
        approved = self.command('git', 'rev-parse', 'HEAD').strip()
        refused = self.ci_result(base)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn('Common policy changed', refused.stderr)
        accepted = self.ci_result(base, '--runner-ref', approved)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_existing_hook_is_chained_and_restored(self):
        self.git_init()
        path = self.write('.git/hooks/pre-commit', '#!/bin/sh\nexit 7\n')
        path.chmod(0o755)
        original = path.read_bytes()
        self.assertEqual(self.install_git().returncode, 0)
        self.assertEqual(self.install_git().returncode, 0)
        self.write(CORE, VALID.replace('return 0;', 'return 1;'))
        self.command('git', 'add', CORE)
        self.assertNotEqual(self.commit_result().returncode, 0)
        self.assertEqual(self.install_git('--remove').returncode, 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(os.access(path, os.X_OK))

    def test_repository_core_hooks_path_is_respected(self):
        self.git_init()
        self.command('git', 'config', 'core.hooksPath', '.project-hooks')
        self.assertEqual(self.install_git().returncode, 0)
        self.assertTrue((self.root / '.project-hooks/pre-commit').is_file())
        self.assertFalse((self.root / '.git/hooks/pre-commit').exists())

    def test_release_install_does_not_initialize_git(self):
        self.assertEqual(self.install_git().returncode, 2)
        self.assertFalse((self.root / '.git').exists())

    def test_codex_hook_handles_apply_patch_and_clean_stop_json(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        reduced = lambda *a, **k: run(*a, checkpatch_mode='off', notes=k.get('notes'))
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps({'hook_event_name':'PostToolUse', 'tool_name':'apply_patch',
                                                      'tool_input':{'command':'*** Begin Patch'}, 'cwd':'/different'}))), \
             patch('driverlint.hooks.run', side_effect=reduced), contextlib.redirect_stdout(output):
            self.assertEqual(hook_main(client='codex', root_override=self.root), 0)
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')
        self.write(CORE, VALID)
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO('{"hook_event_name":"Stop"}')), \
             patch('driverlint.hooks.run', side_effect=reduced), contextlib.redirect_stdout(output):
            self.assertEqual(hook_main(client='codex', root_override=self.root), 0)
        self.assertEqual(json.loads(output.getvalue()), {})

    def test_agent_install_preserves_unrelated_hooks_and_instructions(self):
        original = {'hooks':{'Stop':[{'hooks':[{'type':'command','command':'team-hook'}]}]}}
        self.write('.codex/hooks.json', json.dumps(original))
        self.write('AGENTS.md', 'Existing team instructions.\n')
        self.assertEqual(self.install_agents('--codex').returncode, 0)
        data = (self.root / '.codex/hooks.json').read_bytes()
        self.assertEqual(self.install_agents('--codex').returncode, 0)
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), data)
        self.assertIn('team-hook', data.decode())
        self.assertTrue((self.root / 'AGENTS.md').read_text().startswith('Existing team instructions.'))
        self.assertEqual(self.install_agents('--codex', '--remove').returncode, 0)
        self.assertEqual(json.loads((self.root / '.codex/hooks.json').read_text()), original)
        self.assertEqual((self.root / 'AGENTS.md').read_text(), 'Existing team instructions.\n')

    def test_opencode_conflict_does_not_partially_write_codex_settings(self):
        self.write('.opencode/plugins/driver-lint.js', 'team plugin')
        self.assertEqual(self.install_agents('--codex', '--opencode', 'v1').returncode, 2)
        self.assertFalse((self.root / '.codex/hooks.json').exists())
        self.assertFalse((self.root / 'AGENTS.md').exists())

    def test_removing_one_adapter_keeps_shared_rules_for_the_other(self):
        self.assertEqual(self.install_agents('--codex', '--opencode', 'v1').returncode, 0)
        self.assertEqual(self.install_agents('--codex', '--remove').returncode, 0)
        self.assertIn('driver-lint:start', (self.root / 'AGENTS.md').read_text())
        self.assertEqual(self.install_agents('--opencode', 'v1', '--remove').returncode, 0)
        self.assertFalse((self.root / 'AGENTS.md').exists())

    def test_malformed_codex_group_leaves_settings_unchanged(self):
        path = self.write('.codex/hooks.json', '{"hooks":{"Stop":[null]}}')
        original = path.read_bytes()
        self.assertEqual(self.install_agents('--codex').returncode, 2)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / 'AGENTS.md').exists())

    def test_opencode_v1_feedback_runs_actual_linter_on_apply_patch(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        plugin = self.write('driver-lint.mjs', (self.root / 'driver_lint/integration/opencode-v1.js').read_bytes())
        js = 'import {DriverLint} from ' + json.dumps(plugin.as_uri()) + ';\n' + \
             'const hook=await DriverLint({directory:' + json.dumps(str(self.root)) + '});\n' + \
             'const output={output:"Edit complete",content:[{type:"text",text:"Edit complete"}]}; await hook["tool.execute.after"]({tool:"apply_patch"},output); console.log(JSON.stringify(output));'
        process = subprocess.run(['node', '--input-type=module', '-e', js], capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('SAFE001', json.loads(process.stdout)['output'])
        self.assertIn('DRIVER LINT FAILED', json.loads(process.stdout)['output'])
        self.assertIn('SAFE001', json.loads(process.stdout)['content'][-1]['text'])

    def test_opencode_v2_callback_preserves_parts_and_error_feedback(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        # Only the SDK registration is stubbed; execute.after runs the real
        # adapter and Python linter. Installed OpenCode runtime is not tested.
        code = (self.root / 'driver_lint/integration/opencode-v2.js').read_text().replace(
            "import { Plugin } from '@opencode/plugin';", 'const Plugin = { define: x => x };')
        plugin = self.write('driver-lint-v2.mjs', code)
        js = 'import plugin from ' + json.dumps(plugin.as_uri()) + ';\n' + \
             'let after; await plugin.setup({location:{directory:' + json.dumps(str(self.root)) + '},tool:{hook:async(name,f)=>{after=f}}});\n' + \
             'const event={tool:"apply_patch",status:"completed",result:{content:[{type:"file",uri:"keep"}],metadata:{keep:true}}};\n' + \
             'await after(event); const failure={tool:"bash",status:"error",error:{message:"shell failed",type:"keep"}};\n' + \
             'await after(failure); console.log(JSON.stringify({event,failure}));'
        process = subprocess.run(['node', '--input-type=module', '-e', js], capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload['event']['result']['content'][0]['uri'], 'keep')
        self.assertTrue(payload['event']['result']['metadata']['keep'])
        self.assertIn('SAFE001', payload['event']['result']['content'][-1]['text'])
        self.assertIn('SAFE001', payload['failure']['error']['message'])
        self.assertEqual(payload['failure']['error']['type'], 'keep')
