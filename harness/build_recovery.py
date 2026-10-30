"""Detecção persistente de estagnação e diagnóstico separado da redação e do aceite."""
import json

from .artifacts import digest
from .build_models import BuildDiagnosis
from .build_review import requirement_quotes
from .build_workspace import relative_path


class DiagnosisPending(Exception):
    pass


def execution_brief(engine,key):
    task=engine.tasks[key]
    return {'task_id':key,'implementation':task['content'],
        'source_policy':'TaskContent define a entrega desta unidade. Seed, citações e requisitos globais são referências; não são ordens para implementar toda a fase.',
        'quality_policy':'O harness define e executa as verificações G3. Uma referência genérica a testes próprios não exige criar suítes no G0.',
        'other_owners':[{'task_id':k,'title':t['content']['title']} for k,t in engine.tasks.items()
                        if k!=key and t['phase_number']==task['phase_number']],
        'reservation_policy':'Solicitar scope_requests antes de editar pontos de montagem fora de writes. Não entregar patches não aplicados como integração concluída.'}


def record_failure(engine,key,gate,feedback,tree):
    p=engine.progress[key]
    record={'gate':gate,'attempt':p.get('attempts',0),'tree':digest(json.dumps(tree,sort_keys=True)),
            'feedback':feedback}
    p.setdefault('failure_history',[]).append(record)
    p['failure_history']=p['failure_history'][-8:]
    recent=p['failure_history'][-2:]
    if gate==3 or len(recent)==2 and all(r['gate']==gate for r in recent):p['diagnosis_pending']=True


def request_scope(engine,key,files,reason):
    p=engine.progress[key]
    p.update(stage='scope_repair',scope_files=list(dict.fromkeys([*p.get('scope_files',[]),*files])),
             force_serial=True,candidate_result=None)
    engine.announce('scope_recovery',f'{key}: reserva solicitada antes da correção: '+', '.join(files),task_id=key,reason=reason)
    engine.checkpoint()


async def diagnose(engine,key,workspace):
    p=engine.progress[key]
    p['diagnosis_sequence']=p.get('diagnosis_sequence',0)+1
    data={**engine.code_context(key,workspace,p.get('feedback')),
        'diagnosis_sequence':p['diagnosis_sequence'],'recent_failures':p.get('failure_history',[]),
        'failed_contract':p.get('failed_contract'),'available_files':sorted(workspace.changes()[1]),
        'verification':p.get('verification'),'frozen_checks':{n:v.decode('utf-8',errors='replace') for n,v in workspace.frozen_checks.items()}}
    quotes=requirement_quotes(data)
    def validate(value):
        if value.requirement_quote not in '\n'.join(quotes):raise ValueError('O diagnóstico precisa citar literalmente o requisito desta task.')
        def texts(value):
            if isinstance(value,str):return [value]
            if isinstance(value,dict):return [s for v in value.values() for s in texts(v)]
            if isinstance(value,list):return [s for v in value for s in texts(v)]
            return []
        evidence=texts({'history':data['recent_failures'],'feedback':data.get('feedback'),'failed_contract':data['failed_contract']})
        if not any(value.failure_evidence in text for text in evidence):raise ValueError('Cite literalmente um trecho da falha/feedback recebido para justificar a causa.')
        for path in value.required_files:relative_path(path)
        if value.action=='expand_scope' and not value.required_files:raise ValueError('Expansão precisa listar os caminhos exatos que estão faltando.')
        if value.action=='repair_verification' and not p.get('verification'):
            raise ValueError('Não há verificação congelada a reparar; diagnostique a criação do contrato ou o código.')
    engine.announce('diagnosing',f'{key}: diagnóstico independente da repetição, sem aprovar a task.',task_id=key)
    value=await engine.invoke('reviewer','diagnose',data,BuildDiagnosis,workspace.root,validate)
    workspace.restore_checks()
    p['diagnosis']=value.model_dump();p['diagnosis_pending']=False;p.pop('failed_contract',None)
    engine.store.json(f'tasks/{key}/diagnoses/{p["diagnosis_sequence"]:04d}.json',value.model_dump())
    p['feedback']={**(p.get('feedback') or {}),'diagnosis':value.model_dump()}
    p['failure_history']=[]
    engine.checkpoint()
    return value


