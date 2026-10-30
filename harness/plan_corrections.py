"""Uma emissão e uma aplicação por conflito, com aceite estável por versão."""

from .plan_contracts import PlanContractError, correction_id, patched_content
from .plan_control import version
from .plan_acceptance import effective_conditions
from .plan_references import rewrite
from .recovery import fingerprint


class CorrectionBook:
    def __init__(self, source_hash=''):
        self.source_hash = source_hash
        self.records = {}

    def identifier(self, spec):
        base = correction_id(spec)
        if base not in self.records:
            return base
        old = self.records[base]['contract']
        if old['changes'] == [c.model_dump() for c in spec.changes]:
            return base
        return base + '-' + fingerprint([c.model_dump() for c in spec.changes])[:12]

    def check(self, task, spec, requirements):
        identifier = self.identifier(spec)
        if identifier in self.records:
            raise PlanContractError(f'{identifier}: o mesmo conflito de {task.id} já recebeu uma correção. '
                                    'A revisão repetiu ou reabriu uma exigência; não será reenviado ao escritor.')
        target = patched_content(task, spec, requirements)
        for record in self.active(task.id):
            from .plan_contracts import AcceptanceCondition
            if any(not AcceptanceCondition.model_validate(c).satisfied(target) for c in record['acceptance']):
                raise PlanContractError(f'{task.id}: a nova devolução contradiz o aceite já estabelecido em {record["id"]}.')
        return target

    def issue(self, task, spec, requirements):
        target = self.check(task, spec, requirements)
        identifier = self.identifier(spec)
        self.records[identifier] = {'id': identifier, 'task_id': task.id, 'source_prompt_sha256': self.source_hash,
            'base_version': version(task), 'contract': spec.model_dump(), 'acceptance': effective_conditions(spec),
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

    def rebind(self, bindings, retired=()):
        for record in self.records.values():
            if record['status'] not in ('applied', 'verified'):
                continue
            # Uma decisão estrutural pode tornar um literal de ID obsoleto mesmo
            # quando há vários sucessores. O histórico fica; o documento volta à revisão.
            obsolete = {identifier:'' for identifier in retired if identifier not in bindings}
            updated = [{**c, 'value':rewrite(c['value'], bindings)} for c in record['acceptance']
                       if rewrite(c['value'], obsolete) == c['value']]
            if updated != record['acceptance']:
                record.setdefault('reference_history', []).append({'bindings':dict(bindings), 'retired':sorted(retired), 'before':record['acceptance']})
                record['acceptance'] = updated

    def context(self, ids):
        return [{key: record[key] for key in ('id','task_id','acceptance','applied_version','status')}
                for record in self.records.values() if record['task_id'] in ids and record['status'] in ('applied','verified')]

    def dump(self):
        return {'source_prompt_sha256': self.source_hash, 'contracts': list(self.records.values())}
