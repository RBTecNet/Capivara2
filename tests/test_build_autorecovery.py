"""Falhas recuperáveis não encerram o build nem aprovam entregas inválidas."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from harness.agents import AgentError
from harness.artifacts import read_status
from harness.build_engine import BuildEngine
from harness.build_artifacts import BuildError
from harness.build_review import requirement_quotes
from harness.build_workspace import Workspace
from build_fixture import Builders
import test_build


class AutomaticBuildTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    def engine(self,agents,**kwargs):
        engine=BuildEngine(self.state,agents,**kwargs)
        async def recover(stage,message,attempt=1,task_id=None):
            engine.announce(stage,message,attempt=attempt,task_id=task_id)
            await asyncio.sleep(0)
        engine.recover=recover
        agents.delay=.001
        return engine

    async def test_both_citation_errors_repeat_then_repair_without_rewriting_code(self):
        class BadCitations(Builders):
            failures=0
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='fidelity' and data['task']['id']=='P1-T001' and self.failures<6:
                    self.failures+=1
                    return json.dumps({'approved':False,'summary':'Avaliação requer reparo.', 'issues':[{
                        'file':'dobro.py','observed':'Citação inválida do auditor.', 'required_change':'Preservar dobrar.',
                        'acceptance':'Atender à task original.',
                        'requirement_quote':data['task']['content']['objective'] if self.failures<=3 else 'Exigência que o usuário não pediu.',
                        'evidence_quote':'trecho que não existe' if self.failures<=3 else 'def dobrar(n):','missing':False}]})
                return result
        agents=BadCitations();events=[];engine=self.engine(agents,on_event=events.append)
        await engine.run()
        self.assertEqual(engine.contract_errors,6)
        self.assertEqual(len([d for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001']),1)
        audits=[d for _,op,d in agents.calls if op=='fidelity' and d['task']['id']=='P1-T001']
        self.assertEqual(len(audits),7)
        self.assertIn('trecho literal',audits[3]['contract_error']+json.dumps(audits[3].get('feedback',{}),ensure_ascii=False))
        self.assertIn('Cite literalmente',audits[6]['contract_error']+json.dumps(audits[6].get('feedback',{}),ensure_ascii=False))
        self.assertTrue(all(e.tasks_approved<4 for e in events if e.stage=='format_correction'))
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_reference_ids_bind_issues_to_real_source_and_file_quotes(self):
        class References(Builders):
            rejected=False
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='fidelity' and data['task']['id']=='P1-T001' and not self.rejected:
                    self.rejected=True;catalog=data['evidence_catalog']
                    q=next(iter(catalog['requirements']));e=next(k for k,v in catalog['evidence'].items() if v['file']=='dobro.py')
                    return json.dumps({'approved':False,'summary':'Explicitar a função.', 'issues':[{
                        'file':'dobro.py','observed':'Ajuste identificado no requisito referenciado.',
                        'required_change':'Registrar a intenção da função em comentário.',
                        'acceptance':'Comentário descreve a função, preservando seu resultado.',
                        'requirement_ref':q,'evidence_ref':e,'requirement_quote':'','evidence_quote':'','missing':False}]})
                if op=='write' and data['task']['id']=='P1-T001' and data['feedback']:
                    path=Path(kwargs['cwd'])/'dobro.py';path.write_text(path.read_text()+'# Função dobrar.\n')
                return result
        agents=References();engine=self.engine(agents);await engine.run()
        feedback=next(d['feedback'] for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001' and d['feedback'])
        issue=feedback['issues'][0]
        self.assertEqual(issue['evidence_quote'],'def dobrar(n):')
        self.assertIn(issue['requirement_quote'],requirement_quotes(next(d for _,op,d in agents.calls if op=='fidelity' and d['task']['id']=='P1-T001')))
        self.assertEqual(engine.contract_errors,0)
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_css_and_lockfile_are_reserved_and_reviewed_before_integration(self):
        class ExtraFiles(Builders):
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='write' and data['task']['id']=='P1-T001':
                    root=Path(kwargs['cwd']);(root/'src').mkdir(exist_ok=True)
                    (root/'src/app.css').write_text('.task { color: red; }\n')
                    (root/'package-lock.json').write_text('{"lockfileVersion":3}\n')
                return result
        agents=ExtraFiles();engine=self.engine(agents);await engine.run()
        self.assertEqual((self.root/'src/app.css').read_text(),'.task { color: red; }\n')
        self.assertEqual(json.loads((self.root/'package-lock.json').read_text())['lockfileVersion'],3)
        p=engine.progress['P1-T001']
        self.assertTrue(p['force_serial'])
        self.assertEqual(p['additional_reserved_files'],['package-lock.json','src/app.css'])
        audits=[d for _,op,d in agents.calls if op=='fidelity' and d['task']['id']=='P1-T001']
        self.assertTrue({'src/app.css','package-lock.json'}.issubset(audits[0]['delivered_files']))
        self.assertEqual(p['gates'],['completed']*4)

    async def test_parallel_scope_conflicts_drain_workers_and_preserve_both_contributions(self):
        (self.root/'src').mkdir();(self.root/'src/app.css').write_text('.base { color: black; }\n')
        (self.root/'package-lock.json').write_text('{"entries":[]}\n')
        self.state.build_mode='parallel';self.state.max_agents=2
        class Shared(Builders):
            async def generate(self,*args,**kwargs):
                request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1]);op=request['operation'];data=request['data']
                result=await super().generate(*args,**kwargs)
                if op=='write' and data['task']['id'] in ('P1-T001','P1-T002'):
                    root=Path(kwargs['cwd']);css=root/'src/app.css';lock=root/'package-lock.json';key=data['task']['id']
                    if (data.get('feedback') or {}).get('scope_recovery'):
                        css.write_text('\n'.join(dict.fromkeys(line for line in css.read_text().splitlines() if line.startswith('.')))+'\n')
                        entries={key}
                        for path in (root/'.harness/rebase').glob('*/package-lock.json/*'):
                            if path.is_file():entries.update(json.loads(path.read_text())['entries'])
                        lock.write_text(json.dumps({'entries':sorted(entries)})+'\n')
                    else:
                        css.write_text(css.read_text()+f'.{key} {{ color: red; }}\n')
                        value=json.loads(lock.read_text());value['entries'].append(key);lock.write_text(json.dumps(value)+'\n')
                return result
        agents=Shared();engine=self.engine(agents);agents.delay=.03
        reserve=engine.reserve_extra_files;drains=[]
        async def exclusive(key,workspace):
            drains.append(len(engine.active));await reserve(key,workspace)
        engine.reserve_extra_files=exclusive
        await engine.run()
        self.assertEqual(drains,[0,0])
        self.assertEqual(set(json.loads((self.root/'package-lock.json').read_text())['entries']),{'P1-T001','P1-T002'})
        css=(self.root/'src/app.css').read_text()
        self.assertIn('.P1-T001',css);self.assertIn('.P1-T002',css);self.assertNotIn('<<<<<<<',css)
        self.assertTrue(all(engine.progress[key]['completed'] for key in ('P1-T001','P1-T002')))

    async def test_transient_cli_failure_repairs_same_gate_without_redoing_writer(self):
        class Flaky(Builders):
            failures=0
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='fidelity' and data['task']['id']=='P1-T001' and self.failures<3:
                    self.failures+=1;raise AgentError('Falha transitória do CLI')
                return result
        agents=Flaky();engine=self.engine(agents);await engine.run()
        self.assertEqual(agents.failures,3)
        self.assertEqual(len([d for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001']),1)
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_continuous_invalid_auditor_remains_running_until_user_cancels_then_resumes(self):
        class Broken(Builders):
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                _,op,data=self.calls[-1]
                if op=='fidelity' and data['task']['id']=='P1-T001':return '{"approved":true,"issues":"invalid"}'
                return result
        agents=Broken();engine=self.engine(agents)
        task=asyncio.create_task(engine.run())
        async with asyncio.timeout(10):
            while engine.contract_errors<5:await asyncio.sleep(.005)
        self.assertFalse(task.done())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'running')
        self.assertFalse((self.root/'dobro.py').exists())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        resumed=Builders();await self.engine(resumed).run()
        self.assertFalse(any(op=='write' and d['task']['id']=='P1-T001' for _,op,d in resumed.calls))
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_recoverable_task_exception_requeues_only_current_task(self):
        original=Workspace.audit;errors=[0]
        def flaky(workspace,scope):
            if scope.task_id=='P1-T001' and errors[0]<3:
                errors[0]+=1;raise BuildError('Falha recuperável ao conferir a reserva')
            return original(workspace,scope)
        agents=Builders();engine=self.engine(agents)
        with patch.object(Workspace,'audit',flaky):await engine.run()
        self.assertEqual(errors[0],3)
        self.assertEqual(engine.progress['P1-T001']['recovery_count'],3)
        self.assertEqual(len([d for _,op,d in agents.calls if op=='write' and d['task']['phase_number']==0]),1)
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_abrupt_exit_during_mutating_audit_restores_source_and_resumes_g2(self):
        script='''import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from harness.build_engine import BuildEngine
from harness.domain import WizardState,AgentSelection
from build_fixture import Builders
root=Path(sys.argv[1]);agent=AgentSelection('codex','test','low')
state=WizardState(root,module='build',writer=agent,reviewer=agent)
class Crash(Builders):
 async def generate(self,*args,**kwargs):
  result=await super().generate(*args,**kwargs)
  request=json.loads(args[2].rsplit('REQUEST:',1)[1])
  if request['operation']=='fidelity' and request['data']['task']['id']=='P1-T001':
   (Path(kwargs['cwd'])/'dobro.py').write_text('def dobrar(n): return 0\\n')
   os._exit(75)
  return result
asyncio.run(BuildEngine(state,Crash()).run())
'''
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(self.root)],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,75,result.stderr.decode())
        agents=Builders();await self.engine(agents).run()
        self.assertFalse(any(op=='write' and d['task']['id']=='P1-T001' for _,op,d in agents.calls))
        self.assertIn('n * 2',(self.root/'dobro.py').read_text())
