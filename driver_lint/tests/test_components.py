"""Owner isolation across source, HW contracts, Git gates, CI and clients."""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

from test_enforcement import BundledWorkspace
from test_lint import CAMERA, CORE, API, REG, VALID, WORKSPACE
from driverlint.components import dispatch_hook, policy_path, stdin
from driverlint.gate import evaluate
from driverlint.model import LintError, Policy
from driverlint.selection import Snapshot


IS_CORE = CAMERA + '/is-core.c'


class ComponentTests(BundledWorkspace):
    def owner(self, component):
        return Policy(self.root, self.root / policy_path(component))

    def gate(self, component):
        return evaluate(self.root, common_path=policy_path(component), checkpatch_mode='off')[0]

    def test_production_and_kunit_ownership_are_disjoint(self):
        pablo, gdc = self.owner('pablo-is'), self.owner('gdc')
        for path in (CORE, API, REG, CAMERA+'/testing/kunit/post/pablo-hw-api-gdc-v9999-kunit-test.c'):
            self.assertTrue(gdc.in_scope(path), path)
            self.assertFalse(pablo.in_scope(path), path)
        for path in (IS_CORE, CAMERA+'/hardware/api/is-hw-api-yuvp-v13_0.c',
                     CAMERA+'/testing/kunit/post/pablo-vra-kunit-test.c'):
            self.assertTrue(pablo.in_scope(path), path)
            self.assertFalse(gdc.in_scope(path), path)

    def test_gdc_profile_has_no_pablo_family_or_yuvp(self):
        policy = self.owner('gdc')
        self.assertIsNone(policy.family_domain)
        self.assertEqual([d['name'] for d in policy.profile['version_domains']], ['gdc'])
        self.assertNotIn('PABLO_', json.dumps(policy.profile['build_contracts']))

    def test_gdc_source_error_does_not_fail_pablo_gate(self):
        self.git_init()
        self.write(CORE, VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git','add',CORE)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),0)
        self.assertEqual(self.gate('gdc').exit_code('error'),1)

    def test_pablo_source_error_does_not_fail_gdc_gate(self):
        self.write(IS_CORE,VALID)
        self.git_init()
        self.write(IS_CORE,VALID.replace('return 0;', 'BUG();\n\treturn 0;'))
        self.command('git','add',IS_CORE)
        self.assertEqual(self.gate('gdc').exit_code('error'),0)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),1)

    def test_gdc_selection_error_is_only_gdc_contract(self):
        self.git_init()
        path=CAMERA+'/post/gdc/Makefile'
        self.write(path,(self.root/path).read_text().replace('v1400.o','v1500.o'))
        self.command('git','add',path)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),0)
        self.assertIn('BUILD005',{d.rule for d in self.gate('gdc').diagnostics})

    def test_pablo_selection_error_is_only_pablo_contract(self):
        self.git_init()
        path=CAMERA+'/hardware/Makefile'
        self.write(path,(self.root/path).read_text().replace('yuvp-v13_0.o','yuvp-v19_0.o'))
        self.command('git','add',path)
        self.assertEqual(self.gate('gdc').exit_code('error'),0)
        self.assertIn('BUILD005',{d.rule for d in self.gate('pablo-is').diagnostics})

    def test_broken_other_owner_profile_is_not_loaded(self):
        self.git_init()
        path='driver_lint/pablo_is/config/profiles/s947b.json'
        self.write(path,'{broken profile')
        self.command('git','add',path)
        self.assertEqual(self.gate('gdc').exit_code('error'),0)
        with self.assertRaises(LintError):
            self.gate('pablo-is')

    def test_shared_fragment_checks_only_owned_requirements(self):
        self.git_init()
        path=self.project['fragments'][0]['path']
        original=(self.root/path).read_text()
        self.write(path,original.replace('CONFIG_CAMERA_PP_GDC_V14_0_0_OBJ=y','CONFIG_CAMERA_PP_GDC_V14_0_0_OBJ=n'))
        self.command('git','add',path)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),0)
        self.assertEqual(self.gate('gdc').exit_code('error'),1)
        self.write(path,original.replace('CONFIG_VIDEO_EXYNOS_PABLO_ISP=m','CONFIG_VIDEO_EXYNOS_PABLO_ISP=n'))
        self.command('git','add',path)
        self.assertEqual(self.gate('gdc').exit_code('error'),0)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),1)

    def test_shared_bazel_gdc_mapping_does_not_fail_pablo(self):
        self.git_init()
        path=str(Path(CAMERA).parent/'BUILD.bazel')
        self.write(path,(self.root/path).read_text().replace('pablo-hw-api-gdc-v1400.c','pablo-hw-api-gdc-v1500.c'))
        self.command('git','add',path)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),0)
        self.assertEqual(self.gate('gdc').exit_code('error'),1)

    def test_future_gdc_ip_version_does_not_change_pablo_family(self):
        self.git_init()
        path='driver_lint/gdc/config/profiles/s947b.json'
        text=(self.root/path).read_text().replace('1400','9900').replace('GDC_V14_0_0','GDC_V99_0_0')
        self.write(path,text)
        profile=json.loads(text)
        for fragment in profile['fragments']:
            target=self.root/fragment['path']
            self.write(fragment['path'],target.read_text().replace('GDC_V14_0_0','GDC_V99_0_0'))
        for target in (CAMERA+'/Kconfig',CAMERA+'/post/gdc/Makefile',str(Path(CAMERA).parent/'BUILD.bazel')):
            self.write(target,(self.root/target).read_text().replace('gdc-v1400','gdc-v9900').replace('GDC_V14_0_0','GDC_V99_0_0'))
        self.write(API.replace('1400','9900'),(self.root/API).read_text().replace('v1400','v9900'))
        self.write(REG.replace('1400','9900'),(self.root/REG).read_text())
        self.command('git','add','.')
        self.assertEqual(self.gate('gdc').exit_code('error'),0)
        self.assertEqual(self.gate('pablo-is').exit_code('error'),0)
        self.assertEqual(self.owner('pablo-is').family_domain['selected_versions'],['v14_0_0'])

    def test_independent_hooks_do_not_block_other_driver_edits(self):
        self.write(IS_CORE,VALID)
        self.git_init()
        self.write(CORE,VALID.replace('return 0;','BUG();\n\treturn 0;'))
        payload=json.dumps({'hook_event_name':'Stop'})
        for component,blocked in (('pablo-is',False),('gdc',True)):
            output=io.StringIO()
            with stdin(payload),contextlib.redirect_stdout(output):
                code=dispatch_hook(client='codex',root=self.root,component=component)
            self.assertEqual(code,0)
            result=json.loads(output.getvalue())
            self.assertEqual(result.get('decision')=='block',blocked)
            directory = 'pablo_is' if component == 'pablo-is' else 'gdc'
            command = f'python3 driver_lint/{directory}/lint.py scan'
            if blocked:
                self.assertIn(command, result['reason'])
            output = io.StringIO()
            with stdin(json.dumps({'hook_event_name':'SessionStart'})), contextlib.redirect_stdout(output):
                self.assertEqual(dispatch_hook(client='codex', root=self.root, component=component), 0)
            context = json.loads(output.getvalue())['hookSpecificOutput']['additionalContext']
            self.assertIn(command, context)
            self.assertIn(f'driver_lint/{directory}/CLAUDE.md', context)
            if component == 'gdc':
                self.assertNotIn('pablo-family=', context)

    def test_gdc_only_precommit_accepts_independent_pablo_violation(self):
        self.write(IS_CORE,VALID)
        self.git_init()
        self.assertEqual(self.install_git('--component','gdc').returncode,0)
        self.write(IS_CORE,VALID.replace('return 0;','BUG();\n\treturn 0;'))
        self.command('git','add',IS_CORE)
        process=self.commit_result()
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        # The Pablo IS required server job still owns this violation.
        self.assertIn('BUG()',self.command('git','show','HEAD:'+IS_CORE))

    def test_switching_owned_precommit_does_not_chain_a_previous_driver_gate(self):
        self.git_init()
        self.assertEqual(self.install_git('--component','pablo-is').returncode,0)
        self.assertEqual(self.install_git('--component','gdc').returncode,0)
        text=(self.root/'.git/hooks/pre-commit').read_text()
        self.assertIn('--component gdc',text)
        self.assertNotIn('--component pablo-is',text)
        self.assertFalse((self.root/'.git/hooks/pre-commit.driver-lint-original').exists())

    def test_codex_components_install_and_remove_independently(self):
        self.assertEqual(self.install_agents('--codex','--component','pablo-is').returncode,0)
        self.assertEqual(self.install_agents('--codex','--component','gdc').returncode,0)
        self.assertEqual(self.install_agents('--codex','--component','gdc','--remove').returncode,0)
        text=(self.root/'.codex/hooks.json').read_text()
        self.assertIn('--component pablo-is',text)
        self.assertNotIn('--component gdc',text)
        self.assertTrue((self.root/'AGENTS.md').exists())

    def test_claude_components_install_and_remove_independently(self):
        def install(*args):
            return subprocess.run([sys.executable,str(self.root/'driver_lint/scripts/install_claude.py'),
                '--root',str(self.root),*args],capture_output=True,text=True)
        self.assertEqual(install('--component','pablo-is').returncode,0)
        self.assertEqual(install('--component','gdc').returncode,0)
        self.assertEqual(install('--component','gdc','--remove').returncode,0)
        self.assertIn('--component pablo-is',(self.root/'.claude/settings.json').read_text())
        self.assertNotIn('--component gdc',(self.root/'.claude/settings.json').read_text())
        self.assertTrue((self.root/'.claude/rules/pablo-is-lint.md').is_file())
        self.assertFalse((self.root/'.claude/rules/gdc-lint.md').exists())

    def test_opencode_components_have_separate_files_and_ids(self):
        self.assertEqual(self.install_agents('--opencode','v2').returncode,0)
        pablo=self.root/'.opencode/plugins/driver-lint-pablo-is.js'
        gdc=self.root/'.opencode/plugins/driver-lint-gdc.js'
        self.assertIn("const component = 'pablo-is'",pablo.read_text())
        self.assertIn("const component = 'gdc'",gdc.read_text())
        self.assertEqual(self.install_agents('--opencode','v2','--component','gdc','--remove').returncode,0)
        self.assertTrue(pablo.is_file())
        self.assertFalse(gdc.exists())

    def test_ci_gdc_does_not_load_pablo_policy_but_rejects_gdc_error(self):
        self.git_init()
        base=self.command('git','rev-parse','HEAD').strip()
        bootstrap=self.write('trusted-bootstrap.py',self.command('git','show',base+':driver_lint/scripts/run_ci.py'))
        self.write('driver_lint/pablo_is/config/policy.json','bad pablo policy')
        self.write(CORE,VALID.replace('return 0;','BUG();\n\treturn 0;'))
        self.command('git','add','.')
        self.commit()
        p=subprocess.run([sys.executable,'-I',str(bootstrap),'--root',str(self.root),'--base',base,
            '--component','gdc','--format','json'],capture_output=True,text=True,timeout=90)
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        data=json.loads(p.stdout)
        self.assertEqual(data['component'],'gdc')
        self.assertIn('SAFE001',{d['rule'] for d in data['diagnostics']})

    def test_reference_split_uses_original_bytes_and_independent_scopes(self):
        self.snapshot()
        self.write(CORE,VALID.replace('return 0;','BUG();\n\treturn 0;'))
        p=subprocess.run([sys.executable,str(self.root/'driver_lint/scripts/split_reference.py'),
            '--root',str(self.root)],capture_output=True,text=True,timeout=90)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        gdc=self.owner('gdc')
        pablo=self.owner('pablo-is')
        snap=Snapshot(gdc,Path(gdc.data['snapshot']))
        self.assertEqual(snap.before(CORE),VALID.encode())
        self.assertIn(CORE,{c.path for c in snap.changes()})
        self.assertEqual(Snapshot(pablo,Path(pablo.data['snapshot'])).changes(),[])
