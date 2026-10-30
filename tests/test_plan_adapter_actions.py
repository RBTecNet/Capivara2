"""Encaminhamento de ações por conteúdo/estrutura, sem depender de projeto ou task."""
from copy import deepcopy
import json
import unittest

from harness.plan_adapter import normalize_review, wire_schema
from harness.plan_contracts import TaskAssessment, CollectiveAssessment, patched_content
from harness.plan_control import stub, version
from harness.plan_models import Requirement, TaskSeed, TaskContent
from harness.plan_engine import PlanEngine
from plan_fixture import PlanAgents
import test_plan


def sample(number=2, suffix=1):
    identifier=f'P{number}-A001'
    requirements={identifier:Requirement(id=identifier,phase_number=number,kind='activity',
        text='Integrar o consumidor ao recurso configurado pela camada compartilhada.')}
    seed=TaskSeed(title='Consumidor',scope='Consumir o recurso existente.',source_quotes=[requirements[identifier].text],
        coverage=[{'requirement_id':identifier,'excerpt':requirements[identifier].text}])
    task=stub(number,seed,f'P{number}-T{suffix:03d}')
    task.content=TaskContent(title='Consumidor',objective='Configurar o recurso e integrar o consumidor.',
        implementation_steps=['Configurar o recurso.','Integrar o consumidor.'],deliverables=['Consumidor integrado.'])
    return task,requirements,{'task':task.model_dump(),'base_version':version(task)}


def finding(task,identifier,category='granularity',edits=True):
    return {'task_id':task.id,'category':category,'reason':'A configuração pertence a outra unidade.',
        'instruction':'Consumir o recurso já configurado.','requirement_ids':[identifier],
        'edits':[{'field':'objective','find':'Configurar o recurso e integrar','replace':'Usar o recurso existente e integrar'}] if edits else []}


class PlanActionTests(unittest.TestCase):
    def test_content_edits_are_accepted_for_every_functional_category_and_task(self):
        for number,suffix in ((0,4),(3,12),(8,27)):
            task,requirements,data=sample(number,suffix);identifier=next(iter(requirements))
            for category in ('fidelity','coverage','granularity','test_strategy'):
                with self.subTest(task=task.id,category=category):
                    payload={'summary':'Ajuste de responsabilidade no conteúdo.','replan':None,
                        'findings':[finding(task,identifier,category)]}
                    original=deepcopy(payload)
                    raw,_=normalize_review(payload,TaskAssessment,data,requirements)
                    result=TaskAssessment.model_validate(raw)
                    self.assertFalse(result.approved)
                    self.assertIsNone(result.replan)
                    self.assertEqual(len(result.corrections),1)
                    self.assertEqual(result.corrections[0].category,'fidelity' if category=='granularity' else category)
                    target=patched_content(task,result.corrections[0],requirements)
                    self.assertEqual(target.objective,'Usar o recurso existente e integrar o consumidor.')
                    self.assertEqual(task.content.objective,'Configurar o recurso e integrar o consumidor.')
                    self.assertEqual(payload,original)

    def test_replan_and_explanatory_findings_do_not_become_content_patches(self):
        task,requirements,data=sample(7,19);identifier=next(iter(requirements))
        for category in ('granularity','fidelity','coverage'):
            payload={'summary':'Necessário dividir a unidade.','findings':[finding(task,identifier,category,False)],
                'replan':{'task_id':task.id,'reason':'Dividir o escopo.', 'requirement_ids':[identifier]}}
            raw,_=normalize_review(payload,TaskAssessment,data,requirements)
            result=TaskAssessment.model_validate(raw)
            self.assertFalse(result.approved);self.assertEqual(result.corrections,[])
            self.assertEqual(result.replan.task_id,task.id)
            self.assertEqual(result.replan.evidence[0].quote,requirements[identifier].text)
        schema=wire_schema(TaskAssessment,data,requirements)
        self.assertIn('requirement_ids',schema['$defs']['ReplanRequest']['properties'])
        self.assertNotIn('evidence',schema['$defs']['ReplanRequest']['properties'])

    def test_old_replan_response_uses_canonical_ids_without_recopying_quotes(self):
        task,requirements,data=sample();identifier=next(iter(requirements))
        payload={'summary':'Estrutura.','findings':[finding(task,identifier,edits=False)],
            'replan':{'task_id':task.id,'reason':'Dividir.', 'evidence':[
                {'requirement_id':identifier,'quote':'Texto da task copiado no lugar da origem.'},
                {'requirement_id':identifier,'quote':'Outra cópia redundante.'}]}}
        raw,_=normalize_review(payload,TaskAssessment,data,requirements)
        result=TaskAssessment.model_validate(raw)
        self.assertEqual(len(result.replan.evidence),1)
        self.assertEqual(result.replan.evidence[0].quote,requirements[identifier].text)
        payload['replan']['evidence'][0]['requirement_id']='inexistente'
        with self.assertRaisesRegex(ValueError,'atribuídos'):normalize_review(payload,TaskAssessment,data,requirements)

    def test_granularity_without_edits_requests_review_of_structure_and_never_approves(self):
        task,requirements,data=sample();identifier=next(iter(requirements))
        raw,_=normalize_review({'summary':'Unidade ampla.','findings':[finding(task,identifier,edits=False)],
            'replan':None},TaskAssessment,data,requirements)
        result=TaskAssessment.model_validate(raw)
        self.assertFalse(result.approved);self.assertEqual(result.corrections,[])
        self.assertIsNotNone(result.replan)

    def test_collective_structure_and_other_task_content_can_coexist(self):
        first,requirements,_=sample(4,1);second,_,_=sample(4,2);identifier=next(iter(requirements))
        data={'phase':{'number':4,'tasks':[first.model_dump(),second.model_dump()]},
            'approved_versions':{t.id:version(t) for t in (first,second)}}
        payload={'summary':'Remover unidade redundante e delimitar outra.',
            'findings':[finding(first,identifier,edits=False),finding(second,identifier)],
            'structural_changes':[{'operation':'remove','task_ids':[first.id],'reason':'Unidade redundante.','replacements':[]}],
            'additions':[]}
        raw,_=normalize_review(payload,CollectiveAssessment,data,requirements)
        result=CollectiveAssessment.model_validate(raw)
        self.assertFalse(result.approved)
        self.assertEqual([c.task_id for c in result.corrections],[second.id])
        self.assertEqual(result.structural_changes[0].task_ids,[first.id])
        payload['structural_changes']=[]
        with self.assertRaisesRegex(ValueError,'sem ação'):normalize_review(payload,CollectiveAssessment,data,requirements)

    def test_conflicting_actions_or_nonstructural_findings_without_edits_stay_unapproved(self):
        task,requirements,data=sample();identifier=next(iter(requirements))
        payload={'summary':'Ações incompatíveis.','findings':[finding(task,identifier)],
            'replan':{'task_id':task.id,'reason':'Dividir.', 'requirement_ids':[identifier]}}
        with self.assertRaisesRegex(ValueError,'mesma task'):normalize_review(payload,TaskAssessment,data,requirements)
        payload.update(replan=None,findings=[finding(task,identifier,'coverage',False)])
        with self.assertRaisesRegex(ValueError,'sem ação'):normalize_review(payload,TaskAssessment,data,requirements)

    def test_malformed_structural_response_is_a_recoverable_validation_error(self):
        task,requirements,data=sample()
        for concern in ('replanejar', {'task_id':task.id,'reason':'Dividir.','evidence':[{}]},
                        {'task_id':task.id,'reason':'Dividir.','evidence':None}):
            with self.subTest(replan=concern), self.assertRaises(ValueError):
                normalize_review({'summary':'Estrutura.','findings':[],'replan':concern},TaskAssessment,data,requirements)


class PlanActionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_structural_finding_is_forwarded_to_reassessment_without_a_content_patch(self):
        class Structure(PlanAgents):
            sent=False
            async def generate(inner,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request'];data=request['data']
                if request['task']=='review_task' and not inner.sent:
                    inner.sent=True;task=data['task'];claim=task['seed']['coverage'][0]
                    return json.dumps({'summary':'Conferir o recorte estrutural.', 'findings':[{
                        'task_id':task['id'],'category':'granularity','reason':'Verificar a divisão de responsabilidades.',
                        'instruction':'Conferir a estrutura antes de redigir novamente.',
                        'requirement_ids':[claim['requirement_id']],'edits':[]}],
                        'replan':{'task_id':task['id'],'reason':'Verificar a divisão de responsabilidades.',
                                  'requirement_ids':[claim['requirement_id']]}})
                return result
        agents=Structure();engine=PlanEngine(self.state,agents);await engine.run()
        operations=[e['current_request']['task'] for _,e in agents.calls]
        self.assertEqual(operations.count('reassess_phase'),1)
        self.assertNotIn('correct_task',operations)
        self.assertEqual(engine.contract_errors,0)
        self.assertEqual(len(engine.approved_tasks),sum(len(p.tasks) for p in engine.phases))

    async def test_granularity_content_edits_follow_writer_and_review_without_replanning(self):
        class Edits(PlanAgents):
            sent=False
            async def generate(inner,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request'];data=request['data']
                if request['task']=='review_task' and not inner.sent:
                    inner.sent=True;task=data['task'];claim=task['seed']['coverage'][0]
                    return json.dumps({'summary':'Delimitar responsabilidade.','replan':None,'findings':[{
                        'task_id':task['id'],'category':'granularity','reason':'Explicitar a origem do recurso.',
                        'instruction':'Registrar o requisito atribuído.','requirement_ids':[claim['requirement_id']],
                        'edits':[{'field':'objective','find':'','replace':' Requisito preservado: '+claim['excerpt']}]}]})
                return result
        agents=Edits();engine=PlanEngine(self.state,agents);await engine.run()
        operations=[e['current_request']['task'] for _,e in agents.calls]
        self.assertEqual(operations.count('correct_task'),1)
        self.assertNotIn('reassess_phase',operations)
        self.assertEqual(engine.contract_errors,0)
        self.assertTrue(all(r['status']=='verified' for r in engine.correction_book.records.values()))
