"""Reparos atômicos e pendências estáveis, sem histórico conversacional."""

from collections import Counter
import json

from .artifacts import digest
from .plan_contracts import PlanContractError
from .plan_coverage import check_assignments, check_seed, requests_structural_operation
from .plan_models import PlannedTask, TaskContent


def version(task):
    return digest(json.dumps(task.model_dump(), ensure_ascii=False, sort_keys=True))


def structure_version(phase):
    shape = sorted(((' '.join(t.seed.scope.casefold().split())),
                    sorted((c.requirement_id, ' '.join(c.excerpt.casefold().split())) for c in t.seed.coverage))
                   for t in phase.tasks)
    return digest(json.dumps((phase.number, shape), sort_keys=True))


def stub(number, seed, identifier):
    return PlannedTask(id=identifier, phase_number=number, seed=seed,
        content=TaskContent(title=seed.title, objective=seed.scope,
                           implementation_steps=[seed.scope], deliverables=[seed.title]))


def prepare_changes(phases, repair, requirements, origins, sequences):
    """Valida o lote inteiro em cópias. Nunca modifica estado em caso de erro."""
    result = [phase.model_copy(deep=True) for phase in phases]
    counters = dict(sequences)
    dirty, affected, removed = set(), set(), set()
    feedback = {}
    targets = set()

    def locate(identifier):
        for phase in result:
            for task in phase.tasks:
                if task.id == identifier:
                    return phase, task
        raise ValueError(f'Task inexistente no reparo: {identifier}.')

    def create(phase, seed):
        check_seed(seed, phase.number, origins[phase.number]['text'], requirements)
        counters[phase.number] = counters.get(phase.number, 0) + 1
        task = stub(phase.number, seed, f'P{phase.number}-T{counters[phase.number]:03d}')
        dirty.add(task.id)
        return task

    for correction in repair.corrections:
        if correction.category in ('structure', 'granularity'):
            raise ValueError('Reparo coletivo de estrutura/granularidade exige split, merge ou remove em structural_changes, '
                             'ou uma decisão explícita de limite; não envie outra solicitação de reavaliação ao escritor.')
        phase, task = locate(correction.task_id)
        if task.id in targets:
            raise ValueError('Uma task não pode receber operações conflitantes no mesmo lote.')
        targets.add(task.id)
        if requests_structural_operation(correction.instruction):
            raise ValueError('Remover uma task exige structural_changes operation=remove; não envie remoção ao escritor.')
        if correction.replacement_seed is not None:
            check_seed(correction.replacement_seed, phase.number, origins[phase.number]['text'], requirements)
            task.seed = correction.replacement_seed
        dirty.add(task.id)
        affected.add(phase.number)
        feedback[task.id] = correction.model_dump()
    for change in repair.structural_changes:
        pairs = [locate(identifier) for identifier in change.task_ids]
        phase = pairs[0][0]
        if any(p.number != phase.number for p, _ in pairs):
            raise ValueError('Uma operação estrutural só pode alterar tasks da mesma fase.')
        if targets.intersection(change.task_ids):
            raise ValueError('Consolide as operações: não corrija e substitua a mesma task no mesmo lote.')
        targets.update(change.task_ids)
        first = min(i for i, t in enumerate(phase.tasks) if t.id in change.task_ids)
        replacements = [create(phase, seed) for seed in change.replacements]
        kept = [task for task in phase.tasks if task.id not in change.task_ids]
        kept[first:first] = replacements
        if not kept and any(r.phase_number == phase.number and r.kind in ('activity', 'technology', 'tool') and r.execution_owner == 'tasks'
                            for r in requirements.values()):
            raise ValueError('Uma fase não pode ficar vazia; substitua seu recorte em vez de remover a fase.')
        phase.tasks = kept
        removed.update(change.task_ids)
        affected.add(phase.number)
    anchors = {}
    for addition in repair.additions:
        phase = next((p for p in result if p.number == addition.phase_number), None)
        if phase is None:
            raise ValueError('A adição deve apontar uma fase existente.')
        anchor = anchors.get((phase.number, addition.after_task_id), addition.after_task_id)
        if anchor and anchor not in {t.id for t in phase.tasks}:
            raise ValueError('A âncora da adição deve existir após as operações estruturais.')
        index = next((i + 1 for i, t in enumerate(phase.tasks) if t.id == anchor), 0)
        task = create(phase, addition.task)
        phase.tasks.insert(index, task)
        anchors[(phase.number, addition.after_task_id)] = task.id
        affected.add(phase.number)
    check_assignments(result, requirements)
    return result, counters, dirty, affected, removed, feedback


class IssueLedger:
    def __init__(self):
        self.issues = {}
        self.rejections = Counter()
        self.decisions = []
        self.phase_signatures = {}

    def record(self, task, correction):
        ids = sorted({claim.requirement_id for claim in task.seed.coverage})
        key = f'{task.id}|{correction.category}|' + '|'.join(ids)
        identifier = 'I-' + digest(key)[:16]
        issue = self.issues.setdefault(identifier, {'id': identifier, 'task_id': task.id,
            'category': correction.category, 'requirement_ids': ids, 'status': 'open', 'occurrences': 0, 'evidence': []})
        issue['status'] = 'open'
        issue['occurrences'] += 1
        issue['evidence'].append({'version': version(task), 'correction': correction.model_dump()})
        self.rejections[task.id] += 1
        return identifier

    def resolved(self, task):
        for issue in self.issues.values():
            if issue['task_id'] == task.id and issue['status'] in ('open', 'reassessed'):
                issue.update(status='resolved', approved_version=version(task))
        self.rejections[task.id] = 0

    def stalled(self, task):
        return self.rejections[task.id] >= 3

    def repeated_phase(self, number, verdict):
        key = json.dumps(sorted((c.task_id, c.category) for c in verdict.corrections))
        signatures = self.phase_signatures.setdefault(number, Counter())
        signatures[key] += 1
        return signatures[key] >= 2

    def context(self, task_ids):
        return {'issues': [{**{k: v for k, v in issue.items() if k != 'evidence'},
                           'recent_evidence': issue['evidence'][-3:]}
                          for issue in self.issues.values() if issue['task_id'] in task_ids],
                'decisions': list({d['issue_id']: d for d in self.decisions if d['task_id'] in task_ids}.values())}

    def decide(self, decision):
        issue = self.issues[decision.issue_id]
        if issue['task_id'] != decision.task_id:
            raise ValueError('A decisão deve pertencer à pendência e task indicadas.')
        existing = next((d for d in self.decisions if d['issue_id'] == decision.issue_id), None)
        if existing and existing['decision'] != decision.decision:
            raise PlanContractError('Uma decisão vigente não pode ser substituída silenciosamente. O conflito de escopo exige diagnóstico.')
        if not existing:
            self.decisions.append(decision.model_dump())
        self.rejections[decision.task_id] = 0
        issue['status'] = 'reassessed'

    def dump(self):
        return {'issues': list(self.issues.values()), 'decisions': self.decisions}
