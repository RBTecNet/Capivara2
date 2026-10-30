import json
import unittest

from harness.domain import WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer
from harness.plan_classification import check_classification, classified_requirements
from harness.plan_coverage import build_requirements, check_seed, delegate_validations
from harness.plan_contracts import WorkClassification, TaskAssessment, CollectiveAssessment
from harness.plan_engine import PlanEngine
from harness.plan_models import TaskSeed
import test_plan
from init_fixture import ScriptedAgents, final_document
from plan_fixture import PlanAgents


class PlanDelegationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_plan.PlanTests.asyncSetUp

    def classify(self, texts, responsibilities):
        document=final_document()
        document['phases']=document['phases'][:1]
        document['phases'][0]['tasks']=texts
        raw=build_requirements(document)
        proposal=WorkClassification(activities=[{'source_id':f'P1-A{i:03d}', 'parts':[
            {'text':text,'responsibility':owner,'reason':'Responsabilidade conferida contra a origem.'}]}
            for i,(text,owner) in enumerate(zip(texts,responsibilities),1)])
        return raw,proposal,classified_requirements(proposal,raw)

    async def test_qa_responsibility_does_not_depend_on_first_verb(self):
        texts=['Usar Playwright para executar fluxos reais no navegador.',
               'Com Playwright, implementar cenários de exclusão e edição.',
               'Registrar no repositório evidências verificáveis dos critérios de aceite.',
               'Aplicar validação de título obrigatório na API.']
        raw,proposal,registry=self.classify(texts,['build','build','build','implementation'])
        self.assertEqual([registry[f'P1-A{i:03d}'].execution_owner for i in range(1,5)],['build','build','build','tasks'])
        self.assertTrue(all(registry[f'P1-A{i:03d}'].text==text for i,text in enumerate(texts,1)))
        self.assertTrue(all(r.execution_owner=='tasks' for r in registry.values() if r.phase_number==0))
        self.assertEqual(raw['P1-A001'].execution_owner,'tasks')

    async def test_mixed_commands_and_evidence_are_partitioned_without_losing_source(self):
        text='Registrar no repositório os comandos e evidências verificáveis dos critérios de aceite.'
        raw,proposal,_=self.classify([text],['documentation'])
        proposal.activities[0].parts=[
            {'text':'Registrar no repositório os comandos','responsibility':'documentation','reason':'Comandos de operação.'},
            {'text':'e evidências verificáveis dos critérios de aceite.','responsibility':'build','reason':'Evidências de validação.'}]
        proposal=WorkClassification.model_validate(proposal.model_dump())
        registry=classified_requirements(proposal,raw)
        self.assertEqual(registry['P1-A001'].execution_owner,'collective')
        self.assertEqual(registry['P1-A001.01'].execution_owner,'tasks')
        self.assertEqual(registry['P1-A001.02'].execution_owner,'build')
        self.assertEqual(registry['P1-A001.02'].source_id,'P1-A001')
        proposal.activities[0].parts[1].text='e verificações inventadas.'
        with self.assertRaisesRegex(ValueError,'literal'):
            check_classification(proposal,raw)

    async def test_classification_cannot_omit_a_middle_or_final_clause(self):
        raw,proposal,_=self.classify(['Criar rotas e serviços e testes próprios.'],['implementation'])
        proposal.activities[0].parts[0].text='Criar rotas'
        with self.assertRaisesRegex(ValueError,'omitiu texto'):
            check_classification(proposal,raw)
        proposal.activities[0].parts[0].text='serviços e testes próprios.'
        with self.assertRaisesRegex(ValueError,'omitiu texto'):
            check_classification(proposal,raw)

    async def test_approved_ownership_cannot_be_reinterpreted_by_reviews(self):
        raw,proposal,registry=self.classify(['Usar Playwright para executar fluxos.'],['build'])
        with self.assertRaisesRegex(ValueError,'classificação semântica aprovada'):
            delegate_validations(registry,[{'requirement_id':'P1-A001'}],1)
        seed=TaskSeed(title='Fluxos',scope='Criar fluxos.',source_quotes=['Usar Playwright'],
                      coverage=[{'requirement_id':'P1-A001','excerpt':'Usar Playwright'}])
        with self.assertRaisesRegex(ValueError,'delegada ao build'):
            check_seed(seed,1,registry['P1-A001'].text,registry)
        valid={'approved':True,'matches_source':True,'coverage_complete':True,'tasks_are_small':True,
               'no_test_strategy':True,'summary':'Aprovado.','corrections':[],'replan':None}
        with self.assertRaises(ValueError):
            TaskAssessment.model_validate({**valid,'delegations':[]})
        collective={k:v for k,v in valid.items() if k!='replan'}
        with self.assertRaises(ValueError):
            CollectiveAssessment.model_validate({**collective,'structural_changes':[],'additions':[],'delegations':[]})

    async def test_phase_only_of_validation_is_preserved_without_prescribing_tests(self):
        document=final_document()
        document['phases'].append({'number':3,'title':'Verificação consolidada','goal':'Verificar requisitos da entrega.',
            'tasks':['Executar testes dos módulos e fluxos reais.','Registrar resultados reais da verificação.'],
            'deliverables':['Resultados disponíveis.'],'acceptance_criteria':['Requisitos atendidos.'],'depends_on':[2]})
        agents=ScriptedAgents();agents.documents=[document]
        async def answer(q,index,total):
            return Answer(question_id=q.id,option_id=q.recommended_option_id,label='Local',text='')
        state=WizardState(self.root,prompt='Aplicação de tarefas.',writer=self.state.writer,reviewer=self.state.reviewer)
        init=await InitEngine(state,agents,interview=answer).run()
        state.module='plan';state.prompt=init.read_text()
        final=await PlanEngine(state,PlanAgents()).run()
        plan=json.loads(final.with_suffix('.json').read_text())
        self.assertEqual(plan['phases'][3]['tasks'],[])
        self.assertIn('inclusive testes não mencionados na origem',final.read_text())
        self.assertTrue(all(r['execution_owner']=='build' for r in plan['requirements'] if r['phase_number']==3 and r['kind']=='activity'))
