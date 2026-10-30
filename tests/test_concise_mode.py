"""Concisão sem novos gates de tamanho nem remoção de informação funcional."""
from copy import deepcopy
import json
import unittest

from harness.agent_style import CONCISE_STYLE, compact_schema, render_json, styled
from harness.build_instructions import build_prompt
from harness.init_instructions import REVIEWER,WRITER
from harness.plan_adapter import compact_context
from harness.plan_engine import PlanEngine
from harness.plan_instructions import instructions_for
from plan_fixture import PlanAgents
import test_plan


class ConcisePolicyTests(unittest.TestCase):
    def test_schema_annotations_are_removed_but_fields_and_literal_values_are_preserved(self):
        schema={'title':'Payload','type':'object','properties':{
            'title':{'title':'Título','type':'string'},
            'fixed':{'title':'Fixo','const':{'title':'Valor literal obrigatório'}},
            'choice':{'enum':[{'title':'Literal'}]}},'required':['title','fixed','choice'],'additionalProperties':False}
        original=deepcopy(schema);compact=compact_schema(schema)
        self.assertNotIn('title',compact)
        self.assertIn('title',compact['properties'])
        self.assertEqual(compact['properties']['fixed']['const'],schema['properties']['fixed']['const'])
        self.assertEqual(compact['properties']['choice']['enum'],schema['properties']['choice']['enum'])
        self.assertEqual(compact['required'],schema['required'])
        self.assertEqual(schema,original)
        value={'ação':'preservar  dois espaços\ne a linha seguinte','n':3}
        self.assertEqual(json.loads(render_json(value)),value)

    def test_all_modules_share_the_policy_once_and_keep_request_data(self):
        for prompt in (REVIEWER,WRITER,instructions_for('reviewer','review_task'),instructions_for('writer','write_task')):
            self.assertEqual(styled(prompt).count('CAPIVARA_AGENT_STYLE=concise'),1)
        request={'task':{'objective':'Preservar todos os campos e regras.'},'code':'assert result == 6\n'}
        prompt=build_prompt('write',request,{'title':'Resposta','type':'object','properties':{'summary':{'type':'string'}}})
        self.assertEqual(prompt.count('CAPIVARA_AGENT_STYLE=concise'),1)
        self.assertEqual(json.loads(prompt.rsplit('\n\nREQUEST:\n',1)[1])['data'],request)
        self.assertIn('evidências literais',CONCISE_STYLE)
        self.assertIn('Ignore questões editoriais',instructions_for('reviewer','review_task'))

    def test_context_removes_only_duplicate_metadata_and_keeps_the_task_intact(self):
        requirement={'id':'P7-A001','text':'Preservar o contrato inteiro.','phase_number':7,'kind':'activity'}
        task={'id':'P7-T004','seed':{'title':'Unidade','scope':'Implementar a unidade.',
            'source_quotes':['Preservar o contrato inteiro.'],'coverage':[{'requirement_id':'P7-A001','excerpt':requirement['text']}]},
            'content':{'title':'Unidade','objective':'Implementar a unidade.','implementation_steps':['Fazer o necessário.'],
                       'deliverables':['Entrega funcional.']}}
        data={'task':task,'assigned_requirements':[requirement],'product_requirements':[{'requirement_id':'R001','text':'Regra global.'}],
            'responsibility_map':[{'requirement':requirement,'owners':[{'task_id':task['id'],'scope':task['seed']['scope'],'excerpt':requirement['text']},
                {'task_id':'P7-T002','scope':'Dependência declarada.','excerpt':'Contrato compartilhado.'}]}]}
        original=deepcopy(data);compact=compact_context('review_task',data)
        self.assertEqual(compact['task'],original['task'])
        self.assertEqual(compact['assigned_requirements'],original['assigned_requirements'])
        self.assertEqual(compact['product_requirements'],original['product_requirements'])
        self.assertEqual(compact['responsibility_map'][0]['requirement'],{'id':requirement['id']})
        self.assertEqual(compact['responsibility_map'][0]['owners'][1],original['responsibility_map'][0]['owners'][1])
        self.assertEqual(data,original)
        self.assertLess(len(render_json(compact)),len(json.dumps(data,ensure_ascii=False)))


class ConciseCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_verbose_legacy_response_is_not_rejected_or_truncated(self):
        class Verbose(PlanAgents):
            async def generate(inner,*args,**kwargs):
                output=await super().generate(*args,**kwargs)
                request=inner.calls[-1][1]['current_request']
                if request['task']=='review_task':
                    self.assertEqual(args[2].count('CAPIVARA_AGENT_STYLE=concise'),1)
                    value=json.loads(output);value['summary']='Explicação histórica válida. '*120
                    return json.dumps(value)
                return output
        agents=Verbose();engine=PlanEngine(self.state,agents);await engine.run()
        self.assertEqual(engine.contract_errors,0)
        for path in (self.root/'.harness/plan/phases').glob('*/task-approvals/*.json'):
            self.assertGreater(len(json.loads(path.read_text())['review']['summary']),1000)
