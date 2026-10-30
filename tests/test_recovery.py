"""Retomada real após falhas, cancelamentos e encerramento sem finally."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.artifacts import read_status
from harness.build_engine import BuildEngine
from harness.build_artifacts import BuildError, BuildStore
from harness.build_workspace import Workspace, manifest
from harness.build_models import TaskScope
from harness.plan_engine import PlanEngine
from harness.plan_contracts import PlanContractError
from harness.recovery import OperationJournal
from harness.init_ui import InitExecutionStep
from harness.app import HarnessApp
from build_fixture import Builders, prepared
from plan_fixture import PlanAgents, rejected
import test_plan
from test_wizard import test_catalog


class PlanRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def interrupted(self):
        agents=PlanAgents()
        def stop(operation,store):
            if operation=='outline_phase' and (store.path('phases/00/approved.json')).exists():
                raise RuntimeError('LLM indisponível')
        agents.on_generate=stop
        with self.assertRaisesRegex(RuntimeError,'LLM indisponível'):
            await PlanEngine(self.state,agents).run()
        return agents

    async def test_resume_reuses_approved_work_and_preserves_all_prior_logs(self):
        before=await self.interrupted()
        logs={p.name:p.read_bytes() for p in (self.root/'.harness/plan/log').glob('*.response.txt')}
        agents=PlanAgents();events=[]
        await PlanEngine(self.state,agents,on_event=events.append).run()
        tasks=[e['current_request']['task'] for _,e in agents.calls]
        self.assertNotIn('classify_work',tasks)
        self.assertNotIn('review_classification',tasks)
        self.assertFalse(any(e['current_request']['data'].get('task_id','').startswith('P0-') for _,e in agents.calls))
        for name,content in logs.items():self.assertEqual((self.root/'.harness/plan/log'/name).read_bytes(),content)
        self.assertEqual(events[-1].tasks_approved,7)
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')

    async def test_legacy_context_responses_migrate_without_regenerating_approved_tasks(self):
        await self.interrupted()
        for path in (self.root/'.harness/plan/control/operations').glob('*.json'):path.unlink()
        (self.root/'.harness/plan/control/sequence.json').unlink()
        agents=PlanAgents();await PlanEngine(self.state,agents).run()
        self.assertFalse(any(e['current_request']['task']=='write_task' and e['current_request']['data']['task_id'].startswith('P0-') for _,e in agents.calls))
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')

    async def test_issued_correction_is_applied_once_and_review_still_required(self):
        agents=PlanAgents();agents.task_reviews=[rejected('P0-T001','Explicite a stack aprovada.')]
        def stop(operation,store):
            if operation=='correct_task':raise RuntimeError('Queda antes da resposta do escritor')
        agents.on_generate=stop
        with self.assertRaises(RuntimeError):await PlanEngine(self.state,agents).run()
        agents=PlanAgents();await PlanEngine(self.state,agents).run()
        self.assertNotIn('correct_task',[e['current_request']['task'] for _,e in agents.calls])
        self.assertTrue(any(e['current_request']['task']=='review_task' and e['current_request']['data']['task']['id']=='P0-T001' for _,e in agents.calls))
        book=json.loads((self.root/'.harness/plan/control/corrections.json').read_text())
        self.assertEqual(len(book['contracts']),1)
        self.assertEqual(book['contracts'][0]['status'],'verified')

    async def test_completed_plan_does_not_reset_build_status(self):
        await PlanEngine(self.state,PlanAgents()).run()
        status=self.root/'.harness/status.json';data=json.loads(status.read_text())
        data['modules']['build']={'status':'running','task':'saved'};status.write_text(json.dumps(data))
        agents=PlanAgents();await PlanEngine(self.state,agents).run()
        self.assertEqual(read_status(self.root)['modules']['build'],data['modules']['build'])
        self.assertEqual(agents.calls,[])

    async def test_rejected_contract_can_be_replaced_on_retry_without_rewriting_draft(self):
        agents=PlanAgents();agents.task_reviews=[rejected('P0-T001')]
        agents.contract_reviews=[{'approved':False,'reason':'Contrato contradiz a origem.'}]*2
        validations=0
        def interrupt(operation,store):
            nonlocal validations
            if operation=='validate_correction':
                validations+=1
                if validations==3:raise asyncio.CancelledError()
        agents.on_generate=interrupt
        with self.assertRaises(asyncio.CancelledError):await PlanEngine(self.state,agents).run()
        agents=PlanAgents();await PlanEngine(self.state,agents).run()
        self.assertFalse(any(e['current_request']['task']=='write_task' and e['current_request']['data']['task_id']=='P0-T001' for _,e in agents.calls))
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')

    async def test_hard_process_exit_preserves_approved_phase(self):
        script='''import asyncio,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from harness.plan_engine import PlanEngine
from harness.domain import WizardState,AgentSelection
from plan_fixture import PlanAgents
root=Path(sys.argv[1]);agent=AgentSelection('codex','test','low')
state=WizardState(root,module='plan',writer=agent,reviewer=agent)
agents=PlanAgents()
def stop(operation,store):
 if operation=='outline_phase' and store.path('phases/00/approved.json').exists():os._exit(74)
agents.on_generate=stop
asyncio.run(PlanEngine(state,agents).run())
'''
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(self.root)],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,74,result.stderr.decode())
        agents=PlanAgents();await PlanEngine(self.state,agents).run()
        self.assertNotIn('classify_work',[e['current_request']['task'] for _,e in agents.calls])
        self.assertFalse(any(e['current_request']['task']=='write_task' and e['current_request']['data']['task_id'].startswith('P0-') for _,e in agents.calls))

    async def test_resume_preserves_structural_addition_and_generated_task_ids(self):
        from plan_fixture import approved_review
        agents=PlanAgents()
        agents.task_repairs={'P2-T002':[{**approved_review(),'approved':False,'coverage_complete':False,
            'additions':[{'phase_number':2,'after_task_id':'P2-T002','task':{
                'title':'Persistência das operações','scope':'Conectar operações à persistência local.',
                'source_quotes':['Gerenciar as tarefas.'],'coverage':[{'requirement_id':'P2-A001','excerpt':'tarefas'}]}}]}]}
        def stop(operation,store):
            request=agents.calls[-1][1]['current_request']
            if operation=='write_task' and request['data']['task_id']=='P2-T005':raise RuntimeError('Interrupção estrutural')
        agents.on_generate=stop
        with self.assertRaises(RuntimeError):await PlanEngine(self.state,agents).run()
        agents=PlanAgents();path=await PlanEngine(self.state,agents).run()
        final=json.loads(path.with_suffix('.json').read_text())
        self.assertEqual([t['id'] for t in final['phases'][2]['tasks']],['P2-T001','P2-T002','P2-T005','P2-T003','P2-T004'])
        self.assertFalse(any(e['current_request']['task']=='write_task' and e['current_request']['data']['task_id']=='P2-T002' for _,e in agents.calls))

    async def test_tui_survives_runtime_error_and_retry_resumes(self):
        agents=PlanAgents();attempt=[0]
        def stop(operation,store):
            if operation=='outline_phase' and store.path('phases/00/approved.json').exists() and not attempt[0]:
                attempt[0]+=1;raise RuntimeError('Erro inesperado recuperável')
        agents.on_generate=stop
        app=HarnessApp(launch_dir=self.root,catalog=test_catalog(),agents=agents)
        app.state=self.state
        async with app.run_test(size=(150,54)) as pilot:
            await app.push_screen(InitExecutionStep(module='plan'))
            async with asyncio.timeout(15):
                while app.screen.engine is None or app.screen.busy:await pilot.pause()
            self.assertTrue(app.screen.query_one('#init-retry').display)
            failed_calls=len(agents.calls)
            app.screen.query_one('#init-retry').focus()
            await pilot.pause()
            await pilot.click('#init-retry')
            async with asyncio.timeout(15):
                while app.screen.engine is None or app.screen.busy:await pilot.pause()
            self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')
            self.assertFalse(any(e['current_request']['task']=='classify_work' for _,e in agents.calls[failed_calls:]))


class BuildRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=await prepared(self.root)

    async def test_interrupted_store_initialization_and_legacy_log_ids_resume(self):
        root=self.root/'.harness/build';root.mkdir()
        (root/'owner.json').write_text(json.dumps({'owner':'capivara2','module':'build'}))
        (root/'control').mkdir()
        store=BuildStore(self.root);store.begin(self.state)
        try:
            store.write('log/reviewer-0098.response.txt','resposta anterior preservada')
            journal=OperationJournal(store,'test')
            self.assertEqual(journal.next_call(),99)
        finally:store.close()
        agents=Builders();await BuildEngine(self.state,agents).run()
        self.assertEqual((root/'log/reviewer-0098.response.txt').read_text(),'resposta anterior preservada')

    async def test_new_plan_preserves_previous_build_state_in_history(self):
        store=BuildStore(self.root);store.begin(self.state)
        data=store.read_json('state.json');data['plan_sha256']='plano anterior'
        store.json('state.json',data);store.write('log/previous.txt','não apagar');store.close()
        await BuildEngine(self.state,Builders()).run()
        history=list((self.root/'.harness/build-history').glob('*/log/previous.txt'))
        self.assertEqual(len(history),1);self.assertEqual(history[0].read_text(),'não apagar')

    async def test_each_gate_resumes_without_rewriting_accepted_code(self):
        for gate in (1,2,3):
            with self.subTest(gate=gate), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);state=await prepared(root)
                engine=BuildEngine(state,Builders());original=engine.gate
                def stop(key,number,status):
                    original(key,number,status)
                    if key=='P1-T001' and number==gate and status=='running':raise RuntimeError('interrompido')
                engine.gate=stop
                with self.assertRaisesRegex(RuntimeError,'interrompido'):await engine.run()
                saved=json.loads((root/'.harness/build/state.json').read_text())['tasks']['P1-T001']
                candidate=Path(saved['workspace']);self.assertTrue((candidate/'dobro.py').is_file())
                agents=Builders();new=BuildEngine(state,agents);await new.run()
                self.assertFalse(any(op=='write' and d['task']['id']=='P1-T001' for _,op,d in agents.calls))
                self.assertEqual(new.progress['P1-T001']['workspace'],str(candidate))
                self.assertEqual(new.progress['P1-T001']['gates'],['completed']*4)

    async def test_failed_writer_keeps_partial_files_and_reuses_same_workspace(self):
        class Fail(Builders):
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                if self.calls[-1][1]=='write' and self.calls[-1][2]['task']['id']=='P1-T001':
                    (Path(kwargs['cwd'])/'dobro.py').write_text('# parcial preservado\n')
                    raise RuntimeError('LLM caiu')
                return result
        with self.assertRaises(RuntimeError):await BuildEngine(self.state,Fail()).run()
        saved=json.loads((self.root/'.harness/build/state.json').read_text())['tasks']['P1-T001']
        class Continue(Builders):
            async def generate(self,*args,**kwargs):
                request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1])
                if request['operation']=='write' and request['data']['task']['id']=='P1-T001':
                    assert str(kwargs['cwd'])==saved['workspace']
                    assert (Path(kwargs['cwd'])/'dobro.py').read_text()=='# parcial preservado\n'
                return await super().generate(*args,**kwargs)
        await BuildEngine(self.state,Continue()).run()
        self.assertTrue((self.root/'dobro.py').is_file())

    async def test_g3_completed_check_is_not_repeated_after_cancellation(self):
        from harness.build_commands import run_command as actual
        class TwoChecks(Builders):
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='verification' and data['task']['id']=='P1-T001':
                    value=json.loads(result);value['checks']*=2;return json.dumps(value)
                return result
        async def stop(command,cwd,store,prefix,**kwargs):
            if 'P1-T001-g3' in prefix and prefix.endswith('-2'):raise asyncio.CancelledError()
            return await actual(command,cwd,store,prefix,**kwargs)
        with patch('harness.build_engine.run_command',side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):await BuildEngine(self.state,TwoChecks()).run()
        agents=Builders();executed=[]
        async def record(command,cwd,store,prefix,**kwargs):
            executed.append(prefix);return await actual(command,cwd,store,prefix,**kwargs)
        with patch('harness.build_engine.run_command',side_effect=record):await BuildEngine(self.state,agents).run()
        self.assertEqual(len([p for p in executed if 'P1-T001-g3' in p]),1)
        self.assertFalse(any(op in ('write','fidelity','verification') and d['task']['id']=='P1-T001' for _,op,d in agents.calls))

    async def test_hard_process_exit_restores_gate_and_releases_lock(self):
        script='''import asyncio,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from harness.build_engine import BuildEngine
from harness.domain import WizardState,AgentSelection
from build_fixture import Builders
root=Path(sys.argv[1]);agent=AgentSelection('codex','test','low')
state=WizardState(root,module='build',writer=agent,reviewer=agent)
engine=BuildEngine(state,Builders());old=engine.gate
def stop(key,number,status):
 old(key,number,status)
 if key=='P1-T001' and number==2 and status=='running':os._exit(73)
engine.gate=stop
asyncio.run(engine.run())
'''
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(self.root)],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,73,result.stderr.decode())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'running')
        agents=Builders();await BuildEngine(self.state,agents).run()
        self.assertFalse(any(op=='write' and d['task']['id']=='P1-T001' for _,op,d in agents.calls))
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_publication_interrupted_after_root_changes_is_recovered_without_llm(self):
        engine=BuildEngine(self.state,Builders())
        original=Workspace.publish
        def lost(workspace,scope,tree):
            result=original(workspace,scope,tree)
            if (workspace.root/'dobro.py').exists():raise RuntimeError('Caiu após integrar arquivos')
            return result
        with patch.object(Workspace,'publish',lost):
            with self.assertRaises(RuntimeError):await engine.run()
        self.assertTrue((self.root/'dobro.py').exists())
        agents=Builders();await BuildEngine(self.state,agents).run()
        self.assertFalse(any(op=='write' and d['task']['id']=='P1-T001' for _,op,d in agents.calls))
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_partial_publication_rolls_forward_but_preserves_conflicting_user_edit(self):
        store=BuildStore(self.root);store.begin(self.state)
        try:
            workspace=Workspace(self.root,store,'POWER')
            (workspace.root/'a.py').write_text('a=1\n');(workspace.root/'b.py').write_text('b=2\n')
            expected=manifest(workspace.root)
            scope=TaskScope(task_id='POWER',kind='code',writes=['a.py','b.py'],reads=[],depends_on=[])
            class PowerLoss(BaseException):pass
            from harness.build_workspace import os as workspace_os
            replace=workspace_os.replace
            def crash(source,target):
                if Path(target)==self.root/'b.py':raise PowerLoss()
                return replace(source,target)
            with patch('harness.build_workspace.os.replace',side_effect=crash):
                with self.assertRaises(PowerLoss):workspace.publish(scope,expected)
            self.assertTrue((self.root/'a.py').exists());self.assertFalse((self.root/'b.py').exists())
            restored=Workspace.resume(self.root,store,str(workspace.root))
            (self.root/'a.py').write_text('edicao do usuario\n')
            with self.assertRaisesRegex(BuildError,'Conflito'):restored.recover_publication(expected)
            self.assertEqual((self.root/'a.py').read_text(),'edicao do usuario\n')
            (self.root/'a.py').write_text('a=1\n')
            restored.recover_publication(expected)
            self.assertEqual((self.root/'b.py').read_text(),'b=2\n')
        finally:store.close()
