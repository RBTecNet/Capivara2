from copy import deepcopy
import json
import unittest

from harness.artifacts import read_status, suggested_module
from harness.plan_contracts import (ContentCorrection, PlanContractError, TaskAssessment,
    CollectiveAssessment, StructureRepair, ClassificationReview, ContractValidation,
    patched_content)
from harness.plan_control import stub, version
from harness.plan_corrections import CorrectionBook
from harness.plan_coverage import build_requirements
from harness.plan_engine import PlanEngine
from harness.plan_models import TaskSeed, TaskContent, agent_schema
from init_fixture import final_document
from plan_fixture import PlanAgents, rejected
import test_plan


class CorrectionContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_plan.PlanTests.asyncSetUp

    def sample(self):
        requirements=build_requirements(final_document())
        text=requirements['P2-A001'].text
        seed=TaskSeed(title='Operações',scope=text,source_quotes=[text],
                      coverage=[{'requirement_id':'P2-A001','excerpt':text}])
        task=stub(2,seed,'P2-T001')
        task.content=TaskContent(title='Operações',objective='Adicionar tarefas.',
                                implementation_steps=['Adicionar tarefas.'],deliverables=['Cadastro.'])
        spec=ContentCorrection(task_id=task.id,base_version=version(task),category='coverage',
            reason='O objetivo omite editar, concluir e excluir.',instruction='Preservar as quatro operações solicitadas.',
            evidence=[{'requirement_id':'P2-A001','quote':text}],
            changes=[{'field':'objective','before':'Adicionar tarefas.','after':'Adicionar, editar, concluir e excluir tarefas.'}],
            acceptance=[{'field':'objective','operator':'contains','value':'editar, concluir e excluir'}])
        return task,spec,requirements

    async def test_patch_has_an_objective_acceptance_and_preserves_untouched_fields(self):
        task,spec,requirements=self.sample()
        target=patched_content(task,spec,requirements)
        self.assertEqual(target.objective,'Adicionar, editar, concluir e excluir tarefas.')
        self.assertEqual(target.implementation_steps,task.content.implementation_steps)
        self.assertFalse(spec.acceptance[0].satisfied(task.content))
        self.assertTrue(spec.acceptance[0].satisfied(target))
        self.assertEqual(task.content.objective,'Adicionar tarefas.')

    async def test_vague_stale_or_already_satisfied_contract_never_reaches_writer(self):
        task,spec,requirements=self.sample()
        stale=spec.model_copy(deep=True);stale.base_version='versão antiga'
        with self.assertRaisesRegex(ValueError,'base_version'):
            patched_content(task,stale,requirements)
        no_op=spec.model_copy(deep=True);no_op.acceptance[0].value='tarefas'
        with self.assertRaisesRegex(ValueError,'já está satisfeita'):
            patched_content(task,no_op,requirements)
        wrong=spec.model_copy(deep=True);wrong.changes[0].before='Outro documento.'
        with self.assertRaisesRegex(ValueError,'before'):
            patched_content(task,wrong,requirements)
        with self.assertRaises(ValueError):
            ContentCorrection.model_validate({**spec.model_dump(),'acceptance':[]})

    async def test_same_conflict_cannot_be_emitted_twice_even_with_different_wording(self):
        task,spec,requirements=self.sample()
        book=CorrectionBook('hash-da-origem')
        identifier,target=book.issue(task,spec,requirements)
        task.content=target;book.applied(identifier,task);book.verify(task,approved=True)
        again=spec.model_copy(deep=True)
        again.reason='Outra redação do mesmo erro.'
        again.base_version=version(task)
        with self.assertRaisesRegex(PlanContractError,'mesmo conflito'):
            book.check(task,again,requirements)
        with self.assertRaisesRegex(PlanContractError,'novamente'):
            book.applied(identifier,task)

    async def test_new_correction_cannot_reverse_a_previous_acceptance(self):
        task,spec,requirements=self.sample()
        book=CorrectionBook()
        identifier,target=book.issue(task,spec,requirements)
        task.content=target;book.applied(identifier,task);book.verify(task,approved=True)
        opposite=spec.model_copy(deep=True)
        opposite.category='fidelity';opposite.base_version=version(task)
        opposite.changes[0].before=target.objective
        opposite.changes[0].after='Adicionar tarefas.'
        opposite.acceptance[0].operator='excludes'
        with self.assertRaisesRegex(PlanContractError,'mesmo conflito|contradiz o aceite'):
            book.check(task,opposite,requirements)

    async def test_applying_patch_does_not_count_as_reviewer_approval(self):
        task,spec,requirements=self.sample()
        book=CorrectionBook()
        identifier,target=book.issue(task,spec,requirements)
        task.content=target;book.applied(identifier,task);book.verify(task)
        self.assertEqual(book.records[identifier]['status'],'applied')
        book.verify(task,approved=True)
        self.assertEqual(book.records[identifier]['status'],'verified')

    async def test_format_failure_gets_one_repair_then_diagnostic_without_loop_or_approval(self):
        agents=PlanAgents();agents.invalid=20
        engine=PlanEngine(self.state,agents)
        with self.assertRaisesRegex(PlanContractError,'uma oportunidade'):
            await engine.run()
        self.assertEqual(len(agents.calls),2)
        self.assertEqual(engine.contract_errors,2)
        self.assertEqual(engine.corrections,0)
        self.assertEqual(agents.calls[1][1]['rejected_response'],'resposta inválida')
        self.assertEqual(agents.calls[1][1]['preserved_context'],[])
        self.assertTrue((self.root/'.harness/plan/control/contract-failure.json').exists())
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'failed')
        self.assertFalse((self.root/'.harness/plan/final.md').exists())

    async def test_approved_patch_is_enforced_if_writer_ignores_it_without_a_second_attempt(self):
        agents=PlanAgents();agents.ignore_correction=True
        agents.task_reviews=[rejected('P0-T001','Explicite o requisito da stack.')]
        engine=PlanEngine(self.state,agents)
        final=await engine.run()
        fixes=[e for role,e in agents.calls if e['current_request']['task']=='correct_task']
        self.assertEqual(len(fixes),1)
        self.assertEqual(engine.writer_recoveries,1)
        self.assertEqual(engine.corrections,1)
        self.assertEqual(engine.contract_errors,0)
        records=list(engine.correction_book.records.values())
        self.assertEqual(records[0]['status'],'verified')
        self.assertTrue(records[0]['writer_recovered'])
        content=json.loads(final.with_suffix('.json').read_text())['phases'][0]['tasks'][0]['content']
        self.assertEqual(content,records[0]['expected_content'])

    async def test_semantically_invalid_contract_is_repaired_once_before_writer_then_fails_closed(self):
        agents=PlanAgents()
        agents.task_reviews=[rejected('P0-T001')]
        agents.contract_reviews=[{'approved':False,'reason':'Correção contradiz o escopo aprovado.'}]*2
        engine=PlanEngine(self.state,agents)
        with self.assertRaisesRegex(PlanContractError,'contrato de correção coerente'):
            await engine.run()
        operations=[e['current_request']['task'] for _,e in agents.calls]
        self.assertEqual(operations.count('repair_correction'),1)
        self.assertNotIn('correct_task',operations)
        self.assertFalse((self.root/'.harness/plan/final.md').exists())

    async def test_schemas_only_expose_actions_allowed_for_the_role(self):
        task_schema=agent_schema(TaskAssessment)
        collective=agent_schema(CollectiveAssessment)
        structure=agent_schema(StructureRepair)
        self.assertNotIn('delegations',task_schema['properties'])
        self.assertNotIn('structural_changes',task_schema['properties'])
        self.assertNotIn('replan',collective['properties'])
        self.assertNotIn('corrections',structure['properties'])
        self.assertNotIn('delegations',collective['properties'])
        self.assertNotIn('parts',agent_schema(ClassificationReview)['$defs']['ResponsibilityChange']['properties'])

    async def test_contextual_global_evidence_refines_details_without_expanding_task_scope(self):
        task,spec,requirements=self.sample()
        spec.evidence.append({'requirement_id':'R001','quote':requirements['R001'].text})
        spec=ContentCorrection.model_validate(spec.model_dump())
        self.assertTrue(patched_content(task,spec,requirements).objective.endswith('excluir tarefas.'))
        spec.evidence=spec.evidence[1:]
        with self.assertRaisesRegex(ValueError,'ao menos um requisito atribuído'):
            patched_content(task,spec,requirements)

    async def test_scoped_schema_excludes_other_task_and_build_ids(self):
        from harness.plan_schema import scoped_schema
        task,spec,requirements=self.sample()
        requirements['P2-A001'].execution_owner='build'
        schema=scoped_schema(CollectiveAssessment,'review_phase',{
            'phase':{'number':2,'tasks':[]},'phase_source':{'number':2}},requirements)
        self.assertEqual(schema['properties']['additions']['maxItems'],0)
        requirements['P2-A001'].execution_owner='tasks'
        schema=scoped_schema(TaskAssessment,'review_task',{'task':task.model_dump(),'base_version':version(task)},requirements)
        self.assertEqual(schema['$defs']['ContentCorrection']['properties']['task_id']['enum'],[task.id])
        self.assertEqual(schema['$defs']['ContentCorrection']['properties']['base_version']['enum'],[version(task)])
        ids=schema['$defs']['SourceEvidence']['properties']['requirement_id']['enum']
        self.assertIn('P2-A001',ids)
        self.assertNotIn('P1-A001',ids)

    async def test_noop_responsibility_annotation_does_not_generate_another_agent_cycle(self):
        agents=PlanAgents()
        agents.classification_reviews=[{'status':'corrected','summary':'Responsabilidade confirmada.',
            'corrections':[{'source_id':'P1-A001','part_index':0,'responsibility':'implementation','reason':'Implementação da fundação.'}]}]
        engine=PlanEngine(self.state,agents)
        await engine.run()
        self.assertEqual(engine.contract_errors,0)
        self.assertEqual(sum(e['current_request']['task']=='review_classification' for _,e in agents.calls),1)

    async def test_modified_acceptance_or_missing_completion_certificate_cannot_enable_build(self):
        agents=PlanAgents();agents.task_reviews=[rejected('P0-T001')]
        await PlanEngine(self.state,agents).run()
        self.assertEqual(suggested_module(self.root),'build')
        contracts=self.root/'.harness/plan/control/corrections.json'
        original=contracts.read_text();data=json.loads(original)
        data['contracts'][0]['acceptance'][0]['value']='Aceite adulterado.'
        contracts.write_text(json.dumps(data))
        self.assertEqual(suggested_module(self.root),'plan')
        contracts.write_text(original)
        (self.root/'.harness/plan/control/completion-certificate.json').unlink()
        self.assertEqual(suggested_module(self.root),'plan')
