"""Comprovação por task; a consolidação pertence ao controlador, sem outra chamada LLM."""
from .artifacts import digest
from .plan_contracts import TaskAssessment
from .plan_control import version


def task_view(task):
    data=task.model_dump()
    data['seed'].pop('source_quotes',None)
    return data


def approval_path(task):
    return f'phases/{task.phase_number:02d}/task-approvals/{task.id}.json'


def write_task_approval(engine,task,review,operation=None):
    raw=review.model_dump()
    raw={key:raw[key] for key in ('approved','matches_source','coverage_complete','tasks_are_small','no_test_strategy','summary','corrections')}
    raw['replan']=None
    review=TaskAssessment.model_validate(raw)
    engine.store.json(approval_path(task),{'task_id':task.id,'version':version(task),
        'source_prompt_sha256':engine.source['source_prompt_sha256'],
        'review':review.model_dump(),'operation':operation})


def recorded_approval(engine,task):
    expected=version(task)
    saved=engine.store.read_json(approval_path(task),{})
    if (saved.get('version')==expected and saved.get('source_prompt_sha256')==engine.source['source_prompt_sha256']):
        try:
            if TaskAssessment.model_validate(saved['review']).approved:return True
        except (ValueError,KeyError):pass
    for record in engine.task_approval_records.get(expected,[]):
        if record.get('data',{}).get('task')!=task_view(task):continue
        try:review=engine.journal.cached(record['key'],TaskAssessment)
        except ValueError:continue
        if review and review.approved:
            write_task_approval(engine,task,review,record['key'])
            return True
    # Compatibilidade com execuções antigas cujos recibos ainda não existiam.
    from .response_adapter import decode_response
    from .plan_adapter import normalize_review
    for data,path in engine.legacy_task_approvals.get(expected,[]):
        if data.get('task')!=task_view(task):continue
        try:
            raw,_=normalize_review(decode_response(path.read_text()),TaskAssessment,data,engine.requirements)
            review=TaskAssessment.model_validate(raw)
        except ValueError:continue
        if review.approved:
            write_task_approval(engine,task,review)
            return True
    return False


def phase_certificate(engine,phase):
    versions={task.id:version(task) for task in phase.tasks}
    return {'producer':'controller','source_prompt_sha256':engine.source['source_prompt_sha256'],
        'phase':phase.number,'approved_versions':versions,
        'task_certificates':{task.id:digest(engine.store.path(approval_path(task)).read_text()) for task in phase.tasks}}