async def repair_verification(engine,key,workspace,previous,data,validate):
    """Reparar o instrumento de medição exige um aceite independente, nunca alterar a aplicação."""
    import base64
    from .build_models import VerificationSpec,VerificationRepairReview
    p=engine.progress[key];original=dict(workspace.frozen_checks);feedback=None
    while True:
        revision=p.get('verification_revision',0)+1;p['verification_revision']=revision
        directory=f'tasks/{key}/verification-repairs/{revision:04d}'
        engine.store.json(directory+'/previous.json',previous.model_dump())
        engine.store.json(directory+'/original-checks.json',{n:base64.b64encode(v).decode() for n,v in original.items()})
        engine.checkpoint()
        accepted=False
        try:
            def check(candidate):
                validate(candidate)
                # O aceite é a funcionalidade da task, não a forma do check anterior.
                # A revisão abaixo compara a candidata ao pedido, seguida de execução real.
            proposal=await engine.invoke('reviewer','verification',{**data,'verification_repair':{
                'previous':previous.model_dump(),'diagnosis':p.get('diagnosis'),
                'revision':revision,'review_feedback':feedback,
                'original_checks':{n:v.decode('utf-8',errors='replace') for n,v in original.items()}}},VerificationSpec,workspace.root,check)
            candidate={}
            for path in (workspace.root/'.harness/checks').rglob('*'):
                if path.is_file() and not path.is_symlink() and 'tmp' not in path.relative_to(workspace.root/'.harness/checks').parts:
                    candidate[path.relative_to(workspace.root).as_posix()]=path.read_bytes()
            engine.store.json(directory+'/candidate.json',proposal.model_dump())
            engine.store.json(directory+'/candidate-checks.json',{n:base64.b64encode(v).decode() for n,v in candidate.items()})
            review=await engine.invoke('reviewer','review_verification_repair',{
                **engine.code_context(key,workspace,p.get('feedback')),'previous':previous.model_dump(),
                'candidate':proposal.model_dump(),'revision':revision,'diagnosis':p.get('diagnosis'),
                'original_checks':{n:v.decode('utf-8',errors='replace') for n,v in original.items()},
                'candidate_checks':{n:v.decode('utf-8',errors='replace') for n,v in candidate.items()}},
                VerificationRepairReview,workspace.root)
            engine.store.json(directory+'/review.json',{**review.model_dump(),
                'candidate_sha256':digest(json.dumps(proposal.model_dump(),sort_keys=True)),
                'checks_sha256':digest(json.dumps({n:base64.b64encode(v).decode() for n,v in candidate.items()},sort_keys=True))})
            if not review.approved:
                feedback=review.reason
                await engine.recover('verification_repair',f'{key}: reparo dos checks devolvido: {feedback}',revision,key)
                continue
            p['verification_repair_ready']=directory
            engine.checkpoint()
            workspace.frozen_checks=candidate;workspace.save()
            p.update(verification=proposal.model_dump(),check_results={},probe_repair_pending=False)
            engine.store.json(f'tasks/{key}/verification.json',proposal.model_dump())
            p.pop('verification_repair_ready',None)
            engine.checkpoint()
            accepted=True
            engine.announce('verification_repaired',f'{key}: instrumento de teste corrigido e revisado; executando novamente.',task_id=key)
            return proposal
        finally:
            if not accepted:
                workspace.frozen_checks=original
                workspace.restore_checks()
                workspace.save()


def recover_verification_repair(engine,key,workspace):
    import base64
    from .build_models import VerificationSpec,VerificationRepairReview
    p=engine.progress[key];directory=p.get('verification_repair_ready')
    if not directory:return
    if not directory.startswith(f'tasks/{key}/verification-repairs/'):
        raise ValueError('Transação de reparo de verificação fora da task.')
    candidate=engine.store.read_json(directory+'/candidate.json')
    checks=engine.store.read_json(directory+'/candidate-checks.json')
    certificate=engine.store.read_json(directory+'/review.json')
    review=VerificationRepairReview.model_validate({k:certificate[k] for k in ('approved','preserves_requirements','tests_only_task','reason')})
    if (not review.approved or certificate['candidate_sha256']!=digest(json.dumps(candidate,sort_keys=True))
            or certificate['checks_sha256']!=digest(json.dumps(checks,sort_keys=True))):
        raise ValueError('O reparo da verificação não possui aprovação íntegra.')
    for name in checks:
        if not name.startswith('.harness/checks/') or '..' in name.split('/'):
            raise ValueError('Caminho de check fora da área de controle.')
    proposal=VerificationSpec.model_validate(candidate)
    workspace.frozen_checks={n:base64.b64decode(v) for n,v in checks.items()}
    workspace.restore_checks();workspace.save()
    p.update(verification=proposal.model_dump(),check_results={},probe_repair_pending=False)
    p.pop('verification_repair_ready',None)
    engine.store.json(f'tasks/{key}/verification.json',proposal.model_dump())
    engine.checkpoint()
