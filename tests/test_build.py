import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import OptionList, Input, Static

from harness.agents import CLIAgents
from harness.app import HarnessApp, ConfirmStep, BuildAgentsStep
from harness.artifacts import read_status
from harness.build_agents import build_command
from harness.build_artifacts import BuildError, BuildStore, DependenciesBlocked, load_plan
from harness.build_engine import BuildEngine
from harness.build_models import TaskScope
from harness.build_progress import BuildProgress
from harness.build_workspace import Workspace, conflicts, manifest
from harness.init_ui import InitExecutionStep
from build_fixture import Builders, prepared
from test_wizard import test_catalog


class BuildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=await prepared(self.root)
        self.initial=(self.root/'.harness/prompts/final.md').read_bytes()
        self.plan=(self.root/'.harness/plan/final.json').read_bytes()

    async def test_real_functional_commands_and_outputs_are_delivered_in_project_root(self):
        agents=Builders();events=[]
        engine=BuildEngine(self.state,agents,on_event=events.append)
        result=await engine.run()
        self.assertTrue((self.root/'dobro.py').is_file())
        self.assertTrue((self.root/'triplo.py').is_file())
        self.assertIn('Instalação',(self.root/'README.md').read_text())
        self.assertTrue(result.is_file())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')
        self.assertEqual((self.root/'.harness/prompts/final.md').read_bytes(),self.initial)
        self.assertEqual((self.root/'.harness/plan/final.json').read_bytes(),self.plan)
        self.assertTrue(all(p['gates']==['completed']*4 and p['completed'] for p in engine.progress.values()))
        outcomes=list((self.root/'.harness/build/log').glob('*g3*.result.json'))
        self.assertEqual(len(outcomes),2)
        self.assertTrue(all(json.loads(p.read_text())['exit_code']==0 for p in outcomes))
        self.assertEqual(events[-1].tasks_approved,4)
        self.assertEqual(events[-1].phases_approved,2)
        self.assertTrue(all(e.module=='build' for e in events))

    async def test_parallel_disjoint_tasks_and_shared_file_reservations(self):
        self.state.build_mode='parallel';self.state.max_agents=2
        agents=Builders();agents.delay=.08
        await BuildEngine(self.state,agents).run()
        self.assertEqual(agents.peak,2)
        # Rebuild in another project so resume does not skip completed tasks.
        with tempfile.TemporaryDirectory() as directory:
            state=await prepared(Path(directory));state.build_mode='parallel';state.max_agents=3
            agents=Builders();agents.shared=True;agents.delay=.03
            await BuildEngine(state,agents).run()
            self.assertEqual(agents.peak,1)

    async def test_g1_missing_code_returns_to_g0_without_false_approval(self):
        agents=Builders();agents.g1_missing=True;events=[]
        await BuildEngine(self.state,agents,on_event=events.append).run()
        calls=[d for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001']
        self.assertEqual(len(calls),2)
        self.assertIn('files_missing',calls[1]['feedback'])
        states=[i.gates for e in events for i in e.build_items if i.id=='P1-T001']
        self.assertIn(('completed','running','waiting','waiting'),states)
        self.assertIn(('running','waiting','waiting','waiting'),states)

    async def test_g2_evidence_and_single_specific_return(self):
        class Fixing( Builders ):
            async def generate(self,*args,**kwargs):
                response=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='write' and data['task']['id']=='P1-T001' and data['feedback']:
                    path=Path(kwargs['cwd'])/'dobro.py';path.write_text(path.read_text()+'\n# Função dobrar documentada.\n')
                return response
        agents=Fixing();agents.g2_reject=True;events=[]
        await BuildEngine(self.state,agents,on_event=events.append).run()
        states=[i.gates for e in events for i in e.build_items if i.id=='P1-T001']
        self.assertTrue(any(s[2]=='rejected' for s in states))
        red=next(i for i,s in enumerate(states) if s[2]=='rejected')
        self.assertIn(('running','waiting','waiting','waiting'),states[red+1:])
        feedback=next(d['feedback'] for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001' and d['feedback'])
        self.assertIn('acceptance',feedback['issues'][0])

    async def test_g3_fails_on_actual_wrong_code_freezes_tests_and_restarts_gates(self):
        agents=Builders();agents.g3_bad=True;events=[]
        engine=BuildEngine(self.state,agents,on_event=events.append)
        await engine.run()
        checks=[d for _,op,d in agents.calls if op=='verification' and d['task']['id']=='P1-T001']
        self.assertEqual(len(checks),1)
        states=[i.gates for e in events for i in e.build_items if i.id=='P1-T001']
        rejected=next(i for i,s in enumerate(states) if s[3]=='rejected')
        self.assertIn(('running','waiting','waiting','waiting'),states[rejected+1:])
        results=[json.loads(p.read_text())['exit_code'] for p in (self.root/'.harness/build/log').glob('*P1-T001-g3*.result.json')]
        self.assertIn(1,results);self.assertIn(0,results)
        self.assertEqual(engine.corrections,1)

    async def test_automatic_dependency_install_and_manual_block_recovery(self):
        marker=self.root/'dependency.ready'
        dependency={'name':'pacote-de-exemplo','purpose':'Dependência controlada.',
            'check':{'argv':[sys.executable,'-c',f'from pathlib import Path; raise SystemExit(0 if Path({str(marker)!r}).exists() else 1)'],
                     'purpose':'Checar pacote.','timeout':10,'env':[]},
            'install':[{'argv':[sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).write_text("ok")'],
                        'purpose':'Instalar pacote controlado.','timeout':10,'env':[]}],
            'manual_instructions':'Crie o marcador indicado e rode build novamente.'}
        agents=Builders();agents.dependencies=[dependency]
        await BuildEngine(self.state,agents).run()
        self.assertTrue(marker.exists())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=await prepared(root)
            bad=json.loads(json.dumps(dependency));bad['name']='pacote-sem-permissão'
            bad['check']['argv']=[sys.executable,'-c','raise SystemExit(1)']
            bad['install'][0]['argv']=[sys.executable,'-c','raise SystemExit(13)']
            agents=Builders();agents.dependencies=[bad]
            with self.assertRaisesRegex(DependenciesBlocked,'pacote-sem-permissão'):
                await BuildEngine(state,agents).run()
            self.assertEqual(read_status(root)['modules']['build']['status'],'blocked')
            script=root/'.harness/build/install-manual.sh'
            self.assertTrue(script.is_file());self.assertIn('pacote-sem-permissão',script.read_text())
            self.assertFalse((root/'dobro.py').exists())
            self.assertFalse(any(op=='write' for _,op,_ in agents.calls))

    async def test_scope_violation_never_publishes_candidate_changes(self):
        agents=Builders();agents.outside=True
        with self.assertRaisesRegex(BuildError,'fora da reserva'):
            await BuildEngine(self.state,agents).run()
        self.assertFalse((self.root/'unexpected.py').exists())
        self.assertFalse((self.root/'dobro.py').exists())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'failed')

    async def test_resume_skips_completed_tasks_and_external_changes_invalidate_scope(self):
        await BuildEngine(self.state,Builders()).run()
        agents=Builders();await BuildEngine(self.state,agents).run()
        self.assertFalse(any(op=='write' for _,op,_ in agents.calls))
        (self.root/'dobro.py').write_text('def dobrar(n):\n    return 0\n')
        agents=Builders();await BuildEngine(self.state,agents).run()
        ids=[d['task']['id'] for _,op,d in agents.calls if op=='write']
        self.assertIn('P1-T001',ids);self.assertIn('BUILD-README',ids)
        self.assertNotIn('P1-T002',ids)

    async def test_cancel_never_marks_incomplete_build_completed(self):
        agents=Builders();agents.cancel=True
        with self.assertRaises(asyncio.CancelledError):
            await BuildEngine(self.state,agents).run()
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'cancelled')
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')
        await BuildEngine(self.state,Builders()).run()
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_invalid_plan_and_shared_lock_prevent_execution(self):
        active=BuildEngine(self.state,Builders());active.store.begin(self.state)
        try:
            with self.assertRaisesRegex(BuildError,'módulo em execução'):
                await BuildEngine(self.state,Builders()).run()
        finally:active.store.close()
        (self.root/'.harness/plan/final.json').write_text('{}')
        with self.assertRaises(BuildError):await BuildEngine(self.state,Builders()).run()

    async def test_wizard_parallel_and_actual_dashboard(self):
        app=HarnessApp(launch_dir=self.root,catalog=test_catalog(),build_agents=Builders())
        async with app.run_test(size=(150,58)) as pilot:
            await pilot.press('enter')
            self.assertEqual(app.screen.query_one(OptionList).get_option_at_index(app.screen.query_one(OptionList).highlighted).id,'build')
            await pilot.press('enter','enter','enter','enter')
            self.assertEqual(app.screen.kind,'build-mode')
            await pilot.press('down','enter')
            self.assertIsInstance(app.screen,BuildAgentsStep)
            app.screen.query_one(Input).value='2'
            await pilot.press('enter','enter')
            self.assertIsInstance(app.screen,ConfirmStep)
            self.assertEqual(app.state.max_agents,2)
            await pilot.press('enter')
            async with asyncio.timeout(15):
                while not isinstance(app.screen,InitExecutionStep) or app.screen.busy or not app.screen.final_path:
                    await pilot.pause()
            self.assertEqual(app.screen.module,'build')
            self.assertTrue(app.screen.query(BuildProgress))
            self.assertIn('4/4',str(app.screen.query_one('#metric-tasks .dash-field-value',Static).render()))
            for width in (100,80,60):
                await pilot.resize_terminal(width,30);await pilot.pause()
                dashboard=app.screen.query_one('#init-dashboard')
                self.assertEqual(dashboard.max_scroll_x,0)


