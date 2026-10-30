"""Contrato simples para modelos e tradução determinística para os artefatos do plan."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, ConfigDict

from .init_models import DocumentModel
from .plan_contracts import TaskAssessment, CollectiveAssessment, StructureConcern
from .plan_models import StructuralChange, TaskAddition


class TextEdit(DocumentModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    field: Literal['title', 'objective', 'implementation_steps', 'deliverables']
    find: str
    replace: str


class Finding(DocumentModel):
    task_id: str
    category: Literal['fidelity', 'coverage', 'granularity', 'test_strategy', 'editorial']
    reason: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    requirement_ids: list[str]
    edits: list[TextEdit]


class TaskReviewResponse(DocumentModel):
    summary: str = Field(min_length=1)
    findings: list[Finding]
    replan: StructureConcern | None = None


class PhaseReviewResponse(DocumentModel):
    summary: str = Field(min_length=1)
    findings: list[Finding]
    structural_changes: list[StructuralChange] = Field(default_factory=list)
    additions: list[TaskAddition] = Field(default_factory=list)


REVIEW_MODELS = (TaskAssessment, CollectiveAssessment)


def wire_schema(model, data, requirements):
    wire = TaskReviewResponse if model is TaskAssessment else PhaseReviewResponse
    from .plan_schema import scoped_schema
    schema = scoped_schema(wire, 'review_task' if model is TaskAssessment else 'review_phase', data, requirements)
    tasks = task_map(data)
    finding = schema['$defs']['Finding']['properties']
    if tasks:
        finding['task_id']['enum'] = list(tasks)
    else:
        schema['properties']['findings']['maxItems'] = 0
    ids = sorted({c['requirement_id'] for t in tasks.values() for c in t['seed']['coverage']} |
                 {r.id for r in requirements.values() if r.phase_number == -1})
    if ids:
        finding['requirement_ids']['items']['enum'] = ids
    return schema


def task_map(data):
    if 'task' in data:
        return {data['task']['id']: data['task']}
    return {t['id']: t for p in ([data['phase']] if 'phase' in data else data.get('phases', [])) for t in p['tasks']}


def compact_context(operation, data):
    """O pedido integral continua no disco; cada chamada recebe só seu recorte."""
    result = deepcopy(data)
    source = result.get('source')
    if source and operation not in ('validate_completion',):
        document = source['document']
        result['source'] = {'source_prompt_sha256': source['source_prompt_sha256'], 'document': {
            k: document[k] for k in ('objective', 'constraints', 'requirements', 'stack', 'tools') if k in document}}
        if operation in ('classify_work', 'review_classification'):
            result['source']['document'].pop('stack', None)
            result['source']['document'].pop('tools', None)
    tasks = task_map(result)
    if tasks and 'approved_versions' in result:
        result['approved_versions'] = {k: v for k, v in result['approved_versions'].items() if k in tasks}
    return result


def apply_edits(content, edits):
    target = deepcopy(content)
    for edit in edits:
        value = target[edit.field]
        if not edit.find:
            target[edit.field] = value + [edit.replace] if isinstance(value, list) else value + edit.replace
        else:
            values = value if isinstance(value, list) else [value]
            if sum(item.count(edit.find) for item in values) != 1:
                raise ValueError(f'{edit.field}: find deve identificar exatamente um trecho atual; não adivinhar o texto.')
            replaced = [item.replace(edit.find, edit.replace, 1) for item in values]
            target[edit.field] = replaced if isinstance(value, list) else replaced[0]
    return target


def correction(finding, task, requirements, base_version):
    if not base_version:
        raise ValueError('A versão atual da task deve ser fornecida pelo controlador.')
    if finding.category == 'granularity':
        raise ValueError('Divisão/consolidação exige replan ou structural_changes; não uma edição de conteúdo.')
    if not finding.edits:
        raise ValueError('Problema funcional exige edits concretos, com find/replace.')
    target = apply_edits(task['content'], finding.edits)
    changes, acceptance = [], []
    for field in dict.fromkeys(e.field for e in finding.edits):
        before, after = task['content'][field], target[field]
        old = '\n'.join(before) if isinstance(before, list) else before
        new = '\n'.join(after) if isinstance(after, list) else after
        if before == after:
            raise ValueError('A edição não altera o documento.')
        changes.append({'field': field, 'before': before, 'after': after})
        acceptance.append({'field': field, 'operator': 'contains' if new and new not in old else 'excludes',
                           'value': new if new and new not in old else old})
    evidence = []
    for identifier in finding.requirement_ids:
        if identifier not in requirements:
            raise ValueError('Requisito desconhecido: ' + identifier)
        evidence.append({'requirement_id': identifier, 'quote': requirements[identifier].text})
    return {'task_id': task['id'], 'base_version': base_version,
            'category': finding.category, 'reason': finding.reason, 'instruction': finding.instruction,
            'evidence': evidence, 'changes': changes, 'acceptance': acceptance}


def normalize_review(raw, model, data, requirements):
    """Retorna contrato canônico e observações, sem converter contradição em aprovação."""
    tasks = task_map(data)
    if 'findings' not in raw:
        # Compatibilidade com respostas/checkpoints anteriores. Só adaptar patches inequívocos.
        result = deepcopy(raw)
        for spec in result.get('corrections', []):
            task = tasks.get(spec.get('task_id'))
            if not task:
                continue
            for change in spec.get('changes', []):
                current = task['content'].get(change.get('field'))
                if isinstance(current, list) and isinstance(change.get('before'), str) and isinstance(change.get('after'), str):
                    target = apply_edits(task['content'], [TextEdit(field=change['field'], find=change['before'], replace=change['after'])])
                    change.update(before=current, after=target[change['field']])
        return result, []
    response = (TaskReviewResponse if model is TaskAssessment else PhaseReviewResponse).model_validate(raw)
    editorial, functional = [], []
    grouped = {}
    for finding in response.findings:
        if finding.task_id not in tasks:
            raise ValueError('O problema deve apontar uma task deste recorte.')
        if finding.category == 'editorial':
            editorial.append(finding.model_dump())
        else:
            functional.append(finding)
            grouped.setdefault(finding.task_id, []).append(finding)
    corrections = []
    for task_id, findings in grouped.items():
        first = findings[0].model_copy(deep=True)
        first.edits = [e for f in findings for e in f.edits]
        first.requirement_ids = list(dict.fromkeys(r for f in findings for r in f.requirement_ids))
        first.reason = '\n'.join(f.reason for f in findings)
        first.instruction = '\n'.join(f.instruction for f in findings)
        base_version = data.get('base_version') or data.get('approved_versions', {}).get(task_id)
        corrections.append(correction(first, tasks[task_id], requirements, base_version))
    extra = {'replan': response.replan.model_dump() if response.replan else None} if model is TaskAssessment else {
        'structural_changes': [c.model_dump() for c in response.structural_changes],
        'additions': [a.model_dump() for a in response.additions]}
    actions = bool(corrections or extra.get('replan') or extra.get('structural_changes') or extra.get('additions'))
    return {'approved': not actions, 'matches_source': not any(f.category == 'fidelity' for f in functional) and not bool(extra.get('replan') or extra.get('structural_changes')),
            'coverage_complete': not any(f.category == 'coverage' for f in functional) and not bool(extra.get('additions')),
            'tasks_are_small': not any(f.category == 'granularity' for f in functional),
            'no_test_strategy': not any(f.category == 'test_strategy' for f in functional),
            'summary': response.summary, 'corrections': corrections, **extra}, editorial
