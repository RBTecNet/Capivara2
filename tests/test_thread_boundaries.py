"""Um par por task. Fases só consolidam certificados; modelos não recebem lotes."""
import json
from pathlib import Path
import tempfile
import unittest

from harness.plan_engine import PlanEngine
from harness.plan_artifacts import load_init
from harness.plan_contracts import TaskAssessment, PlanContractError
from harness.plan_certificates import write_task_approval
from harness.plan_control import stub, version
from harness.plan_models import PlannedPhase, TaskSeed, Requirement
from harness.build_engine import BuildEngine
from harness.build_models import TaskScope
from harness.task_view import TaskView
from plan_fixture import PlanAgents
from build_fixture import Builders
import test_plan
import test_build


class ThreadPlanTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_no_agent_receives_phase_results_or_global_completion(self):
        agents=PlanAgents();engine=PlanEngine(self.state,agents);await engine.run()
        for _,envelope in agents.calls:
            request=envelope['current_request'];data=request['data']
            self.assertNotIn(request['task'],('review_phase','review_plan','validate_completion'))
            if request['task'] in ('review_task','correct_task','validate_correction','repair_correction','reassess_task'):
                self.assertNotIn('phase',data);self.assertNotIn('phases',data)
                if 'task' in data:self.assertEqual(len([data['task']]),1)
        certificate=json.loads((self.root/'.harness/plan/control/approval.json').read_text())
        self.assertEqual(certificate['completion_mode'],'task-certificates')
        self.assertEqual(len(certificate['task_certificates']),len(engine.approved_tasks))

    async def test_hundred_completed_threads_consolidate_without_model_call(self):
        class NoCalls:
            async def generate(self,*args,**kwargs):raise AssertionError('Consolidação não chama agentes.')
        engine=PlanEngine(self.state,NoCalls());engine.source=engine.store.begin(self.state)
        self.addCleanup(engine.store.close)
        engine.prepare_origins();tasks=[]
        approved=TaskAssessment(approved=True,matches_source=True,coverage_complete=True,tasks_are_small=True,
            no_test_strategy=True,summary='Unidade aprovada pelo seu validador.',corrections=[],replan=None)
        for i in range(1,101):
            identifier=f'P1-A{i:03d}';text=f'Implementar a unidade independente {i}.'
            engine.requirements[identifier]=Requirement(id=identifier,phase_number=1,kind='activity',text=text)
            task=stub(1,TaskSeed(title=text,scope=text,source_quotes=[text],
                coverage=[{'requirement_id':identifier,'excerpt':text}]),f'P1-T{i:03d}')
            tasks.append(task);engine.approved_tasks.add(task.id);engine.approved_versions[task.id]=version(task)
            write_task_approval(engine,task,approved)
        phase=PlannedPhase(number=1,title='Unidades',goal='Unidades independentes.',depends_on=[],tasks=tasks)
        engine.phases=[phase];await engine.validate_phase(phase)
        self.assertIn(1,engine.approved_phases)
        with self.assertRaises(PlanContractError):
            await engine.call('reviewer','review_phase',{},TaskAssessment)


class TaskViewTests(unittest.TestCase):
    def test_only_delivery_declared_dependencies_and_imports_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'unit.py').write_text('from helper import value\ndef unit(): return value\n')
            (root/'helper.py').write_text('value=3\n')
            (root/'other_thread.py').write_text('SECRET_OTHER_THREAD_SOURCE\n')
            (root/'contract.txt').write_text('Contrato necessário.')
            (root/'.harness/baseline').mkdir(parents=True)
            (root/'.harness/baseline/unit.py').write_text('Versão de base.')
            (root/'.harness/baseline/removed.py').write_text('Conteúdo removido por esta task.')
            scope=TaskScope(task_id='P4-T019',kind='code',writes=['unit.py'],reads=['contract.txt'],depends_on=[])
            view=TaskView(root,{'delivered_files':['unit.py'],'changed_files':['removed.py']},scope)
            try:
                self.assertTrue((view.root/'unit.py').exists())
                self.assertTrue((view.root/'helper.py').exists())
                self.assertTrue((view.root/'contract.txt').exists())
                self.assertFalse((view.root/'other_thread.py').exists())
                self.assertEqual((view.root/'.harness/baseline/unit.py').read_text(),'Versão de base.')
                self.assertTrue((view.root/'.harness/baseline/removed.py').exists())
                self.assertFalse((view.root/'removed.py').exists())
                self.assertFalse(view.root.is_relative_to(root))
            finally:view.close()


class ThreadBuildTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    async def test_parallel_workers_have_unit_views_and_individual_blueprints(self):
        class Isolated(Builders):
            async def generate(inner,*args,**kwargs):
                request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1])
                operation,data=request['operation'],request['data']
                result=await super().generate(*args,**kwargs)
                if operation=='blueprint':
                    self.assertEqual(sum(len(p['tasks']) for p in data['plan']['phases']),1)
                if operation in ('fidelity','verification'):
                    root=Path(kwargs['cwd'])
                    self.assertFalse(root.is_relative_to(self.root))
                    self.assertFalse((root/'unrelated.py').exists())
                    if data['scope']['kind']=='code':
                        own='dobro.py' if data['task']['id']=='P1-T001' else 'triplo.py'
                        other='triplo.py' if own=='dobro.py' else 'dobro.py'
                        self.assertTrue((root/own).exists())
                        self.assertFalse((root/other).exists())
                return result
        (self.root/'unrelated.py').write_text('Outro contexto.\n')
        self.state.build_mode='parallel';self.state.max_agents=5
        agents=Isolated();engine=BuildEngine(self.state,agents);await engine.run()
        self.assertTrue(all(p['completed'] for p in engine.progress.values()))
        self.assertGreaterEqual(agents.peak,2)
