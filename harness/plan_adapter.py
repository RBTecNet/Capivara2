"""Contrato simples para modelos e tradução determinística para os artefatos do plan."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, ConfigDict

from .init_models import DocumentModel
from .plan_contracts import TaskAssessment, CollectiveAssessment, StructureConcern
from .plan_models import StructuralChange, TaskAddition
from .plan_acceptance import change_conditions


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


class ReplanRequest(DocumentModel):
    task_id: str
    reason: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)


class TaskReviewResponse(DocumentModel):
    summary: str = Field(min_length=1)
    findings: list[Finding]
    replan: ReplanRequest | None = None


class PhaseReviewResponse(DocumentModel):
    summary: str = Field(min_length=1)
    findings: list[Finding]
    structural_changes: list[StructuralChange] = Field(default_factory=list)
    additions: list[TaskAddition] = Field(default_factory=list)


REVIEW_MODELS = (TaskAssessment, CollectiveAssessment)


def stable_context(data):
    """O formato do aceite não muda uma aprovação da mesma versão documental."""
    result = deepcopy(data)
    for contract in result.get('correction_contracts', []):
        contract.pop('acceptance', None)
    return result


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
    concern = schema['$defs'].get('ReplanRequest')
    if concern and tasks:
        concern['properties']['task_id']['enum'] = list(tasks)
        concern['properties']['requirement_ids']['items']['enum'] = sorted({
            c['requirement_id'] for task in tasks.values() for c in task['seed']['coverage']})
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
    for issue in result.get('scope_decisions', {}).get('issues', []):
        issue['recent_evidence'] = [{'version':entry['version'], 'correction':{
            k:v for k,v in entry['correction'].items() if k in ('category','reason','instruction')}}
            for entry in issue.get('recent_evidence', [])]
    # A origem completa e a versão canônica continuam no controlador/journal.
    # No pedido, remover apenas repetições exatas já disponíveis por ID.
    assigned={r['id']:r for r in result.get('assigned_requirements',[])}
    current=result.get('task')
    if current:
        for entry in result.get('responsibility_map',[]):
            requirement=entry['requirement']
            if requirement['id'] in assigned and requirement==assigned[requirement['id']]:
                entry['requirement']={'id':requirement['id']}
            for owner in entry['owners']:
                if owner['task_id']==current['id'] and owner.get('scope')==current['seed']['scope']:
                    owner.pop('scope',None)
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
    if not finding.edits:
        raise ValueError('Problema funcional exige edits concretos, com find/replace.')
    target = apply_edits(task['content'], finding.edits)
    changes, acceptance = [], []
    for field in dict.fromkeys(e.field for e in finding.edits):
        before, after = task['content'][field], target[field]
        if before == after:
            raise ValueError('A edição não altera o documento.')
        changes.append({'field': field, 'before': before, 'after': after})
        acceptance.extend(change_conditions(field, before, after))
    evidence = []
    for identifier in finding.requirement_ids:
        if identifier not in requirements:
            raise ValueError('Requisito desconhecido: ' + identifier)
        evidence.append({'requirement_id': identifier, 'quote': requirements[identifier].text})
    return {'task_id': task['id'], 'base_version': base_version,
            'category': 'fidelity' if finding.category == 'granularity' else finding.category,
            'reason': finding.reason, 'instruction': finding.instruction,
            'evidence': evidence, 'changes': changes, 'acceptance': acceptance}


def structural_concern(request, tasks, requirements):
    task = tasks.get(request.task_id)
    if task is None:
        raise ValueError('O replanejamento deve apontar uma task deste recorte.')
    assigned = {c['requirement_id'] for c in task['seed']['coverage']}
    evidence = []
    for identifier in dict.fromkeys(request.requirement_ids):
        requirement = requirements.get(identifier)
        if requirement is None or (identifier not in assigned and requirement.phase_number != -1):
            raise ValueError('O replanejamento deve apontar requisitos atribuídos à task.')
        if identifier in assigned:
            evidence.append({'requirement_id': identifier, 'quote': requirement.text})
    if not evidence:
        raise ValueError('O replanejamento deve apontar ao menos um requisito atribuído à task.')
    return StructureConcern(task_id=request.task_id, reason=request.reason, evidence=evidence).model_dump()


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
    raw = deepcopy(raw)
    # Respostas de versões anteriores já gravadas podem conter citações em replan.
    # Recuperar os IDs explícitos; os bytes canônicos pertencem ao controlador.
    old_concern = raw.get('replan')
    if model is TaskAssessment and isinstance(old_concern, dict) and 'evidence' in old_concern:
        legacy = StructureConcern.model_validate({k:v for k,v in old_concern.items() if k != 'requirement_ids'})
        identifiers = list(dict.fromkeys(e.requirement_id for e in legacy.evidence))
        if 'requirement_ids' in old_concern:
            request = ReplanRequest.model_validate({k:v for k,v in old_concern.items() if k != 'evidence'})
            if set(request.requirement_ids) != set(identifiers):
                raise ValueError('O replanejamento informa requisitos contraditórios.')
        old_concern.pop('evidence')
        old_concern['requirement_ids'] = identifiers
    response = (TaskReviewResponse if model is TaskAssessment else PhaseReviewResponse).model_validate(raw)
    extra = {'replan': structural_concern(response.replan, tasks, requirements) if response.replan else None} if model is TaskAssessment else {
        'structural_changes': [c.model_dump() for c in response.structural_changes],
        'additions': [a.model_dump() for a in response.additions]}
    structural_tasks = ({extra['replan']['task_id']} if extra.get('replan') else set()) | {
        identifier for change in extra.get('structural_changes', []) for identifier in change['task_ids']}
    editorial, functional = [], []
    grouped = {}
    for finding in response.findings:
        if finding.task_id not in tasks:
            raise ValueError('O problema deve apontar uma task deste recorte.')
        if finding.category == 'editorial':
            editorial.append(finding.model_dump())
        elif finding.edits:
            if finding.task_id in structural_tasks:
                raise ValueError('A mesma task recebeu edições de conteúdo e replanejamento. Escolha uma ação coerente; não é necessário replanejar para corrigir o texto dentro do escopo atual.')
            functional.append(finding)
            grouped.setdefault(finding.task_id, []).append(finding)
        elif finding.task_id in structural_tasks:
            # O finding explica a ação estrutural já fornecida; não é outro patch.
            continue
        elif finding.category == 'granularity' and model is TaskAssessment:
            request = ReplanRequest(task_id=finding.task_id, reason=finding.reason + '\n' + finding.instruction,
                                    requirement_ids=finding.requirement_ids)
            extra['replan'] = structural_concern(request, tasks, requirements)
            structural_tasks.add(finding.task_id)
        else:
            raise ValueError('Problema funcional sem ação: forneça edits para conteúdo ou uma ação estrutural para a task indicada.')
    corrections = []
    if structural_tasks & grouped.keys():
        raise ValueError('A mesma task recebeu edições de conteúdo e replanejamento. Escolha uma ação coerente; não é necessário replanejar para corrigir o texto dentro do escopo atual.')
    for task_id, findings in grouped.items():
        first = findings[0].model_copy(deep=True)
        first.edits = [e for f in findings for e in f.edits]
        first.requirement_ids = list(dict.fromkeys(r for f in findings for r in f.requirement_ids))
        first.reason = '\n'.join(f.reason for f in findings)
        first.instruction = '\n'.join(f.instruction for f in findings)
        base_version = data.get('base_version') or data.get('approved_versions', {}).get(task_id)
        corrections.append(correction(first, tasks[task_id], requirements, base_version))
    actions = bool(corrections or extra.get('replan') or extra.get('structural_changes') or extra.get('additions'))
    return {'approved': not actions, 'matches_source': not any(f.category in ('fidelity', 'granularity') for f in functional) and not bool(structural_tasks),
            'coverage_complete': not any(f.category == 'coverage' for f in functional) and not bool(extra.get('additions')),
            'tasks_are_small': not bool(structural_tasks),
            'no_test_strategy': not any(f.category == 'test_strategy' for f in functional),
            'summary': response.summary, 'corrections': corrections, **extra}, editorial
