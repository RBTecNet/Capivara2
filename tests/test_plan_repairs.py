import asyncio
from collections import Counter
import json
import unittest

from harness.artifacts import read_status, suggested_module
from harness.plan_artifacts import load_init
from harness.plan_control import prepare_changes, stub
from harness.plan_coverage import build_requirements, check_seed
from harness.plan_engine import PlanEngine
from harness.plan_contracts import PlanContractError
from harness.plan_models import PlanReview, TaskSeed, PlannedPhase, PhaseOutline, PlanRepair, agent_schema
import test_plan
from plan_fixture import PlanAgents, approved_review, rejected


def seed(scope, excerpt):
    return {"title": scope, "scope": scope, "source_quotes": ["Definir o esquema de tarefas."],
            "coverage": [{"requirement_id": "P1-A001", "excerpt": excerpt}]}


def structural(operation, ids, replacements):
    return {"operation": operation, "task_ids": ids, "reason": "Corrigir divisão e sobreposição de escopos.",
            "replacements": replacements}


def structural_review(changes):
    return {**approved_review(), "approved": False, "matches_source": False,
            "structural_changes": changes}


class PlanRepairTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_plan.PlanTests.asyncSetUp

    def control(self):
        engine = PlanEngine(self.state, PlanAgents())
        engine.source = load_init(self.root)
        engine.prepare_origins()
        engine.requirements = build_requirements(engine.source['document'])
        first, second = TaskSeed.model_validate(seed('Definir esquema', 'esquema')), TaskSeed.model_validate(seed('Definir campos das tarefas', 'tarefas'))
        phase = PlannedPhase(number=1, title='Persistência', goal='Guardar tarefas localmente.', depends_on=[0],
            tasks=[stub(1, first, 'P1-T001'), stub(1, second, 'P1-T002')])
        engine.phases = [phase]
        engine.task_sequences = {1: 2}
        return engine, phase

    async def test_merge_split_remove_are_structural_and_ids_are_never_reused(self):
        engine, phase = self.control()
        combined = seed('Definir esquema e campos', 'Definir o esquema de tarefas.')
        verdict = PlanReview.model_validate(structural_review([structural('merge', ['P1-T001', 'P1-T002'], [combined])]))
        result, counters, dirty, affected, removed, _ = prepare_changes(engine.phases, verdict,
            engine.requirements, engine.origins, engine.task_sequences)
        self.assertEqual(removed, {'P1-T001', 'P1-T002'})
        self.assertEqual(result[0].tasks[0].id, 'P1-T003')
        split = PlanReview.model_validate(structural_review([structural('split', ['P1-T003'],
            [seed('Definir esquema', 'esquema'), seed('Definir campos das tarefas', 'tarefas')])]))
        result, counters, *_ = prepare_changes(result, split, engine.requirements, engine.origins, counters)
        self.assertEqual([t.id for t in result[0].tasks], ['P1-T004', 'P1-T005'])
        remove = PlanReview.model_validate(structural_review([structural('remove', ['P1-T005'], [])]))
        result, counters, *_ = prepare_changes(result, remove, engine.requirements, engine.origins, counters)
        self.assertEqual([t.id for t in result[0].tasks], ['P1-T004'])
        self.assertEqual(counters[1], 5)
        self.assertEqual([t.id for t in phase.tasks], ['P1-T001', 'P1-T002'])

    async def test_invalid_batch_and_duplicate_addition_never_mutate_the_original(self):
        engine, phase = self.control()
        before = phase.model_dump()
        duplicate = PlanReview.model_validate({**approved_review(), 'approved': False, 'coverage_complete': False,
            'additions': [{'phase_number': 1, 'after_task_id': 'P1-T001', 'task': seed('Outro nome, mesmo recorte', 'esquema')}]})
        with self.assertRaisesRegex(ValueError, 'já pertence'):
            prepare_changes(engine.phases, duplicate, engine.requirements, engine.origins, engine.task_sequences)
        self.assertEqual(phase.model_dump(), before)

    async def test_reassessment_cannot_send_structural_repair_back_to_writer_as_instructions(self):
        engine, phase = self.control()
        before = phase.model_dump()
        verdict = rejected('P1-T001','Reavalie a fase e divida esta task.')
        verdict['corrections'][0]['category']='granularity'
        with self.assertRaisesRegex(ValueError,'split, merge ou remove'):
            prepare_changes(engine.phases,PlanReview.model_validate(verdict),engine.requirements,
                            engine.origins,engine.task_sequences)
        self.assertEqual([t.id for t in phase.tasks],['P1-T001','P1-T002'])
        self.assertEqual(engine.task_sequences, {1: 2})
        invalid = PlanReview.model_validate(structural_review([structural('remove', ['P1-T001', 'P1-T002'], [])]))
        with self.assertRaisesRegex(ValueError, 'vazia'):
            prepare_changes(engine.phases, invalid, engine.requirements, engine.origins, engine.task_sequences)
        self.assertEqual(phase.model_dump(), before)

    async def test_batch_transfers_responsibilities_before_any_writer_is_called(self):
        agents = PlanAgents()
        verdict = structural_review([structural('split', ['P1-T001'],
            [seed('Definir esquema', 'esquema'), seed('Definir campos das tarefas', 'tarefas')])])
        agents.task_repairs = {'P1-T001':[verdict]}
        observed = []
        engine = PlanEngine(self.state, agents)
        def observe(operation, store):
            if operation == 'write_task' and engine.repair_id:
                phase = next(p for p in engine.phases if p.number == 1)
                observed.append([(t.id, [c.excerpt for c in t.seed.coverage]) for t in phase.tasks])
        agents.on_generate = observe
        await engine.run()
        self.assertTrue(observed)
        self.assertTrue(all(items == [('P1-T002', ['esquema']), ('P1-T003', ['tarefas'])] for items in observed))

    async def test_split_retires_old_task_and_reviews_every_replacement(self):
        agents = PlanAgents()
        verdict = structural_review([structural('split', ['P1-T001'],
            [seed('Definir esquema', 'esquema'), seed('Definir campos das tarefas', 'tarefas')])])
        agents.task_repairs = {'P1-T001':[verdict]}
        engine = PlanEngine(self.state, agents)
        final = await engine.run()
        plan = json.loads(final.with_suffix('.json').read_text())
        self.assertEqual([t['id'] for t in plan['phases'][1]['tasks']], ['P1-T002', 'P1-T003'])
        self.assertNotIn('P1-T001', engine.approved_tasks)
        self.assertIn('P1-T002', engine.approved_tasks)
        self.assertIn('P1-T003', engine.approved_tasks)
        writes = Counter(e['current_request']['data']['task_id'] for role,e in agents.calls if role=='writer')
        self.assertEqual(writes['P1-T001'], 1)
        self.assertEqual(writes['P1-T002'], 1)
        self.assertEqual(writes['P1-T003'], 1)

    async def test_remove_does_not_reach_writer_as_a_rewrite(self):
        class WithExtraTask(PlanAgents):
            async def generate(self, selection, role, prompt, schema, store, call_id):
                result = await super().generate(selection, role, prompt, schema, store, call_id)
                request = self.calls[-1][1]['current_request']
                if request['task']=='outline_phase' and request['data']['phase_source']['number']==1:
                    out = json.loads(result)
                    out['tasks'].append({'title':'Resumo redundante', 'scope':'Repetir objetivo da persistência.',
                        'source_quotes':['Guardar tarefas localmente.'],
                        'coverage':[{'requirement_id':'P1-A001', 'excerpt':'tarefas'}]})
                    return json.dumps(out)
                return result
        agents = WithExtraTask()
        agents.task_repairs = {'P1-T002':[structural_review([structural('remove', ['P1-T002'], [])])]}
        engine = PlanEngine(self.state, agents)
        await engine.run()
        self.assertNotIn('P1-T002', engine.approved_tasks)
        writes = [e['current_request']['data'] for role,e in agents.calls if role=='writer']
        self.assertEqual(sum(d['task_id']=='P1-T002' for d in writes), 1)
        self.assertFalse(any(d['correction'] and 'remov' in d['correction']['instruction'].lower() for d in writes))

    async def test_repeated_rejection_returns_to_reviewer_without_another_writer_attempt(self):
        agents = PlanAgents()
        agents.task_reviews = [rejected('P0-T001', 'Preservar somente o recorte atribuído.')] * 2
        engine = PlanEngine(self.state, agents)
        await engine.run()
        operations = [e['current_request']['task'] for _,e in agents.calls]
        self.assertEqual(operations.count('correct_task'), 1)
        self.assertNotIn('reassess_task', operations)
        self.assertEqual(read_status(self.root)['modules']['plan']['status'], 'completed')
        self.assertTrue((self.root/'.harness/plan/final.md').exists())
        self.assertGreater(engine.contract_errors,0)

    async def test_reassessment_does_not_mark_tasks_approved_when_next_review_is_cancelled(self):
        agents = PlanAgents()
        concern = rejected('P0-T001')
        concern['corrections'][0]['category']='structure'
        agents.task_reviews = [concern]
        engine = PlanEngine(self.state, agents)
        def cancel(operation, store):
            if operation == 'review_task' and engine.ledger.decisions:
                raise asyncio.CancelledError()
        agents.on_generate = cancel
        with self.assertRaises(asyncio.CancelledError):
            await engine.run()
        self.assertNotIn('P0-T001', engine.approved_tasks)
        self.assertEqual(read_status(self.root)['modules']['plan']['status'], 'cancelled')
        self.assertFalse((self.root/'.harness/plan/final.md').exists())

    async def test_final_consolidation_does_not_reopen_approved_tasks(self):
        agents = PlanAgents()
        agents.plan_reviews = [rejected('P2-T001', 'Reabrir indevidamente uma task aprovada.')]
        engine = PlanEngine(self.state, agents)
        await engine.run()
        operations = [e['current_request']['task'] for _,e in agents.calls]
        self.assertNotIn('validate_completion',operations)
        self.assertNotIn('review_phase',operations)
        self.assertNotIn('review_plan',operations)
        self.assertNotIn('correct_task',operations)
        self.assertEqual(len(agents.plan_reviews),1)

    async def test_all_checks_true_cannot_be_rejected_for_editorial_preferences(self):
        bad = {**approved_review(), 'approved': False,
               'corrections': rejected('P1-T001', 'Troque apenas o estilo da redação.')['corrections']}
        with self.assertRaisesRegex(ValueError, 'todos os critérios'):
            PlanReview.model_validate(bad)

    async def test_structured_output_schemas_require_all_fields_recursively(self):
        def inspect(node):
            if isinstance(node, dict):
                self.assertNotIn('default', node)
                if 'properties' in node:
                    self.assertEqual(set(node['required']), set(node['properties']))
                for value in node.values():
                    inspect(value)
            elif isinstance(node, list):
                for value in node:
                    inspect(value)
        for model in (PlanReview, PhaseOutline, PlanRepair):
            inspect(agent_schema(model))

    async def test_canonical_source_quotes_do_not_loop_on_markdown_formatting(self):
        engine, _ = self.control()
        requirement = engine.requirements['P0-S001']
        canonical = requirement.text
        self.assertNotIn(canonical, engine.origins[0]['text'])
        task_seed = TaskSeed(title='Runtime', scope='Documentar runtime aprovado.', source_quotes=[canonical],
            coverage=[{'requirement_id': requirement.id, 'excerpt': 'Python'}])
        check_seed(task_seed, 0, engine.origins[0]['text'], engine.requirements)
        task_seed.source_quotes = ['Uma stack inventada sem referência.']
        with self.assertRaisesRegex(ValueError, 'Citação ausente'):
            check_seed(task_seed, 0, engine.origins[0]['text'], engine.requirements)
