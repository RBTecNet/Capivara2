"""Uma emissão e uma aplicação por conflito, com aceite estável por versão."""

from .plan_contracts import PlanContractError, correction_id, patched_content
from .plan_control import version


class CorrectionBook:
    def __init__(self, source_hash=''):
        self.source_hash = source_hash
        self.records = {}

    def check(self, task, spec, requirements):
        identifier = correction_id(spec)
        if identifier in self.records:
            raise PlanContractError(f'{identifier}: o mesmo conflito de {task.id} já recebeu uma correção. '
                                    'A revisão repetiu ou reabriu uma exigência; não será reenviado ao escritor.')
        assigned = {claim.requirement_id for claim in task.seed.coverage}
        primary = {e.requirement_id for e in spec.evidence} & assigned
        fields = {change.field for change in spec.changes}
        for record in self.active(task.id):
            previous = record['contract']
            prior_primary = {e['requirement_id'] for e in previous['evidence']} & assigned
            if primary & prior_primary and fields & {change['field'] for change in previous['changes']}:
                raise PlanContractError(f'{record["id"]}: o mesmo conflito de {task.id} reapareceu em campos e requisitos '
                                        'já corrigidos, com outra categoria ou condição. Não será reenviado ao escritor.')
        target = patched_content(task, spec, requirements)
        for record in self.active(task.id):
            from .plan_contracts import AcceptanceCondition
            if any(not AcceptanceCondition.model_validate(c).satisfied(target) for c in record['acceptance']):
                raise PlanContractError(f'{task.id}: a nova devolução contradiz o aceite já estabelecido em {record["id"]}.')
        return target

    def issue(self, task, spec, requirements):
        target = self.check(task, spec, requirements)
        identifier = correction_id(spec)
        self.records[identifier] = {'id': identifier, 'task_id': task.id, 'source_prompt_sha256': self.source_hash,
            'base_version': version(task), 'contract': spec.model_dump(), 'acceptance': [c.model_dump() for c in spec.acceptance],
            'expected_content': target.model_dump(), 'status': 'issued', 'writer_recovered': False}
        return identifier, target

    def applied(self, identifier, task, recovered=False):
        record = self.records[identifier]
        if record['status'] != 'issued':
            raise PlanContractError(f'{identifier}: tentativa de aplicar o mesmo contrato novamente.')
        if task.content.model_dump() != record['expected_content']:
            raise PlanContractError(f'{identifier}: a saída não corresponde às substituições aprovadas.')
        record.update(status='applied', applied_version=version(task), writer_recovered=recovered)

    def active(self, task_id):
        return [r for r in self.records.values() if r['task_id'] == task_id and r['status'] in ('applied','verified')]

    def verify(self, task, *, approved=False):
        from .plan_contracts import AcceptanceCondition
        for record in self.active(task.id):
            if any(not AcceptanceCondition.model_validate(c).satisfied(task.content) for c in record['acceptance']):
                raise PlanContractError(f'{task.id}: uma correção aplicada regrediu, violando {record["id"]}.')
            if approved:
                record.update(status='verified', verified_version=version(task))

    def retire(self, identifiers):
        for record in self.records.values():
            if record['task_id'] in identifiers:
                record['status'] = 'retired'

    def context(self, ids):
        return [{key: record[key] for key in ('id','task_id','acceptance','applied_version','status')}
                for record in self.records.values() if record['task_id'] in ids and record['status'] in ('applied','verified')]

    def dump(self):
        return {'source_prompt_sha256': self.source_hash, 'contracts': list(self.records.values())}
