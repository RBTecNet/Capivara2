"""Contratos comuns, observações editoriais e retomada independente do provedor."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch, AsyncMock

from harness.cli_protocols import native_result
from harness.plan_adapter import normalize_review, compact_context, wire_schema
from harness.plan_contracts import TaskAssessment, CollectiveAssessment, patched_content
from harness.plan_control import stub, version
from harness.plan_coverage import build_requirements
from harness.plan_engine import PlanEngine
from harness.plan_models import TaskContent, TaskSeed
from harness.response_adapter import decode_response
from init_fixture import final_document
from plan_fixture import PlanAgents
import test_plan


class ResponseAdapterTests(unittest.TestCase):
    def test_transport_variations_have_one_canonical_payload(self):
        payload={'summary':'Fiel.','findings':[]}
        text=json.dumps(payload)
        for output in (text, '```json\n'+text+'\n```', 'Resposta final:\n'+text,
                       json.dumps(text), text+'\n'+text):
            self.assertEqual(decode_response(output),payload)
        frames={
            'opencode':{'type':'text','part':{'text':'```json\n'+text+'\n```'}},
            'claude':{'type':'result','subtype':'success','structured_output':payload,'result':'texto redundante'},
            'agy':{'event':'result','result':{'status':'SUCCESS','structured_output':payload,'response':'texto redundante'}}}
        for provider, frame in frames.items():
            self.assertEqual(decode_response(native_result(provider,json.dumps(frame),allow_tools=False)),payload)
        for output in ('não é JSON', '{}\n{"approved":true}', '{}\n```json\n{"approved":true}\n```', '[]'):
            with self.assertRaises(ValueError):decode_response(output)

    def sample(self):
        requirements=build_requirements(final_document())
        text=requirements['P2-A001'].text
        task=stub(2,TaskSeed(title='Tarefas',scope=text,source_quotes=[text],
            coverage=[{'requirement_id':'P2-A001','excerpt':text}]),'P2-T001')
        task.content=TaskContent(title='Tarefas',objective='Adicionar tarefas.',
            implementation_steps=['Adicionar tarefas.'],deliverables=['Cadastro.'])
        return task, requirements, {'task':task.model_dump(),'base_version':version(task)}

    def test_controller_builds_version_evidence_before_and_acceptance(self):
        task,requirements,data=self.sample()
        wire={'summary':'Falta editar.','findings':[{'task_id':task.id,'category':'coverage',
            'reason':'Operação omitida.','instruction':'Incluir edição.','requirement_ids':['P2-A001'],
            'edits':[{'field':'objective','find':'Adicionar tarefas.','replace':'Adicionar e editar tarefas.'}]}], 'replan':None}
        raw,notes=normalize_review(wire,TaskAssessment,data,requirements)
        verdict=TaskAssessment.model_validate(raw)
        self.assertFalse(verdict.approved)
        spec=verdict.corrections[0]
        self.assertEqual(spec.base_version,version(task))
        self.assertEqual(spec.evidence[0].quote,requirements['P2-A001'].text)
        self.assertEqual(patched_content(task,spec,requirements).objective,'Adicionar e editar tarefas.')
        self.assertEqual(task.content.objective,'Adicionar tarefas.')
        self.assertEqual(notes,[])
        schema=wire_schema(TaskAssessment,data,requirements)
        self.assertNotIn('approved',schema['properties'])
        self.assertNotIn('base_version',schema['$defs']['Finding']['properties'])

    def test_editorial_is_nonblocking_and_functional_findings_still_block(self):
        task,requirements,data=self.sample()
        editorial={'task_id':task.id,'category':'editorial','reason':'Grafia sem mudança de sentido.',
            'instruction':'Ajuste opcional.','requirement_ids':[], 'edits':[]}
        wire={'summary':'Pedido atendido.','findings':[editorial],'replan':None}
        raw,notes=normalize_review(wire,TaskAssessment,data,requirements)
        self.assertTrue(TaskAssessment.model_validate(raw).approved)
        self.assertEqual(len(notes),1)
        self.assertEqual(task.content.title,'Tarefas')
        wire['findings'].append({**editorial,'category':'fidelity','edits':[]})
        with self.assertRaises(ValueError):normalize_review(wire,TaskAssessment,data,requirements)

    def test_legacy_list_patch_is_unambiguous_but_a_contradiction_is_not_approved(self):
        task,requirements,data=self.sample()
        raw={'approved':False,'matches_source':True,'coverage_complete':True,'tasks_are_small':True,
            'no_test_strategy':True,'summary':'Todos os critérios atendidos, mas há grafia.',
            'corrections':[{'task_id':task.id,'base_version':version(task),'category':'fidelity',
                'reason':'Grafia.','instruction':'Corrigir.', 'evidence':[{'requirement_id':'P2-A001','quote':requirements['P2-A001'].text}],
                'changes':[{'field':'implementation_steps','before':'Adicionar tarefas.','after':'Adicionar as tarefas.'}],
                'acceptance':[{'field':'implementation_steps','operator':'contains','value':'Adicionar as tarefas.'}]}], 'replan':None}
        adapted,_=normalize_review(raw,TaskAssessment,data,requirements)
        self.assertEqual(adapted['corrections'][0]['changes'][0]['before'],['Adicionar tarefas.'])
        with self.assertRaisesRegex(ValueError,'Reprovar exige'):TaskAssessment.model_validate(adapted)
        self.assertIsInstance(raw['corrections'][0]['changes'][0]['before'],str)

    def test_context_keeps_phase_requirements_and_global_constraints_without_other_phases(self):
        data={'source':{'source_prompt_sha256':'hash','original_prompt':'x'*20000,'approved_prompt':'y'*20000,
            'document':final_document()},'phase_source':{'number':2,'text':'referência completa da fase'},
            'requirements':[{'id':'P2-A001','text':'requisito literal'}]}
        compact=compact_context('outline_phase',data)
        self.assertLess(len(json.dumps(compact)),len(json.dumps(data))/2)
        self.assertEqual(compact['phase_source'],data['phase_source'])
        self.assertEqual(compact['requirements'],data['requirements'])
        self.assertEqual(compact['source']['document']['requirements'],data['source']['document']['requirements'])
        self.assertIn('original_prompt',data['source'])
        self.assertNotIn('phases',compact['source']['document'])


class AdapterRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_inconsistent_phase_response_returns_only_to_reviewer_and_preserves_tasks(self):
        class Inconsistent(PlanAgents):
            phase_calls=0
            async def generate(inner,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request']
                if request['task']=='review_phase' and request['data']['phase']['number']==0:
                    inner.phase_calls+=1
                    if inner.phase_calls<=3:
                        return json.dumps({'approved':False,'matches_source':True,'coverage_complete':True,
                            'tasks_are_small':True,'no_test_strategy':True,'summary':'Reprovação contraditória.',
                            'corrections':[],'additions':[],'structural_changes':[]})
                    task=request['data']['phase']['tasks'][0]
                    return json.dumps({'summary':'Fase fiel; observação opcional de grafia.',
                        'findings':[{'task_id':task['id'],'category':'editorial','reason':'Grafia sem perda de sentido.',
                            'instruction':'Revisão editorial opcional.','requirement_ids':[],'edits':[]}],
                        'structural_changes':[],'additions':[]})
                return result
        agents=Inconsistent();e=PlanEngine(self.state,agents)
        with patch('harness.plan_engine.asyncio.sleep',new=AsyncMock()):await e.run()
        self.assertEqual(agents.phase_calls,4)
        operations=[r['current_request']['task'] for _,r in agents.calls]
        self.assertNotIn('correct_task',operations)
        self.assertEqual(operations.count('write_task'),len(e.approved_tasks))
        self.assertEqual(e.contract_errors,3)
        self.assertTrue(list((self.root/'.harness/plan/control/observations').glob('*.json')))

    async def test_wire_functional_correction_is_applied_and_reviewed(self):
        class Wire(PlanAgents):
            sent=False
            async def generate(inner,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request']
                if request['task']=='review_task' and not inner.sent:
                    inner.sent=True;task=request['data']['task']
                    claim=task['seed']['coverage'][0]
                    return json.dumps({'summary':'Explicitar o requisito.','replan':None,'findings':[{
                        'task_id':task['id'],'category':'coverage','reason':'Falta explicitação.',
                        'instruction':'Explicitar no objetivo.','requirement_ids':[claim['requirement_id']],
                        'edits':[{'field':'objective','find':'','replace':' Requisito preservado: '+claim['excerpt']}]}]})
                return result
        agents=Wire();e=PlanEngine(self.state,agents);await e.run()
        self.assertEqual(len(e.correction_book.records),1)
        self.assertTrue(all(r['status']=='verified' for r in e.correction_book.records.values()))
        self.assertEqual(e.contract_errors,0)
