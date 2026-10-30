"""Builder retomável: dependências, reservas de arquivos e gates G0–G3."""

import asyncio
import json
import os
from pathlib import Path
import platform
import shlex
import shutil

from .artifacts import digest, atomic_write
from .agents import AgentError
from .build_artifacts import BuildError, BuildStore, DependenciesBlocked, DeliveryDependenciesPending, load_plan
from .build_commands import run_command
from .build_instructions import build_prompt
from .build_models import (BuildBlueprint, EnvironmentSpec, FidelityReview, TaskScope,
                           VerificationSpec, WriterResult)
from .build_recovery import DiagnosisPending, execution_brief, record_failure, request_scope, diagnose, repair_verification, recover_verification_repair
from .build_workspace import Workspace, conflicts, diff, manifest, matches, relative_path, copy_project, restore_sources, sync_tree
from .plan_models import agent_schema
from .run_events import BuildItem, InitEvent
from .recovery import OperationJournal
from .response_adapter import decode_response, contract_feedback
from .build_review import evidence_catalog, fidelity_schema, requirement_quotes, resolve_references


class ScopeExpansionPending(Exception):
    """A task volta à fila para reservar arquivos adicionais em execução exclusiva."""


class BuildEngine:
    def __init__(self,state,agents,*,on_event=None):
        self.state=state;self.agents=agents;self.on_event=on_event
        self.store=BuildStore(state.project_dir)
        self.source={};self.tasks={};self.scopes={};self.progress={};self.saved={}
        self.call_id=self.operations=self.corrections=self.contract_errors=0
        self.stage='dependencies';self.active={};self.last_role=None
        self.event_task_id=None
        self.packages_total=self.packages_ready=0
        self.publish_lock=asyncio.Lock()
        self.tools_lock=asyncio.Lock()
        self.rejections={};self.executed_checks={}
        self.journal=None

    def emit(self,kind='stage_changed'):
        if not self.on_event:return
        items=[];completed_phases=0;phase_done={}
        for phase in self.source.get('plan',{}).get('phases',[]):
            ids=[key for key,value in self.tasks.items() if value['phase_number']==phase['number']]
            done=bool(self.progress) and all(self.progress.get(key,{}).get('completed') for key in ids)
            if not ids:
                dependencies=set(phase['depends_on'])|({0} if phase['number']>0 else set())
                done=done and all(phase_done.get(dep,False) for dep in dependencies)
            phase_done[phase['number']]=done
            started=any(self.progress.get(key,{}).get('started') for key in ids)
            status='completed' if done else 'running' if started else 'waiting'
            completed_phases+=done
            items.append(BuildItem(f"P{phase['number']}",phase['title'],'phase',status=status))
            for key in ids:
                p=self.progress.get(key,{})
                items.append(BuildItem(key,self.tasks[key]['content']['title'],'task',
                    gates=tuple(p.get('gates',['waiting']*4)),status='completed' if p.get('completed') else 'waiting'))
        role=next(iter(self.active.values())) if len(self.active)==1 else None
        self.on_event(InitEvent(kind,self.stage,1+self.corrections,role,self.last_role,self.operations,self.call_id,
            self.corrections,0,0,0,module='build',phases_total=len(self.source.get('plan',{}).get('phases',[])),
            phases_approved=completed_phases,tasks_total=len(self.tasks),
            tasks_approved=sum(p.get('completed',False) for p in self.progress.values()),
            contract_errors=self.contract_errors,build_items=tuple(items),active_agents=len(self.active),
            max_agents=self.state.max_agents if self.state.build_mode=='parallel' else 1,
            packages_total=self.packages_total,packages_ready=self.packages_ready,task_id=self.event_task_id,
            active_writers=list(self.active.values()).count('writer'),active_reviewers=list(self.active.values()).count('reviewer')))

    def announce(self,stage,message,**details):
        self.stage=stage;self.event_task_id=details.get('task_id');self.store.event(stage,message,**details);self.emit()

    def checkpoint(self):
        self.saved.update(corrections=self.corrections,contract_errors=self.contract_errors,tasks=self.progress,blueprint={'tasks':[s.model_dump() for s in self.scopes.values()]})
        self.store.json('state.json',self.saved)
        self.emit('counts_changed')

    def gate(self,key,number,state):
        p=self.progress[key];p['started']=True;p['gates'][number]=state
        self.announce(f'g{number}',f'{key} · G{number} · {state}',task_id=key,gate=number,state=state)
        self.checkpoint()

    async def invoke(self,role,operation,data,model,cwd,check=None):
        feedback='';rejected=None
        schema=agent_schema(model)
        if operation=='fidelity':
            data=dict(data,evidence_catalog=evidence_catalog(data,cwd))
            schema=fidelity_schema(schema,data['evidence_catalog'])
        context_tree=await asyncio.to_thread(manifest,cwd)
        key=self.journal.key(role,operation,dict(data,source_tree=context_tree),schema)
        cached=self.journal.cached(key,model) if operation not in ('write','verification','delivery_repair') else None
        if cached is not None:
            try:
                if check:check(cached)
                return cached
            except ValueError as exc:
                self.journal.abandon(key,str(exc))
                feedback=str(exc)
        attempt=self.journal.read(key).get('attempt',0)
        while True:
            attempt+=1
            self.call_id=self.journal.next_call();call=self.call_id
            self.journal.start(key,role=role,operation=operation,data=data,attempt=attempt)
            self.operations=self.journal.metrics().get('operations',0)
            request=dict(data,contract_error=feedback,rejected_response=rejected,recovery_attempt=attempt)
            snapshot=None
            if operation in ('fidelity','verification','diagnose','review_verification_repair','delivery_plan','delivery_diagnosis','delivery_repair_review'):
                snapshot=self.store.path(f'control/audits/{call:04d}/source')
                await asyncio.to_thread(copy_project,cwd,snapshot,runtimes=False)
                await asyncio.to_thread(sync_tree,snapshot)
                atomic_write(snapshot.parent/'audit.json',json.dumps({'status':'running','workspace':str(cwd)}))
            provider_error=None
            self.active[call]=role;self.last_role=role;self.emit('agent_started')
            try:
                output=await self.agents.generate(self.state.reviewer if role=='reviewer' else self.state.writer,
                    role,build_prompt(operation,request,schema),schema,self.store,call,cwd=cwd,allow_tools=True)
            except AgentError as exc:
                provider_error=exc
            except asyncio.CancelledError:
                if snapshot:
                    await asyncio.to_thread(restore_sources,cwd,snapshot,snapshot.parent/'changes')
                    await asyncio.to_thread(sync_tree,cwd)
                    self.close_audit(snapshot)
                raise
            finally:
                self.active.pop(call,None);self.emit('agent_finished')
            if provider_error:
                if snapshot:
                    await asyncio.to_thread(restore_sources,cwd,snapshot,snapshot.parent/'changes')
                    await asyncio.to_thread(sync_tree,cwd)
                    self.close_audit(snapshot)
                self.journal.failed(key,provider_error)
                await self.recover('agent_recovery',str(provider_error),attempt,data.get('task',{}).get('id'))
                feedback=f'A chamada anterior falhou: {provider_error}. Continue a operação preservando o trabalho existente.'
                continue
            self.store.write(f'log/{role}-{call:04d}.response.txt',output)
            if operation in ('fidelity','verification','diagnose','review_verification_repair','delivery_plan','delivery_diagnosis','delivery_repair_review') and await asyncio.to_thread(manifest,cwd)!=context_tree:
                gate='G2' if operation=='fidelity' else 'G3'
                self.journal.abandon(key,f'{gate} alterou a fonte durante a auditoria.')
                await asyncio.to_thread(restore_sources,cwd,snapshot,snapshot.parent/'changes')
                await asyncio.to_thread(sync_tree,cwd)
                self.close_audit(snapshot)
                feedback=f'{gate} alterou a fonte da aplicação. O harness restaurou a fonte: apenas audite, sem editar código.'
                rejected=output
                await self.recover('auditor_recovery',feedback,attempt,data.get('task',{}).get('id'))
                continue
            if snapshot:self.close_audit(snapshot)
            try:
                raw=decode_response(output)
                if operation=='fidelity':raw=resolve_references(raw,data['evidence_catalog'])
                value=model.model_validate(raw)
                if check:check(value)
                self.store.json(f'log/{role}-{call:04d}.normalized.json', value.model_dump())
                self.journal.finish(key,value)
                return value
            except ValueError as exc:
                self.contract_errors+=1;feedback=contract_feedback(exc);rejected=output
                self.journal.failed(key,exc)
                if self.progress:self.checkpoint()
                task_id=data.get('task',{}).get('id')
                if attempt%3==0 and operation in ('write','fidelity','verification','delivery_repair') and task_id in self.progress:
                    p=self.progress[task_id]
                    p.update(diagnosis_pending=True,failed_contract={'operation':operation,'error':feedback,'rejected_response':output[-6000:]})
                    self.checkpoint()
                    raise DiagnosisPending()
                await self.recover('format_correction',f'{operation}: corrigindo o contrato do agente. {feedback}',attempt,data.get('task',{}).get('id'))

    async def recover(self,stage,message,attempt=1,task_id=None):
        self.announce(stage,message,task_id=task_id,attempt=attempt)
        # Cancelável; impede disparar chamadas sem pausa em falhas persistentes.
        await asyncio.sleep(min(0.25*2**min(max(attempt-1,0),6),15))

    def close_audit(self,snapshot):
        atomic_write(snapshot.parent/'audit.json',json.dumps({'status':'closed'}))

    async def recover_audits(self):
        for path in self.store.path('control/audits').glob('*/audit.json'):
            record=json.loads(path.read_text())
            if record['status']!='running':continue
            cwd=Path(record['workspace']).resolve()
            if not cwd.is_relative_to(self.store.path('work').resolve()):
                raise BuildError('A auditoria pendente aponta para fora das cópias do build.')
            await asyncio.to_thread(restore_sources,cwd,path.parent/'source',path.parent/'changes')
            await asyncio.to_thread(sync_tree,cwd)
            self.close_audit(path.parent/'source')
            self.announce('auditor_recovery','Fonte da auditoria interrompida restaurada; retomando o gate pendente.')

    async def reserve_extra_files(self,key,workspace):
        p=self.progress[key];scope=self.scopes[key]
        async with self.publish_lock:
            changes,tree=await asyncio.to_thread(workspace.changes)
            extras=list(dict.fromkeys(relative_path(name) for name in [*changes,*p.get('scope_files',[])] if not matches(name,scope.writes)))
            if extras:
                p['additional_reserved_files']=sorted(set(p.get('additional_reserved_files',[]))|set(extras))
                scope.writes=list(dict.fromkeys([*scope.writes,*extras]))
                self.store.json(f'tasks/{key}/scope-{self.store.run_id}-{p.get("attempts",0)}.json',{
                    'files':extras,'scope':scope.model_dump(),'policy':'Reserva exclusiva; G2 ainda exige fidelidade à task original.'})
            rebased=await asyncio.to_thread(workspace.rebase)
            p['scope_files']=[]
            p['gates']=['completed','waiting','waiting','waiting']
            p['check_results']={}
            if rebased or not p.get('candidate_result'):
                p['gates']=['waiting']*4
                p.update(stage='g0',feedback={**(p.get('feedback') or {}),'scope_recovery':{
                    'additional_reserved_files':p.get('additional_reserved_files',[]),'rebased_files':rebased,
                    'rebase_directory':str(workspace.root/'.harness/rebase'),
                    'required_change':'Continue somente a task original. Preserve as alterações integradas por outras tasks; resolva conflitos de merge indicados. A reserva não autoriza funcionalidades novas.'}})
            else:
                changes,tree=await asyncio.to_thread(workspace.audit,scope)
                result=WriterResult.model_validate(p['candidate_result'])
                result.files=list(dict.fromkeys([*result.files,*(name for name in changes if name in tree)]))
                p.update(stage='g1',writer_result=result.model_dump(),tested_tree=tree,changes=changes)
            await asyncio.to_thread(workspace.sync_sources)
            self.checkpoint()
        self.announce('scope_recovered',f'{key}: reserva ajustada com execução exclusiva; entrega seguirá pelos gates.',task_id=key)

    async def prepare_environment(self):
        self.announce('dependencies','Levantando dependências do plano e verificando o ambiente.')
        directory=self.store.path('control/environment-analysis');directory.mkdir(parents=True,exist_ok=True)
        cached=self.store.read_json('control/dependencies.json')
        spec=EnvironmentSpec.model_validate(cached) if cached else await self.invoke('reviewer','environment',{'phase_zero':self.source['plan']['phases'][0],
            'stack':self.source['init']['document']['stack'],'tools':self.source['init']['document']['tools'],
            'system':platform.system(),'platform':platform.platform(),'providers':sorted({self.state.writer.provider,self.state.reviewer.provider}),
            'executables':{name:shutil.which(name) for name in ('node','npm','pnpm','python3','pip','uv','cargo','go','dotnet','apt-get','dnf','pacman','brew','codex','claude','opencode','agy')}},
            EnvironmentSpec,directory)
        self.store.json('control/dependencies.json',spec.model_dump())
        await self.ensure_dependencies(spec.dependencies,self.state.project_dir)

    async def ensure_dependencies(self,dependencies,cwd):
        self.packages_total+=len(dependencies);missing=[];attempted=set();verified=[]
        for index,dependency in enumerate(dependencies,1):
            prefix=f'log/dependency-{self.store.run_id}-{self.operations}-{index:03d}'
            result=await run_command(dependency.check,cwd,self.store,prefix+'-check')
            if result['exit_code']!=0:
                for j,install in enumerate(dependency.install):
                    key=json.dumps(install.model_dump(),sort_keys=True)
                    if key in attempted:continue
                    attempted.add(key)
                    self.announce('installing_dependencies',f'Instalando dependência: {dependency.name}.')
                    installed=await run_command(install,cwd,self.store,prefix+f'-install-{j}')
                    if installed['exit_code']!=0:break
                result=await run_command(dependency.check,cwd,self.store,prefix+'-recheck')
            if result['exit_code']==0:
                self.packages_ready+=1
                verified.append({'name':dependency.name,'check':dependency.check.model_dump(),
                                 'stdout':result['stdout'][-4000:],'stderr':result['stderr'][-4000:]})
            else:missing.append({'dependency':dependency.model_dump(),'exit_code':result['exit_code'],
                                 'error':result['stderr'][-3000:]})
            self.emit()
        if missing:
            self.store.json('control/missing-dependencies.json',missing)
            lines=['#!/usr/bin/env bash','set -euo pipefail',f'cd -- {shlex.quote(str(cwd))}']
            commands=set()
            notes=['# Dependências pendentes','']
            for entry in missing:
                dep=entry['dependency'];lines+=['']
                lines.extend('# Pacote: '+line for line in dep['name'].splitlines())
                notes.extend([f"## {dep['name']}",'',dep['purpose'],'',dep['manual_instructions'],'',
                              f"Falha observada (saída {entry['exit_code']}):",'',entry['error'],''])
                for line in dep['manual_instructions'].splitlines():lines.append('# '+line)
                for command in dep['install']:
                    argv=list(command['argv'])
                    # No script manual, sudo pode pedir a autenticação do próprio usuário.
                    if argv and Path(argv[0]).name=='sudo':argv=[a for a in argv if a!='-n']
                    text=' '.join(f'{k}={shlex.quote(v)}' for k,v in ((v['name'],v['value']) for v in command['env']))
                    text=(('env '+text+' ') if text else '')+shlex.join(argv)
                    if text not in commands:lines.append(text);commands.add(text)
            self.store.write('manual-install.md','\n'.join(notes)+'\n')
            if commands:
                self.store.write('install-manual.sh','\n'.join(lines)+'\n');os.chmod(self.store.path('install-manual.sh'),0o700)
            else:self.store.path('install-manual.sh').unlink(missing_ok=True)
            names=', '.join(e['dependency']['name'] for e in missing)
            self.announce('dependencies_blocked',f'Dependências pendentes: {names}.')
            recovery=f'Script disponível: {self.store.path("install-manual.sh")}. ' if commands else 'Não há instalador automático viável para estes pacotes. '
            raise DependenciesBlocked(f'Instale manualmente: {names}. {recovery}Instruções específicas: {self.store.path("manual-install.md")}. Depois execute build novamente.')
        self.announce('dependencies_ready','Dependências verificadas; preparação concluída.')
        return verified

    async def prepare_tasks(self):
        for phase in self.source['plan']['phases']:
            for task in phase['tasks']:
                self.tasks[task['id']]=task
        last=self.source['plan']['phases'][-1]['number']
        self.tasks['BUILD-README']={'id':'BUILD-README','phase_number':last,'context_policy':'fresh',
            'seed':{'scope':'Entregar documentação de instalação, configuração e execução da aplicação produzida.',
                    'source_quotes':[],'coverage':[]},
            'content':{'title':'Documentação de instalação e execução','objective':'Fornecer README útil para instalar e executar a aplicação.',
                       'implementation_steps':['Documentar dependências, instalação e execução reais.','Incluir exemplos de configuração e uso aplicáveis, sem segredos.'],
                       'deliverables':['README.md e exemplos de variáveis necessários.']}}
        repair=self.saved.get('delivery',{}).get('repair_task')
        if repair:self.tasks['BUILD-INSTALLATION']=repair
        roles={}
        for item in self.source.get('classification',{}).get('activities',[]):
            for index,part in enumerate(item['parts'],1):
                identifier=item['source_id'] if len(item['parts'])==1 else f"{item['source_id']}.{index:02d}"
                roles[identifier]=part['responsibility']
        def validate(blueprint):
            if len({s.task_id for s in blueprint.tasks})!=len(blueprint.tasks) or {s.task_id for s in blueprint.tasks}!=set(self.tasks)-{'BUILD-README'}:
                raise ValueError('Mapeie exatamente todos os IDs de tasks do plan, sem omissões ou tasks novas.')
            for scope in blueprint.tasks:
                task=self.tasks[scope.task_id]
                assigned=[roles.get(c['requirement_id']) for c in task['seed'].get('coverage',[])]
                if 'implementation' in assigned and scope.kind=='documentation':
                    raise ValueError(f'{scope.task_id} contém implementação aprovada no plan e exige kind code/setup, com G3 executável.')
                if task['phase_number']==0 and scope.kind!='documentation':
                    raise ValueError('Fase 0 do plan é catálogo documental; a instalação já ocorre na etapa de dependências.')
                for path in scope.writes+scope.reads:relative_path(path,pattern=True)
                valid_ids={f'R{i:03d}' for i in range(1,len(self.source['init']['document']['requirements'])+1)}
                if not set(scope.requirement_ids).issubset(valid_ids):raise ValueError('Use requirement_ids Rxxx fornecidos, sem criar IDs.')
                if scope.task_id in scope.depends_on or any(d not in self.tasks or d=='BUILD-README' for d in scope.depends_on):
                    raise ValueError('Dependências devem apontar IDs de tasks existentes, sem autorreferência.')
            candidate={s.task_id:s for s in blueprint.tasks}
            self.validate_graph(candidate)
        cached=self.saved.get('blueprint')
        if cached:
            value=BuildBlueprint.model_validate({'tasks':[s for s in cached['tasks'] if s['task_id']!='BUILD-README']})
            validate(value)
        else:
            directory=self.store.path('control/blueprint-analysis');directory.mkdir(parents=True,exist_ok=True)
            value=await self.invoke('reviewer','blueprint',{'plan':self.source['plan'],'product_requirements':{
                    f'R{i:03d}':text for i,text in enumerate(self.source['init']['document']['requirements'],1)}},BuildBlueprint,directory,validate)
        self.scopes={s.task_id:s for s in value.tasks}
        self.scopes['BUILD-README']=TaskScope(task_id='BUILD-README',kind='documentation',writes=['README.md','.env.example'],reads=['**'],depends_on=list(set(self.tasks)-{'BUILD-README','BUILD-INSTALLATION'}),
            requirement_ids=[f'R{i:03d}' for i in range(1,len(self.source['init']['document']['requirements'])+1)])
        if cached:
            previous_readme=next((s for s in cached['tasks'] if s['task_id']=='BUILD-README'),None)
            if previous_readme:
                self.scopes['BUILD-README'].writes=list(dict.fromkeys([*self.scopes['BUILD-README'].writes,
                    *(relative_path(p,pattern=True) for p in previous_readme['writes'])]))
        self.validate_graph(self.scopes)
        previous=self.saved.get('tasks',{});accepted=self.saved.get('accepted_tree',{})
        for key,p in previous.items():
            if not p.get('completed') and p.get('stage')=='publish' and p.get('workspace'):
                workspace=Workspace.resume(self.state.project_dir,self.store,p['workspace'])
                recovered=await asyncio.to_thread(workspace.recover_publication,p['tested_tree'])
                if recovered:
                    published,accepted=recovered
                    p.update(completed=True,stage='completed',gates=['completed']*4,published=published)
                    if key=='BUILD-INSTALLATION':
                        self.store.json(f'tasks/{key}/approval.json',{'task_id':key,'gates':['completed']*4,
                            'review':self.saved['delivery']['repair_review'],'published':published,'proof':'delivery/approval.json'})
                        self.saved['delivery'].pop('repair_workspace',None)
                    else:self.write_approval(key,p)
                    self.saved['accepted_tree']=accepted
                    self.store.json('state.json',self.saved)
        accepted=self.saved.get('accepted_tree',{})
        current=await asyncio.to_thread(manifest,self.state.project_dir)
        changed=diff(accepted,current) if accepted else []
        invalid={key for key,scope in self.scopes.items() if any(matches(p,scope.writes+scope.reads) for p in changed)}
        while True:
            expanded=invalid|{key for key in self.scopes if self.dependencies(key)&invalid}
            if expanded==invalid:break
            invalid=expanded
        for key in self.tasks:
            p=previous.get(key,{})
            if p.get('completed') and p.get('gates')!=['completed']*4:
                p.update(completed=False,stage='g1' if p.get('writer_result') else 'g0',check_results={})
            if p and key not in invalid:
                self.progress[key]=p
            else:
                if p:
                    self.store.json(f'tasks/{key}/history/{self.store.run_id}.json',p)
                self.progress[key]={'gates':['waiting']*4,'completed':False,'started':False,'attempts':0,'stage':'g0'}
        for key in self.tasks:self.store.json(f'tasks/{key}/brief.json',execution_brief(self,key))
        self.saved['accepted_tree']=current
        self.checkpoint()

    def dependencies(self,key):
        scope=self.scopes[key];task=self.tasks[key]
        phase=next(p for p in self.source['plan']['phases'] if p['number']==task['phase_number'])
        phase_deps=set(phase['depends_on'])|({0} if phase['number']>0 else set())
        return set(scope.depends_on)|{k for k,t in self.tasks.items() if t['phase_number'] in phase_deps}

    def validate_graph(self,scopes):
        saved=self.scopes;self.scopes=scopes
        try:
            visiting=set();done=set()
            def visit(key):
                if key in visiting:raise ValueError('Dependências de tasks/fases formam um ciclo.')
                if key in done:return
                visiting.add(key)
                for dep in self.dependencies(key):
                    if dep in scopes:visit(dep)
                visiting.remove(key);done.add(key)
            for key in scopes:visit(key)
        finally:self.scopes=saved

    def code_context(self,key,workspace,feedback):
        return {'task':self.tasks[key],'execution_brief':execution_brief(self,key),'scope':self.scopes[key].model_dump(),
            'objective':self.source['init']['document']['objective'],
            'requirements':[text for i,text in enumerate(self.source['init']['document']['requirements'],1)
                            if f'R{i:03d}' in self.scopes[key].requirement_ids],
            'constraints':self.source['init']['document']['constraints'],
            'stack':self.source['init']['document']['stack'],'feedback':feedback,
            'workspace':str(workspace.root),
            'recovery_epoch':self.progress[key].get('diagnosis_sequence',0),
            'baseline_directory':str(workspace.root/'.harness/baseline'),
            'additional_reserved_files':self.progress[key].get('additional_reserved_files',[])}

    def validate_scope_requests(self,requests,key,workspace,feedback):
        source='\n'.join(requirement_quotes(self.code_context(key,workspace,feedback)))
        for request in requests:
            relative_path(request.file)
            if request.requirement_quote not in source:
                raise ValueError('A solicitação de reserva deve citar literalmente uma necessidade da task atual.')

    def reject(self,key,gate,feedback,workspace):
        identity=feedback
        if gate==3:
            identity=[(f['command']['argv'],f['acceptance']) for f in feedback['functional_failures']]
        elif gate==2:
            identity=[(i['file'],i['acceptance']) for i in feedback['issues']]
        fingerprint=digest(json.dumps((gate,identity),sort_keys=True,ensure_ascii=False))
        signature=(key,fingerprint)
        self.rejections[signature]=self.rejections.get(signature,0)+1
        if self.rejections[signature]>1:
            feedback=dict(feedback,repeated_conflict=self.rejections[signature],
                recovery_instruction='A correção anterior não satisfez o mesmo aceite. Inspecione a causa, aplique uma mudança efetiva e preserve os checks congelados; não basta repetir a resposta.')
        record_failure(self,key,gate,feedback,manifest(workspace.root))
        self.corrections+=1
        self.progress[key]['rejection_sequence']=self.progress[key].get('rejection_sequence',0)+1
        self.progress[key].update(feedback=feedback,before_failed=manifest(workspace.root),stage='g0',check_results={})
        self.gate(key,gate,'waiting' if gate==1 else 'rejected')
        self.store.json(f'tasks/{key}/rejection-{self.progress[key]["attempts"]}-{self.progress[key]["rejection_sequence"]}.json',{'gate':gate,'feedback':feedback})

    async def task_run(self,key):
        scope=self.scopes[key];p=self.progress[key]
        if p.get('workspace'):
            workspace=Workspace.resume(self.state.project_dir,self.store,p['workspace'])
        else:
            async with self.publish_lock:
                copying=asyncio.create_task(asyncio.to_thread(Workspace,self.state.project_dir,self.store,key))
                try:workspace=await asyncio.shield(copying)
                except asyncio.CancelledError:
                    workspace=await copying
                    p.update(workspace=str(workspace.root),stage='g0');self.checkpoint()
                    raise
            p.update(workspace=str(workspace.root),stage='g0');self.checkpoint()
        recover_verification_repair(self,key,workspace)
        feedback=p.get('feedback')
        verification=VerificationSpec.model_validate(p['verification']) if p.get('verification') else None
        before_failed=p.get('before_failed')
        # Gate receipts bind the approval to the exact saved source tree.
        if p.get('stage','g0') not in ('g0','scope_repair') and p.get('tested_tree')!=await asyncio.to_thread(manifest,workspace.root):
            p.update(stage='g0',gates=['waiting']*4,check_results={})
        if p.get('stage')=='scope_repair':await self.reserve_extra_files(key,workspace)
        while True:
            if p.get('diagnosis_pending'):
                decision=await diagnose(self,key,workspace)
                if decision.action=='expand_scope' or any(not matches(f,scope.writes) for f in decision.required_files):
                    request_scope(self,key,decision.required_files,decision.root_cause)
                    raise ScopeExpansionPending()
                if decision.action=='repair_verification':
                    p['probe_repair_pending']=True
                    p['stage']='g3'
                elif decision.action=='review_again' and p.get('writer_result') and p.get('files') and await asyncio.to_thread(manifest,workspace.root)==p.get('tested_tree'):
                    p['stage']='g2'
                else:
                    p['stage']='g0';p['probe_repair_pending']=False
                self.checkpoint()
            feedback=p.get('feedback');before_failed=p.get('before_failed')
            if p.get('stage','g0')=='g0':
                p['attempts']+=1
                p['gates']=['running','waiting','waiting','waiting']
                self.gate(key,0,'running')
                workspace.restore_checks()
                def validate_delivery(value):
                    for name in value.files:relative_path(name)
                    self.validate_scope_requests(value.scope_requests,key,workspace,feedback)
                result=await self.invoke('writer','write',self.code_context(key,workspace,feedback),WriterResult,workspace.root,validate_delivery)
                if result.scope_requests:
                    files=[r.file for r in result.scope_requests if not matches(r.file,scope.writes)]
                    if files:
                        request_scope(self,key,files,result.summary)
                        raise ScopeExpansionPending()
                p['candidate_result']=result.model_dump()
                changes,tree=await asyncio.to_thread(workspace.changes)
                extras=list(dict.fromkeys(relative_path(name) for name in [*changes,*result.files] if not matches(name,scope.writes)))
                if extras:
                    p.update(stage='scope_repair',scope_files=extras,force_serial=True)
                    await asyncio.to_thread(workspace.sync_sources)
                    self.checkpoint()
                    self.announce('scope_recovery',f'{key}: reservando arquivos adicionais em execução exclusiva: '+', '.join(extras),task_id=key)
                    raise ScopeExpansionPending()
                changes,tree=await asyncio.to_thread(workspace.audit,scope)
                if feedback and (feedback.get('issues') or feedback.get('functional_failures')) and tree==before_failed:
                    p['feedback']=dict(feedback,recovery_instruction='A entrega não mudou após a devolução. Inspecione os arquivos reais e corrija a causa indicada, sem alterar o aceite ou os testes.')
                    record_failure(self,key,0,p['feedback'],tree)
                    p['diagnosis_pending']=True
                    self.checkpoint()
                    await self.recover('writer_recovery',f'{key}: escritor precisa aplicar a correção ao código.',p['attempts'],key)
                    continue
                result.files=list(dict.fromkeys([*result.files,*(name for name in changes if name in tree)]))
                await asyncio.to_thread(workspace.sync_sources)
                p.update(stage='g1',writer_result=result.model_dump(),tested_tree=tree,changes=changes,check_results={})
                self.gate(key,0,'completed')
            result=WriterResult.model_validate(p['writer_result'])
            tree=p['tested_tree'];changes=p['changes']
            if p['stage']=='g1':
                self.gate(key,1,'running')
                files=[];missing=[];merge_conflicts=[]
                for name in result.files:
                    name=relative_path(name)
                    if not matches(name,scope.writes):raise BuildError(f'{key}: arquivo entregue fora da reserva: {name}.')
                    path=workspace.root/name
                    if not path.is_file() or not path.stat().st_size:missing.append(name)
                    else:
                        files.append(name)
                        text=path.read_text(encoding='utf-8',errors='replace')
                        if any(line.startswith('<<<<<<< ') for line in text.splitlines()) and any(line.startswith('>>>>>>> ') for line in text.splitlines()):
                            merge_conflicts.append(name)
                substantive=any(Path(f).suffix.lower() not in ('.md','.rst','.txt','.log') for f in files)
                if missing or merge_conflicts or not files or (scope.kind=='code' and not substantive):
                    feedback={'files_missing':missing,'merge_conflicts':merge_conflicts,'required_change':('Resolva os marcadores de merge preservando as contribuições necessárias das duas tasks.' if merge_conflicts else 'Grave os documentos reais previstos pela task; não basta afirmar que foram criados.' if scope.kind=='documentation' else 'Entregue os arquivos reais de código/configuração da task, não apenas uma descrição.'),'acceptance':'Arquivos da entrega existem, sem conflitos de merge e com conteúdo apropriado à task.'}
                    self.reject(key,1,feedback,workspace);before_failed=tree;await self.recover('task_recovery',f'{key}: corrigindo a entrega conforme o gate.',p['attempts'],key);continue
                p.update(stage='g2',files=files)
                self.gate(key,1,'completed')
            files=p['files']
            immutable=tree
            if p['stage']=='g2':
                self.gate(key,2,'running')
                def validate_fidelity(value):
                    task=self.tasks[key]
                    source='\n'.join(requirement_quotes(self.code_context(key,workspace,feedback)))
                    self.validate_scope_requests(value.scope_requests,key,workspace,feedback)
                    requested={r.file for r in value.scope_requests}
                    for issue in value.issues:
                        name=relative_path(issue.file)
                        if not matches(name,scope.writes) and name not in requested:raise ValueError('G2 só pode apontar arquivos da responsabilidade desta task.')
                        if issue.requirement_quote not in source:raise ValueError('Cite literalmente um requisito da task/origem, sem inventar exigências.')
                        path=workspace.root/name
                        if issue.missing:
                            if path.exists():raise ValueError('O arquivo apontado como ausente existe.')
                        elif not issue.evidence_quote or not path.is_file() or issue.evidence_quote not in path.read_text(encoding='utf-8',errors='replace'):
                            raise ValueError('A evidência deve ser um trecho literal do arquivo entregue.')
                review=await self.invoke('reviewer','fidelity',{**self.code_context(key,workspace,feedback),'delivered_files':files,'changed_files':changes},FidelityReview,workspace.root,validate_fidelity)
                if await asyncio.to_thread(manifest,workspace.root)!=immutable:
                    raise BuildError(f'{key}: G2 alterou a fonte em vez de somente auditá-la.')
                if review.scope_requests:
                    files=[r.file for r in review.scope_requests if not matches(r.file,scope.writes)]
                    if files:
                        p['feedback']={'issues':[i.model_dump() for i in review.issues]}
                        request_scope(self,key,files,review.summary)
                        raise ScopeExpansionPending()
                if not review.approved:
                    feedback={'issues':[i.model_dump() for i in review.issues]}
                    self.reject(key,2,feedback,workspace);before_failed=tree;await self.recover('task_recovery',f'{key}: corrigindo a entrega conforme o gate.',p['attempts'],key);continue
                p.update(stage='g3',fidelity_review=review.model_dump())
                self.gate(key,2,'completed')
            review=FidelityReview.model_validate(p['fidelity_review'])
            self.gate(key,3,'running')
            if verification is None or p.get('probe_repair_pending'):
                def validate_checks(value):
                    if scope.kind!='documentation' and not value.checks:raise ValueError('Código/setup exige ao menos uma verificação funcional executável.')
                    for check in value.checks:
                        if not set(check.target_files).issubset(files):raise ValueError('Os targets dos checks devem ser arquivos da entrega desta task.')
                        for argument in check.command.argv:
                            if argument.startswith('.harness/checks/') and not (workspace.root/argument).exists():
                                raise ValueError(f'O script de check {argument} não foi criado. Crie o arquivo antes de responder.')
                        if check.assertion_file:
                            name=Path(check.assertion_file)
                            if name.is_absolute() or '..' in name.parts:
                                raise ValueError('assertion_file deve estar dentro do workspace.')
                            test_file=workspace.root/name
                            if not test_file.is_file() or not test_file.stat().st_size:raise ValueError('O arquivo de assertivas precisa existir e conter verificações reais.')
                data={**self.code_context(key,workspace,feedback),'delivered_files':files,
                    'scratch_directory':'.harness/checks','tools_directory':str(self.store.path('tools'))}
                if p.get('probe_repair_pending') and verification is not None:
                    verification=await repair_verification(self,key,workspace,verification,data,validate_checks)
                else:
                    verification=await self.invoke('reviewer','verification',{**self.code_context(key,workspace,feedback),
                        'delivered_files':files,'scratch_directory':'.harness/checks','tools_directory':str(self.store.path('tools'))},VerificationSpec,workspace.root,validate_checks)
                if await asyncio.to_thread(manifest,workspace.root)!=immutable:
                    raise BuildError(f'{key}: o verificador G3 alterou código da aplicação para preparar os testes.')
                workspace.freeze_checks()
                p['verification']=verification.model_dump();self.checkpoint()
                self.store.json(f'tasks/{key}/verification.json',verification.model_dump())
                for name,data in workspace.frozen_checks.items():
                    target=self.store.path(f'checks/{key}/{name.removeprefix(".harness/checks/")}')
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            if verification.dependencies:
                async with self.tools_lock:
                    tools=self.store.path('tools');tools.mkdir(parents=True,exist_ok=True)
                    await self.ensure_dependencies(verification.dependencies,tools)
            workspace.restore_checks()
            await asyncio.to_thread(workspace.clear_bytecode)
            probe_source=self.store.path(f'control/probe-source/{key}-{p["attempts"]}-{p.get("verification_revision",0)}')
            await asyncio.to_thread(copy_project,workspace.root,probe_source,runtimes=False)
            failures=[]
            for index,check in enumerate(verification.checks,1):
                outcome=p.setdefault('check_results',{}).get(str(index))
                if outcome is None:
                    outcome=await run_command(check.command,workspace.root,self.store,
                        f'log/{self.store.run_id}-{key}-g3-{p["attempts"]}-v{p.get("verification_revision",0)}-{index}',test=True)
                    p['check_results'][str(index)]=outcome;self.checkpoint()
                if outcome['exit_code']!=0 or (check.expected_stdout is not None and outcome['stdout'].strip()!=check.expected_stdout.strip()):
                    failures.append({'command':check.command.model_dump(),'exit_code':outcome['exit_code'],
                        'expected_stdout':check.expected_stdout,
                        'observed':(outcome['stderr']+'\n'+outcome['stdout'])[-6000:],
                        'required_change':'Corrija a implementação para atender a verificação funcional congelada.',
                        'acceptance':check.requirement_quote})
            if await asyncio.to_thread(manifest,workspace.root)!=immutable:
                await asyncio.to_thread(restore_sources,workspace.root,probe_source,probe_source.parent/(probe_source.name+'-changes'))
                await asyncio.to_thread(workspace.sync_sources)
                p['diagnosis_pending']=True
                p['failed_contract']={'operation':'checks','error':'Os testes modificaram a fonte da aplicação; diagnostique o isolamento da verificação.'}
                self.checkpoint()
                raise DiagnosisPending()
            if failures:
                feedback={'functional_failures':failures}
                self.reject(key,3,feedback,workspace);before_failed=tree;await self.recover('task_recovery',f'{key}: corrigindo a entrega conforme o gate.',p['attempts'],key);continue
            p['stage']='publish';self.checkpoint()
            publishing=asyncio.create_task(self.accept_workspace(key,workspace,scope,p,verification,review,files,immutable))
            try:await asyncio.shield(publishing)
            except asyncio.CancelledError:
                await publishing
                raise
            return

    async def accept_workspace(self,key,workspace,scope,p,verification,review,files,tested_tree):
        async with self.publish_lock:
            current=await asyncio.to_thread(manifest,self.state.project_dir)
            if current!=self.saved['accepted_tree']:
                raise BuildError('Os arquivos do projeto mudaram fora da integração do harness. Os arquivos foram preservados.')
            published,accepted=await asyncio.to_thread(workspace.publish,scope,tested_tree)
            self.saved['accepted_tree']=accepted
            self.gate(key,3,'completed')
            p.update(completed=True,stage='completed',workspace=str(workspace.root),files=files,published=published)
            self.write_approval(key,p)
            self.checkpoint()

    def write_approval(self,key,p):
        self.store.json(f'tasks/{key}/approval.json',{'task_id':key,'gates':['completed']*4,
            'checks_executed':len(p['verification']['checks']),'attempts':p['attempts'],'files':p['files'],
            'fidelity_review':p['fidelity_review']})

    async def schedule(self):
        pending={key for key in self.tasks if not self.progress[key]['completed'] and key!='BUILD-INSTALLATION'}
        running={};limit=self.state.max_agents if self.state.build_mode=='parallel' else 1
        limit=max(1,min(32,limit))
        order={key:index for index,key in enumerate(self.tasks)}
        try:
            while pending or running:
                serial=sorted((k for k in pending if self.progress[k].get('force_serial')),key=order.get)
                candidates=serial[:1] if serial else sorted(pending,key=order.get)
                if (serial and running) or any(self.progress[k].get('force_serial') for k in running.values()):
                    candidates=[]
                for key in candidates:
                    if len(running)>=limit:break
                    if any(not self.progress[d]['completed'] for d in self.dependencies(key)):continue
                    if any(conflicts(self.scopes[key],self.scopes[active]) for active in running.values()):continue
                    running[asyncio.create_task(self.task_run(key))]=key;pending.remove(key)
                    if self.progress[key].get('force_serial'):break
                if not running:
                    raise BuildError('Nenhuma task pode iniciar: verifique dependências e reservas do blueprint.')
                done,_=await asyncio.wait(running,return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    key=running.pop(future)
                    try:await future
                    except (ScopeExpansionPending,DiagnosisPending):pending.add(key)
                    except DependenciesBlocked:raise
                    except BuildError as exc:
                        p=self.progress[key]
                        p['recovery_count']=p.get('recovery_count',0)+1
                        p.update(stage='scope_repair',force_serial=True,completed=False,candidate_result=None,
                            feedback={**(p.get('feedback') or {}),'execution_recovery':{
                                'reason':str(exc),'required_change':'Diagnostique e corrija esta falha na task atual, preservando as entregas anteriores e os critérios de aceite.'}})
                        self.checkpoint()
                        pending.add(key)
                        await self.recover('task_recovery',f'{key}: recuperação automática: {exc}',p['recovery_count'],key)
        finally:
            for future in running:future.cancel()
            if running:await asyncio.gather(*running,return_exceptions=True)

    async def run(self):
        if not self.state.writer or not self.state.reviewer:raise BuildError('Selecione escritor e revisor antes do build.')
        if self.state.build_mode not in ('serial','parallel') or not 1<=self.state.max_agents<=32:
            raise BuildError('Escolha modo serial/paralelo e máximo de 1 a 32 agentes.')
        try:
            self.source,self.saved=self.store.begin(self.state)
            self.journal=OperationJournal(self.store,self.source['plan_sha256'])
            self.corrections=self.saved.get('corrections',0)
            self.contract_errors=self.saved.get('contract_errors',0)
            self.operations=self.journal.metrics().get('operations',0)
            self.call_id=self.journal.metrics().get('call_id',0)
            await self.recover_audits()
            from .agents import CLIAgents
            if isinstance(self.agents,CLIAgents):
                absent=[]
                for selection in (self.state.writer,self.state.reviewer):
                    command=self.agents.commands.get(selection.provider,(selection.provider,))[0]
                    if not shutil.which(command):absent.append(command)
                if absent:
                    self.store.json('control/missing-clis.json',{'programs':sorted(set(absent)),
                        'instruction':'Instale e autentique os CLIs selecionados, ou configure build_agents no catálogo, e execute build novamente.'})
                    raise DependenciesBlocked('CLIs selecionados não disponíveis: '+', '.join(sorted(set(absent)))+
                        '. Instale/autentique os programas ou configure build_agents; detalhes em .harness/build/control/missing-clis.json.')
            await self.prepare_environment()
            self.announce('scheduling','Organizando dependências, arquivos e execução das tasks.')
            await self.prepare_tasks()
            await self.schedule()
            from .build_delivery import validate_delivery
            delivery=await validate_delivery(self)
            if load_plan(self.state.project_dir)!=self.source:raise BuildError('O plan mudou durante o build; os arquivos da aplicação foram preservados.')
            readme=self.state.project_dir/'README.md'
            if not readme.is_file() or not readme.read_text(encoding='utf-8').strip():raise BuildError('README de instalação/execução não foi entregue.')
            self.store.write('report.md',f'# Build concluído\n\nProjeto: {self.state.project_dir}\n\n'
                f'{len(self.tasks)} tasks aprovadas nos gates G0–G3.\n\n'
                'As verificações são funcionais por task, sem julgamento estético ou suíte global.\n\n'
                'Instalação limpa, build e inicialização operacional aprovados. Diagnósticos adicionais constam no comprovante de entrega.\n'
                'Evidências: build/delivery/approval.json.\n\n'
                'Consulte o README.md da aplicação para instalação, configuração e execução.\n')
            self.announce('completed','Build concluído: aplicação e README entregues na raiz do projeto.')
            self.store.finish('completed',tasks=len(self.tasks),report='build/report.md',
                              delivery='build/delivery/approval.json',delivery_source_key=delivery['source_key'],
                              delivery_status='approved',ready_to_run=True,
                              files_sha256=digest(json.dumps(self.saved['accepted_tree'],sort_keys=True)))
            return self.store.path('report.md')
        except DeliveryDependenciesPending as exc:
            self.store.write('report.md','# Build concluído — instalação pendente\n\n'
                'A geração e validação das tasks foram concluídas. A instalação operacional ainda não foi aprovada.\n\n'
                +str(exc)+'\n\nConsulte no README a relação de pacotes e comandos. Revalidar a instalação preserva as tasks concluídas.\n')
            self.announce('delivery_pending','Build concluído — instalação pendente. Consulte os pacotes e comandos no README.')
            self.store.finish('completed',tasks=len(self.tasks),report='build/report.md',
                delivery_status='blocked',ready_to_run=False,reason=str(exc),
                recovery_script='build/install-manual.sh' if self.store.path('install-manual.sh').is_file() else None)
            return self.store.path('report.md')
        except DependenciesBlocked as exc:
            if self.store.started:
                previous=self.store.path('report.md')
                if previous.exists():self.store.write(f'control/reports/{self.store.run_id}.previous.md',previous.read_text())
                self.store.write('report.md','# Build não concluído: dependências pendentes\n\n'+str(exc)+'\n')
            self.store.finish('blocked',reason=str(exc),recovery_script='build/install-manual.sh' if self.store.path('install-manual.sh').is_file() else None);raise
        except asyncio.CancelledError:
            self.store.finish('cancelled',reason='Build interrompido; tasks integradas foram preservadas para retomada.');raise
        except Exception as exc:
            self.store.finish('failed',reason=str(exc));raise
        finally:
            if self.store.started and self.progress:
                for p in self.progress.values():p['gates']=['waiting' if g=='running' else g for g in p['gates']]
                self.checkpoint()
            self.store.close()