class BuildAdapterTests(unittest.TestCase):
    def test_native_permission_flags_are_specific_to_provider(self):
        from harness.domain import AgentSelection
        for provider,flag in (('codex','--dangerously-bypass-approvals-and-sandbox'),('claude','--dangerously-skip-permissions'),
                              ('opencode','--auto'),('agy','--dangerously-skip-permissions')):
            selection=AgentSelection(provider,'default','default')
            command,mode=build_command(selection,{},Path('/tmp/out'),Path('/tmp/schema'),Path('/tmp/work'))
            self.assertIn(flag,command)
            self.assertNotIn('--continue',command)
            self.assertNotIn('--session',command)
            if provider=='codex':self.assertNotIn('read-only',command)
        self.assertEqual(mode,'agy')

    def test_read_write_conflicts_include_parent_paths_and_unknown_scopes(self):
        def scope(writes,reads=()):return TaskScope(task_id='T',kind='code',writes=writes,reads=list(reads),depends_on=[])
        self.assertTrue(conflicts(scope(['src/**']),scope(['src/a.py'])))
        self.assertTrue(conflicts(scope(['app.py']),scope(['view.py'],['app.py'])))
        self.assertTrue(conflicts(scope(['**']),scope(['a.py'])))
        self.assertFalse(conflicts(scope(['a/**'],['common.py']),scope(['b/**'],['common.py'])))

class BuildGateIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=BuildTests.asyncSetUp

    async def test_publish_rejects_code_changed_after_g3_and_preserves_external_edits(self):
        store=BuildStore(self.root);store.begin(self.state)
        try:
            workspace=Workspace(self.root,store,'CHECK')
            path=workspace.root/'dobro.py';path.write_text('def dobrar(n): return n*2\n')
            tested=manifest(workspace.root)
            path.write_text('def dobrar(n): return 0\n')
            scope=TaskScope(task_id='CHECK',kind='code',writes=['dobro.py'],reads=[],depends_on=[])
            with self.assertRaisesRegex(BuildError,'mudou após o G3'):
                workspace.publish(scope,tested)
            self.assertFalse((self.root/'dobro.py').exists())
            path.write_text('def dobrar(n): return n*2\n')
            (self.root/'dobro.py').write_text('conteúdo editado pelo usuário\n')
            with self.assertRaisesRegex(BuildError,'base da task mudou'):
                workspace.publish(scope,manifest(workspace.root))
            self.assertEqual((self.root/'dobro.py').read_text(),'conteúdo editado pelo usuário\n')
        finally:store.close()
    async def test_wrong_stdout_fails_even_when_process_exits_zero(self):
        class StdoutCheck(Builders):
            async def generate(self,*args,**kwargs):
                response=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='verification' and data['scope']['kind']=='code':
                    filename=data['delivered_files'][0];module=Path(filename).stem
                    function='dobrar' if module=='dobro' else 'triplicar';expected='6' if module=='dobro' else '9'
                    return json.dumps({'summary':'Conferir o resultado real.','dependencies':[], 'checks':[{
                        'command':{'argv':[sys.executable,'-c',f'import {module}; print({module}.{function}(3))'],
                            'purpose':'Resultado funcional.','timeout':10,'env':[]},'target_files':[filename],
                        'requirement_quote':data['task']['content']['objective'],'assertion_file':None,'expected_stdout':expected}]})
                return response
        agents=StdoutCheck();agents.g3_bad=True
        await BuildEngine(self.state,agents).run()
        feedback=next(d['feedback'] for _,op,d in agents.calls if op=='write' and d['feedback'])
        failure=feedback['functional_failures'][0]
        self.assertEqual(failure['exit_code'],0)
        self.assertEqual(failure['expected_stdout'],'6')
        self.assertIn('0',failure['observed'])

    async def test_writer_cannot_relax_frozen_test_to_pass_wrong_code(self):
        class Tamper(Builders):
            async def generate(self,*args,**kwargs):
                response=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='write' and data['task']['id']=='P1-T001' and data['feedback']:
                    path=Path(kwargs['cwd'])
                    (path/'dobro.py').write_text('def dobrar(n):\n    return 0\n# mudou, mas continua errado\n')
                    (path/'.harness/checks/check.py').write_text('assert True\n')
                return response
        agents=Tamper();agents.g3_bad=True
        with self.assertRaisesRegex(BuildError,'mesmo conflito'):
            await BuildEngine(self.state,agents).run()
        self.assertFalse((self.root/'dobro.py').exists())

    async def test_validator_cannot_change_application_source(self):
        class Mutator(Builders):
            async def generate(self,*args,**kwargs):
                response=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='fidelity' and data['task']['id']=='P1-T001':
                    (Path(kwargs['cwd'])/'dobro.py').write_text('def dobrar(n):\n    return 2\n')
                return response
        with self.assertRaisesRegex(BuildError,'G2 alterou a fonte'):
            await BuildEngine(self.state,Mutator()).run()
        self.assertFalse((self.root/'dobro.py').exists())

    async def test_missing_late_test_dependency_blocks_without_blame_to_writer(self):
        class MissingTestTool(Builders):
            async def generate(self,*args,**kwargs):
                response=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='verification' and data['scope']['kind']=='code':
                    result=json.loads(response)
                    result['dependencies']=[{'name':'ferramenta-g3-indisponível','purpose':'Executar o check específico.',
                        'check':{'argv':[sys.executable,'-c','raise SystemExit(1)'],'purpose':'Checar.','timeout':5,'env':[]},
                        'install':[],'manual_instructions':'Instalar ferramenta-g3-indisponível manualmente.'}]
                    return json.dumps(result)
                return response
        agents=MissingTestTool()
        with self.assertRaisesRegex(DependenciesBlocked,'ferramenta-g3-indisponível'):
            await BuildEngine(self.state,agents).run()
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'blocked')
        self.assertFalse((self.root/'dobro.py').exists())
        self.assertTrue((self.root/'.harness/build/manual-install.md').is_file())
        self.assertFalse((self.root/'.harness/build/install-manual.sh').exists())
