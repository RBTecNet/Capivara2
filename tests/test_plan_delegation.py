import json
import unittest

from harness.domain import WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer
from harness.plan_control import prepare_changes, stub
from harness.plan_coverage import build_requirements, check_assignments, check_seed, delegate_validations
from harness.plan_engine import PlanEngine
from harness.plan_models import Requirement, ValidationDelegation, PlannedPhase, TaskSeed, PlanRepair
import test_plan
from init_fixture import ScriptedAgents, final_document
from plan_fixture import PlanAgents


class PlanDelegationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_plan.PlanTests.asyncSetUp

    async def test_registry_distinguishes_implementation_qa_and_collective_references_before_outlining(self):
        document=final_document()
        document['phases'][0]['tasks'] = ['Validar título obrigatório.',
            'Preparar infraestrutura de testes com bancos isolados.', 'Criar testes próprios de API e interface.']
        registry=build_requirements(document)
        self.assertEqual(registry['P1-A001'].execution_owner,'tasks')
        self.assertEqual(registry['P1-A002'].execution_owner,'tasks')
        self.assertEqual(registry['P1-A003'].execution_owner,'build')
        self.assertEqual(registry['P1-D001'].execution_owner,'collective')
        self.assertEqual(registry['P1-C001'].execution_owner,'collective')
        with self.assertRaisesRegex(ValueError,'P1-D001.*kind=deliverable'):
            delegate_validations(registry,[ValidationDelegation(requirement_id='P1-D001',reason='Delegar QA.')],1)

    async def test_phase_zero_tool_purpose_is_documentation_even_when_it_mentions_executing_tests(self):
        tool = Requirement(id='P0-T001', phase_number=0, kind='tool',
            text='Vitest: Executar testes automatizados das regras e serviços.')
        registry = {tool.id: tool}
        task_seed = TaskSeed(title='Dependência de testes', scope='Identificar Vitest e sua finalidade.',
            source_quotes=['Vitest'], coverage=[{'requirement_id':tool.id, 'excerpt':'Vitest'}])
        check_seed(task_seed,0,tool.text,registry)
        phase=PlannedPhase(number=0,title='Stack',goal='Documentar ferramentas.',depends_on=[],
                           tasks=[stub(0,task_seed,'P0-T001')])
        check_assignments([phase],registry)
        with self.assertRaisesRegex(ValueError,'fase 0 cataloga'):
            delegate_validations(registry,[ValidationDelegation(requirement_id=tool.id,reason='Executar testes.')],0)

    async def test_build_delegation_preserves_qa_source_but_never_absorbs_functional_validation_or_infrastructure(self):
        texts = ['Criar testes próprios de API e interface.',
                 'Validar título e conteúdo obrigatórios.',
                 'Preparar infraestrutura de testes com bancos isolados.',
                 'Aplicar normalização dos nomes e validar a cor.',
                 'Executar verificação de tipos, compilação e testes dos módulos.',
                 'Demonstrar pelo navegador os critérios de cada módulo.',
                 'Revisar a interface em larguras de 360 e 1280 pixels.',
                 'Registrar resultados reais e comandos de reprodução.',
                 'Verificar a senha do usuário antes de permitir a operação.']
        registry = {f'P1-A{i:03d}': Requirement(id=f'P1-A{i:03d}', phase_number=1, kind='activity', text=text)
                    for i,text in enumerate(texts, 1)}
        for number in (1,5,6,7,8):
            identifier = f'P1-A{number:03d}'
            changed = delegate_validations(registry, [ValidationDelegation(requirement_id=identifier,
                reason='Validação explicitamente definida na origem; o build determina sua execução.')], 1)
            self.assertEqual(changed[identifier].execution_owner, 'build')
            self.assertEqual(changed[identifier].text, registry[identifier].text)
            self.assertEqual(registry[identifier].execution_owner, 'tasks')
        for number in (2,3,4,9):
            with self.assertRaisesRegex(ValueError, 'nunca implementação'):
                delegate_validations(registry, [ValidationDelegation(requirement_id=f'P1-A{number:03d}', reason='Delegar.')], 1)

    async def test_existing_qa_task_can_be_removed_in_same_atomic_batch_as_delegation(self):
        qa = Requirement(id='P1-A001', phase_number=1, kind='activity', text='Executar testes dos módulos.')
        registry = {qa.id:qa}
        seed = TaskSeed(title='Testes', scope=qa.text, source_quotes=[qa.text],
                        coverage=[{'requirement_id':qa.id, 'excerpt':qa.text}])
        phase = PlannedPhase(number=1, title='Verificação', goal='Verificar entrega.', depends_on=[0],
                             tasks=[stub(1,seed,'P1-T001')])
        repair = PlanRepair(summary='O build define testes; preservar o requisito sem prescrevê-los.', decisions=[],
            corrections=[], additions=[], structural_changes=[{'operation':'remove','task_ids':['P1-T001'],
            'reason':'Responsabilidade delegada ao build.', 'replacements':[]}],
            delegations=[{'requirement_id':qa.id, 'reason':'Execução de testes pertence ao build.'}])
        updated = delegate_validations(registry, repair.delegations, 1)
        with self.assertRaisesRegex(ValueError, 'delegada ao build'):
            check_seed(seed, 1, qa.text, updated)
        result, *_ = prepare_changes([phase], repair, updated, {1:{'text':qa.text}}, {1:1})
        self.assertEqual(result[0].tasks, [])
        check_assignments(result,updated)
        self.assertEqual(len(phase.tasks),1)

    async def test_phase_only_of_validation_remains_in_plan_without_generating_test_tasks(self):
        document = final_document()
        document['phases'].append({'number':3,'title':'Verificação consolidada','goal':'Verificar os requisitos da entrega.',
            'tasks':['Executar testes dos módulos e fluxos reais.', 'Registrar resultados reais da verificação.'],
            'deliverables':['Resultados reais disponíveis.'], 'acceptance_criteria':['Requisitos atendidos.'], 'depends_on':[2]})
        agents = ScriptedAgents()
        agents.documents = [document]
        async def answer(q,index,total):
            return Answer(question_id=q.id,option_id=q.recommended_option_id,label='Local',text='')
        state = WizardState(self.root,prompt='Aplicação de tarefas.',writer=self.state.writer,reviewer=self.state.reviewer)
        final_init = await InitEngine(state,agents,interview=answer).run()
        state.module='plan'
        state.prompt=final_init.read_text()
        class QAPlan(PlanAgents):
            async def generate(self, selection, role, prompt, schema, store, call_id):
                output=await super().generate(selection,role,prompt,schema,store,call_id)
                request=self.calls[-1][1]['current_request']
                if request['task']=='outline_phase' and request['data']['phase_source']['number']==3:
                    return json.dumps({'tasks':[], 'delegations':[
                        {'requirement_id':r['id'],'reason':'A execução e definição de validações pertencem ao build.'}
                        for r in request['data']['requirements'] if r['kind']=='activity']})
                return output
        plan_agents=QAPlan()
        final=await PlanEngine(state,plan_agents).run()
        plan=json.loads(final.with_suffix('.json').read_text())
        self.assertEqual(plan['phases'][3]['tasks'],[])
        self.assertEqual(plan['phases'][3]['title'],'Verificação consolidada')
        self.assertIn('sem gerar instruções de testes',final.read_text())
        self.assertTrue(all(r['execution_owner']=='build' for r in plan['requirements']
                            if r['phase_number']==3 and r['kind']=='activity'))
        self.assertFalse(any(role=='writer' and e['current_request']['data']['phase_number']==3 for role,e in plan_agents.calls))
