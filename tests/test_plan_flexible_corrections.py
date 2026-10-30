"""Regressões do harness: correções independentes e IDs estruturais em projetos quaisquer."""
from copy import deepcopy
import asyncio
import json
import unittest
from unittest.mock import patch as mock_patch

from harness.plan_acceptance import effective_conditions
from harness.plan_adapter import normalize_review
from harness.plan_contracts import TaskAssessment, ContentCorrection, correction_id
from harness.plan_corrections import CorrectionBook
from harness.plan_control import version
from harness.plan_engine import PlanEngine
from harness.plan_models import PlannedPhase
from harness.plan_references import successors, rebind_tasks, rewrite
from plan_fixture import PlanAgents, rejected
from test_plan_adapter_actions import sample
import test_plan


def patch(task, requirements, find, replace):
    raw,_=normalize_review({'summary':'Correção objetiva.','findings':[{
        'task_id':task.id,'category':'fidelity','reason':'Detalhe necessário.', 'instruction':'Ajustar o trecho indicado.',
        'requirement_ids':list(requirements),'edits':[{'field':'objective','find':find,'replace':replace}]}],
        'replan':None},TaskAssessment,{'task':task.model_dump(),'base_version':version(task)},requirements)
    return TaskAssessment.model_validate(raw).corrections[0]


class FlexibleCorrectionsTests(unittest.TestCase):
    def test_distinct_corrections_in_same_field_and_requirement_are_allowed(self):
        task,requirements,_=sample(5,17);task.content.objective='Criar a função.'
        book=CorrectionBook()
        first=patch(task,requirements,'função','função que dobra valores')
        identifier,target=book.issue(task,first,requirements);task.content=target;book.applied(identifier,task)
        second=patch(task,requirements,'valores','valores inteiros')
        next_id,target=book.issue(task,second,requirements)
        self.assertNotEqual(identifier,next_id)
        task.content=target;book.applied(next_id,task);book.verify(task,approved=True)
        self.assertEqual(task.content.objective,'Criar a função que dobra valores inteiros.')
        self.assertEqual(len(book.records),2)

    def test_old_whole_field_acceptance_does_not_freeze_unrelated_text(self):
        task,requirements,_=sample();task.content.objective='Criar a função. Nota: detalhe independente.'
        spec=patch(task,requirements,'função','função que dobra valores')
        after=spec.changes[0].after
        legacy=spec.model_dump();legacy['acceptance']=[{'field':'objective','operator':'contains','value':after}]
        spec=ContentCorrection.model_validate(legacy)
        conditions=effective_conditions(spec)
        self.assertTrue(conditions);self.assertNotIn(after,[c['value'] for c in conditions])
        book=CorrectionBook();identifier,target=book.issue(task,spec,requirements)
        task.content=target;book.applied(identifier,task)
        other=patch(task,requirements,'detalhe independente','detalhe atualizado')
        self.assertIsNotNone(book.check(task,other,requirements))

    def test_retired_owner_reference_is_rebound_with_acceptance_and_history(self):
        task,requirements,_=sample(9,4);task.content.objective='Consumir o recurso.'
        old='P9-T021';new='P9-T033'
        spec=patch(task,requirements,'recurso','recurso fornecido por '+old)
        book=CorrectionBook();identifier,target=book.issue(task,spec,requirements)
        task.content=target;book.applied(identifier,task);book.verify(task,approved=True)
        historical=deepcopy(book.records[identifier]['contract'])
        phase=PlannedPhase(number=9,title='Fase',goal='Objetivo',depends_on=[],tasks=[task])
        rebind_tasks([phase],{old:new});book.rebind({old:new});book.verify(task)
        self.assertIn(new,task.content.objective)
        self.assertEqual(book.records[identifier]['contract'],historical)
        self.assertTrue(book.records[identifier]['reference_history'])
        self.assertEqual(rewrite(old+' '+old+'0',{old:new}),new+' '+old+'0')

    def test_successor_requires_unique_ownership_of_all_retired_slices(self):
        old,_,_=sample(3,2);new=old.model_copy(deep=True);new.id='P3-T019'
        phase=lambda tasks:PlannedPhase(number=3,title='Fase',goal='Objetivo',depends_on=[],tasks=tasks)
        self.assertEqual(successors([phase([old])],[phase([new])],{old.id}),{old.id:new.id})
        another=new.model_copy(deep=True);another.id='P3-T025'
        self.assertEqual(successors([phase([old])],[phase([new,another])],{old.id}),{})
        new.seed.coverage[0].excerpt='Outra responsabilidade.'
        self.assertEqual(successors([phase([old])],[phase([new])],{old.id}),{})


class FlexibleCorrectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_different_saved_target_with_same_legacy_id_is_preserved(self):
        class Collision(PlanAgents):
            old_path=None;old_bytes=None
            async def generate(inner,*args,**kwargs):
                output=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request'];data=request['data']
                if request['task']=='validate_correction' and inner.old_path is None:
                    spec=ContentCorrection.model_validate(data['contract']);store=args[4]
                    relative=f'control/applied-corrections/{correction_id(spec)}.json'
                    target={**data['expected_content'],'title':'Outro ajuste aprovado anteriormente.'}
                    store.json(relative,{'status':'applied','base_version':spec.base_version,'target':target})
                    inner.old_path=store.path(relative);inner.old_bytes=inner.old_path.read_bytes()
                return output
        agents=Collision();agents.task_reviews=[rejected('P0-T001')]
        await PlanEngine(self.state,agents).run()
        self.assertEqual(agents.old_path.read_bytes(),agents.old_bytes)
        self.assertGreaterEqual(len(list(agents.old_path.parent.glob(agents.old_path.stem+'*.json'))),2)

    async def test_resume_reuses_approved_versions_when_only_acceptance_representation_changes(self):
        previous_context=CorrectionBook.context
        def whole_fields(book, identifiers):
            result=previous_context(book,identifiers)
            for entry in result:
                changes=book.records[entry['id']]['contract']['changes']
                entry['acceptance']=[{'field':c['field'],'operator':'contains',
                    'value':'\n'.join(c['after']) if isinstance(c['after'],list) else c['after']} for c in changes]
            return result
        first=PlanAgents();first.task_reviews=[rejected('P0-T001')]
        def stop(operation,store):
            if operation=='outline_phase' and store.path('phases/00/approved.json').exists():raise asyncio.CancelledError()
        first.on_generate=stop
        with mock_patch.object(CorrectionBook,'context',whole_fields):
            with self.assertRaises(asyncio.CancelledError):await PlanEngine(self.state,first).run()
        second=PlanAgents();await PlanEngine(self.state,second).run()
        for _,envelope in second.calls:
            request=envelope['current_request'];data=request['data']
            self.assertFalse(request['task']=='review_phase' and data['phase']['number']==0)
            self.assertFalse(request['task']=='review_task' and data['task']['phase_number']==0)
            self.assertFalse(request['task'] in ('write_task','correct_task') and data['task_id'].startswith('P0-'))

    async def test_structural_change_updates_references_and_reviews_only_changed_documents(self):
        class Replacement(PlanAgents):
            replaced=False
            async def generate(inner,*args,**kwargs):
                output=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request'];data=request['data']
                if request['task']=='write_task' and data['task_id']=='P0-T001':
                    value=json.loads(output);value['objective']+=' Consumir a referência P0-T002.'
                    return json.dumps(value)
                if request['task']=='outline_phase' and data['phase_source']['number']==0 and not inner.replaced:
                    inner.replaced=True;seed=deepcopy(json.loads(output)['tasks'][1]);seed['scope']+=' para o projeto.'
                    inner.task_repairs['P0-T002']=[{'summary':'Atualizar o recorte mantendo a responsabilidade.',
                        'structural_changes':[{'operation':'remove','task_ids':['P0-T002'],'replacements':[],'reason':'Substituir o recorte.'}],
                        'additions':[{'phase_number':0,'after_task_id':'P0-T001','task':seed}]}]
                return output
        agents=Replacement();engine=PlanEngine(self.state,agents);await engine.run()
        task=next(t for p in engine.phases for t in p.tasks if t.id=='P0-T001')
        self.assertIn('P0-T003',task.content.objective);self.assertNotIn('P0-T002',task.content.objective)
        writes=[e for _,e in agents.calls if e['current_request']['task']=='write_task' and e['current_request']['data']['task_id']==task.id]
        reviews=[e for _,e in agents.calls if e['current_request']['task']=='review_task' and e['current_request']['data']['task']['id']==task.id]
        self.assertEqual(len(writes),1);self.assertEqual(len(reviews),2)
        self.assertEqual(engine.contract_errors,0)
        self.assertTrue(list((self.root/'.harness/plan/control/reference-bindings').glob('*.json')))
        second=PlanAgents();await PlanEngine(self.state,second).run();self.assertEqual(second.calls,[])
