"""Restringe as respostas aos IDs, versões e ações válidos no recorte atual."""

from .plan_models import agent_schema


def scoped_schema(model, operation, data, requirements):
    schema = agent_schema(model)
    definitions = schema.get('$defs', {})
    if operation in ('write_task', 'correct_task', 'validate_correction', 'validate_completion'):
        return schema
    if operation in ('classify_work','review_classification'):
        ids = [a['source_id'] for a in data['activities']]
        for name in ('ClassifiedActivity','ResponsibilityChange'):
            if name in definitions and ids:
                definitions[name]['properties']['source_id']['enum'] = ids
        return schema
    if 'task' in data:
        tasks = [data['task']]
    elif 'phase' in data:
        tasks = data['phase']['tasks']
    else:
        tasks = [t for phase in data.get('phases', []) for t in phase.get('tasks', [])]
    phase_numbers = {data['phase_source']['number']} if 'phase_source' in data else {t['phase_number'] for t in tasks}
    owned = [r.id for r in requirements.values() if r.phase_number in phase_numbers and r.execution_owner == 'tasks']
    if operation=='reassess_task':
        owned=list({c['requirement_id'] for t in tasks for c in t['seed']['coverage']})
        if 'StructuralChange' in definitions:
            definitions['StructuralChange']['properties']['operation']['enum']=['split','remove']
    if 'CoverageClaim' in definitions and owned:
        definitions['CoverageClaim']['properties']['requirement_id']['enum'] = owned
    if 'tasks' in schema.get('properties', {}) and operation == 'outline_phase' and not owned:
        schema['properties']['tasks']['maxItems'] = 0
    task_ids = [t['id'] for t in tasks]
    versions = [data['base_version']] if 'base_version' in data else [data.get('approved_versions', {}).get(t['id']) for t in tasks]
    versions = [v for v in versions if v]
    if 'ContentCorrection' in definitions and task_ids:
        definitions['ContentCorrection']['properties']['task_id']['enum'] = task_ids
        if versions:
            definitions['ContentCorrection']['properties']['base_version']['enum'] = versions
    if 'task_id' in schema.get('properties', {}) and task_ids:
        schema['properties']['task_id']['enum'] = task_ids
        if 'base_version' in schema['properties'] and versions:
            schema['properties']['base_version']['enum'] = versions
    if 'StructureConcern' in definitions and task_ids:
        definitions['StructureConcern']['properties']['task_id']['enum'] = task_ids
    evidence = sorted({c['requirement_id'] for task in tasks for c in task['seed']['coverage']} |
                      {r.id for r in requirements.values() if r.phase_number == -1})
    if 'SourceEvidence' in definitions and evidence:
        definitions['SourceEvidence']['properties']['requirement_id']['enum'] = evidence
    if 'StructuralChange' in definitions and task_ids:
        definitions['StructuralChange']['properties']['task_ids']['items']['enum'] = task_ids
    if 'TaskAddition' in definitions and phase_numbers:
        definitions['TaskAddition']['properties']['phase_number']['enum'] = sorted(phase_numbers)
    if 'ScopeDecision' in definitions:
        issues = data.get('scope_decisions', {}).get('issues', [])
        if issues:
            definitions['ScopeDecision']['properties']['issue_id']['enum'] = [i['id'] for i in issues]
    if operation in ('review_phase','reassess_phase') and not owned:
        for name in ('corrections','structural_changes','additions'):
            if name in schema.get('properties', {}):
                schema['properties'][name]['maxItems'] = 0
    return schema
