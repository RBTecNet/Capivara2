"""Barreira operacional de entrega: prova de instalação limpa antes de concluir o build."""
import asyncio
import json
from pathlib import Path
import platform
import os
import shlex
import shutil
import tempfile
from uuid import uuid4

from .artifacts import digest
from .build_artifacts import BuildError,DependenciesBlocked,DeliveryDependenciesPending
from .build_commands import run_command
from .build_models import TaskScope,WriterResult
from .build_workspace import Workspace,manifest,matches,relative_path
from .delivery_models import DeliveryPlan,DeliveryDiagnosis,DeliveryRepairReview,DeliveryRequirement
from .delivery_source import copy_source,source_key,source_tree,source_name,manifests,installation,compilation,render_readme,readme_body
from .delivery_run import startup,validate_command
from .recovery import fingerprint
from .build_recovery import DiagnosisPending
from .delivery_evidence import repair_evidence,failure_context,operational_only


REPAIR='BUILD-INSTALLATION'


class DeliveryGate:
    def __init__(self,engine):
        self.e=engine;self.store=engine.store;self.project=engine.state.project_dir
        self.record=engine.saved.setdefault('delivery',{})
        self.work=None;self.feedback=None
        if self.record.get('repair_workspace'):
            self.work=Workspace.resume(self.project,self.store,self.record['repair_workspace'])
        self.applied=self.record.setdefault('readme_changes_applied',[])

    @property
    def root(self):return self.work.root if self.work else self.project

    def save(self,**fields):
        self.record.update(fields);self.e.checkpoint()

    def host(self):
        release=Path('/etc/os-release')
        executables={name:shutil.which(name) for name in ('node','npm','pnpm','yarn','python3','pip','uv','make','gcc','g++','cc','pkg-config','cargo','go','dotnet','apt-get','dnf','pacman','brew','sudo')}
        return {'system':platform.platform(),'os_release':release.read_text()[:3000] if release.exists() else platform.system(),
            'executables':executables,'executable_fingerprints':{name:[Path(path).stat().st_size,Path(path).stat().st_mtime_ns] for name,path in executables.items() if path}}

    def context(self):
        return {'objective':self.e.source['init']['document']['objective'],
            'stack':self.e.source['init']['document']['stack'],'tools':self.e.source['init']['document']['tools'],
            'manifests':manifests(self.root),'readme':readme_body(self.project),
            'system':self.host(),'source_key':source_key(self.root),
            'procedure_revision':self.record.get('procedure_revision',0),
            'procedure_failure':self.record.get('procedure_failure'),'repair_feedback':self.record.get('repair_feedback'),
            'instruction':'Validar o procedimento operacional desta entrega, não uma suíte funcional global.'}

    def plan_key(self):return fingerprint(self.context())

    async def plan(self):
        key=self.plan_key()
        if self.record.get('plan_key')==key and self.record.get('plan'):
            return DeliveryPlan.model_validate(self.record['plan'])
        analysis=self.store.path(f'delivery/analysis-{uuid4().hex[:12]}')
        copy_source(self.root,analysis)
        def check(value):
            for dep in value.prerequisites:validate_command(dep.check,host=True)
            if (self.root/'package.json').exists():
                manager=json.loads((self.root/'package.json').read_text()).get('packageManager','npm').split('@',1)[0]
                executables={Path(dep.check.argv[0]).name for dep in value.prerequisites}
                if 'node' not in executables or manager not in executables:
                    raise ValueError('O inventário Node deve listar e conferir explicitamente Node.js e seu gerenciador de pacotes, mesmo já instalados.')
            for spec in value.install:validate_command(spec)
            for item in value.checks:validate_command(item.command)
            for probe in value.startup:
                validate_command(probe.command)
                if len(probe.command.argv)==2 and probe.command.argv[1] in ('--version','-v'):
                    raise ValueError('Smoke deve executar a aplicação/biblioteca, não somente informar a versão do runtime.')
        self.e.announce('delivery_analysis','Levantando pré-requisitos e comandos reais de instalação da entrega.')
        value=await self.e.invoke('reviewer','delivery_plan',self.context(),DeliveryPlan,analysis,check)
        observed={d.name:d for d in value.prerequisites}
        observed.update({name:DeliveryRequirement.model_validate(v) for name,v in self.record.get('observed_prerequisites',{}).items()})
        value.prerequisites=list(observed.values())
        self.save(plan=value.model_dump(),plan_key=key)
        return value

    def document(self,plan,installs,checks,status):
        # O documento é derivado do inventário, não de uma afirmação de sucesso do modelo.
        current=manifest(self.project)
        previous=self.e.saved['accepted_tree']
        if any(current.get(n)!=previous.get(n) for n in current.keys()|previous.keys() if source_name(n)):
            raise BuildError('Os arquivos da aplicação mudaram durante a validação de entrega; foram preservados.')
        render_readme(self.project,plan,installs,checks,status,self.applied,self.record.get('diagnostics',[]))
        updated=manifest(self.project)
        self.e.saved['accepted_tree']=updated
        if self.work:
            (self.work.root/'README.md').write_bytes((self.project/'README.md').read_bytes())
            self.work.base['README.md']=updated['README.md'];self.work.base_root['README.md']=updated['README.md']
            self.work.save()
        self.record['plan_key']=self.plan_key()
        self.save()

    def clean_root(self,recipe):
        saved=self.record.get('clean',{})
        if saved.get('recipe')==recipe:
            path=Path(saved['path']).resolve()
            marker=path/'.harness/delivery-owner.json'
            try:owner=json.loads(marker.read_text())
            except (OSError,ValueError):owner={}
            valid=(not path.is_relative_to(self.project.resolve()) and path.name.startswith('capivara2-install-')
                and owner=={'owner':'capivara2','id':saved.get('id'),'project':str(self.project.resolve())})
            if valid and path.is_dir() and all(source_tree(path).get(n)==h for n,h in source_tree(self.root).items()):return path
        # Fora da árvore original: Node busca node_modules nas pastas ancestrais,
        # mesmo sem NODE_PATH. Uma cópia sob .harness poderia reutilizar o projeto pai.
        path=Path(tempfile.mkdtemp(prefix='capivara2-install-')).resolve();identifier=uuid4().hex
        copy_source(self.root,path)
        from .artifacts import atomic_write
        atomic_write(path/'.harness/delivery-owner.json',json.dumps({'owner':'capivara2','id':identifier,'project':str(self.project.resolve())}))
        if saved:self.record.setdefault('clean_history',[]).append(saved)
        self.record['clean']={'recipe':recipe,'path':str(path),'id':identifier,'source_key':source_key(self.root),'results':{}}
        self.save()
        return path

    async def validate(self,plan,installs,checks):
        recipe=fingerprint({'source':source_key(self.root),'host':self.record.get('host_key'),'install':[c.model_dump() for c in installs],
            'checks':[c.model_dump() for c in checks],'startup':[p.model_dump() for p in plan.startup]})
        root=self.clean_root(recipe);results=self.record['clean']['results']
        before=source_tree(self.root)
        self.record['diagnostics']=[]
        for stage,commands in (('install',installs),('compile',checks),('startup',plan.startup)):
            for index,spec in enumerate(commands):
                item=f'{stage}-{index}';expected=fingerprint(spec.model_dump())
                diagnostic=(stage=='compile' and ((len(spec.argv)>=3 and spec.argv[1:3]==['run','typecheck']) or spec.argv[:2]==['node','node_modules/typescript/bin/tsc'] or any(c.kind=='typecheck' and c.command.argv==spec.argv for c in plan.checks)))
                if results.get(item,{}).get('command_key')==expected:
                    if results[item]['exit_code']==0:continue
                    if results[item].get('diagnostic'):
                        self.record['diagnostics'].append(results[item]);continue
                self.record['execution_sequence']=self.record.get('execution_sequence',0)+1
                prefix=f'log/delivery-{self.store.run_id}-{self.record["execution_sequence"]:04d}-{item}'
                self.e.announce('delivery_'+stage,{'install':'Instalando em cópia sem dependências herdadas.',
                    'compile':'Conferindo compilação e tipagem da entrega.',
                    'startup':'Conferindo a inicialização real da aplicação.'}[stage])
                if stage=='startup':outcome=await startup(spec,root,self.store,prefix)
                else:outcome=await run_command(spec,root,self.store,prefix,clean=True)
                results[item]={**outcome,'command_key':expected,'command':spec.model_dump(),'log':prefix,'diagnostic':diagnostic}
                self.save(status='validating')
                if outcome['exit_code']!=0 and diagnostic:
                    self.record['diagnostics'].append({'command':spec.model_dump(),'exit_code':outcome['exit_code'],
                        'observed':(outcome['stderr']+'\n'+outcome['stdout'])[-8000:],'diagnostic':True})
                    self.save()
                    continue
                if outcome['exit_code']!=0:
                    return {'stage':stage,'source_key':source_key(self.root),'command':spec.model_dump(),'exit_code':outcome['exit_code'],
                        'observed':(outcome['stderr']+'\n'+outcome['stdout'])[-16000:],'log':prefix}
                after=source_tree(root)
                altered=[n for n,h in before.items() if after.get(n)!=h]
                if altered:return {'stage':stage,'command':spec.model_dump(),'exit_code':1,
                    'observed':'O procedimento alterou arquivos de origem: '+', '.join(altered)+'. Inclua a versão correta na fonte antes de validar a entrega.'}
        return None

    async def diagnose(self,failure):
        self.record['diagnosis_sequence']=self.record.get('diagnosis_sequence',0)+1
        data={**self.context(),'failure':failure,'failure_context':failure_context(failure,self.record,self.root),
              'file_evidence':repair_evidence(self.work,[*self.work.changes()[0],*self.e.scopes[REPAIR].writes]) if self.work else None,'current_plan':self.record.get('plan'),'sequence':self.record['diagnosis_sequence'],
              'previous_failures':self.record.get('failures',[])[-3:]}
        analysis=self.store.path(f'delivery/diagnosis-{uuid4().hex[:12]}');copy_source(self.root,analysis)
        def check(value):
            if value.evidence not in failure['observed']:raise ValueError('Cite literalmente uma evidência da saída real recebida.')
            if value.kind=='host_dependencies' and failure['stage']!='install' and any(t in failure['observed'] for t in ('TS2688','Cannot find type definition','ERR_MODULE_NOT_FOUND','ModuleNotFoundError')):
                raise ValueError('Falha de tipos/importação exige corrigir declaração, instalação local ou procedimento; não tratá-la como simples pré-requisito global pendente.')
            for name in value.repair_files:relative_path(name)
            for dep in value.dependencies:validate_command(dep.check,host=True)
        self.e.announce('delivery_diagnosis','Diagnosticando a falha real de instalação/compilação/inicialização.')
        result=await self.e.invoke('reviewer','delivery_diagnosis',data,DeliveryDiagnosis,analysis,check)
        self.store.json(f'delivery/diagnoses/{self.record["diagnosis_sequence"]:04d}.json',result.model_dump())
        return result

    def setup_repair(self,diagnosis):
        e=self.e
        number=e.source['plan']['phases'][-1]['number']
        goal='Corrigir a instalação limpa, dependências e comandos operacionais da aplicação preservando seus requisitos.'
        e.tasks[REPAIR]={'id':REPAIR,'phase_number':number,'context_policy':'fresh',
            'seed':{'scope':goal,'source_quotes':[goal],'coverage':[]},
            'content':{'title':'Instalação e entrega operacional','objective':goal,
                'implementation_steps':[diagnosis.instruction,'Preservar o comportamento solicitado; comprovar instalação, build e inicialização reais.'],
                'deliverables':['Manifestos e arquivos operacionais necessários para instalar e iniciar a aplicação.']}}
        previous=e.scopes.get(REPAIR)
        writes=list(dict.fromkeys([*(previous.writes if previous else []),*diagnosis.repair_files]))
        e.scopes[REPAIR]=TaskScope(task_id=REPAIR,kind='setup',writes=writes,reads=[*source_tree(self.project),'README.md'],depends_on=[],requirement_ids=[])
        e.progress.setdefault(REPAIR,{'gates':['waiting']*4,'started':True,'completed':False,'attempts':0})
        if not self.work:
            self.work=Workspace(self.project,self.store,REPAIR,runtimes=False,source_only=True)
            self.record['repair_workspace']=str(self.work.root)
        self.record['repair_scope']=e.scopes[REPAIR].model_dump();self.record['repair_task']=e.tasks[REPAIR]
        self.record['repair_diagnosis']=diagnosis.model_dump()
        self.save()

    async def reconsider_repair(self,feedback,failure):
        self.record['repair_feedback']=feedback
        evidence={**failure,'observed':failure['observed']+'\nReparo anterior: '+json.dumps(feedback,ensure_ascii=False)}
        decision=await self.diagnose(evidence)
        scope=self.e.scopes[REPAIR]
        if decision.kind!='review_context':
            for name in decision.repair_files:
                if name not in scope.writes:scope.writes.append(name)
        self.record['repair_diagnosis']=decision.model_dump()
        self.record['repair_scope']=scope.model_dump()
        self.e.progress[REPAIR]['diagnosis_sequence']=self.record['diagnosis_sequence']
        if decision.kind=='host_dependencies':
            plan=DeliveryPlan.model_validate(self.record['plan'])
            merged={d.name:d for d in plan.prerequisites};merged.update({d.name:d for d in decision.dependencies})
            plan.prerequisites=list(merged.values())
            self.record.setdefault('observed_prerequisites',{}).update({d.name:d.model_dump() for d in decision.dependencies})
            self.save(plan=plan.model_dump())
            installs=installation(self.root,plan);checks=compilation(self.root,plan)
            self.document(plan,installs,checks,'reparo em andamento; conferindo dependências')
            await self.ensure_host(plan,installs,checks)
        elif decision.kind=='review_context':
            self.record['review_context_files']=list(dict.fromkeys([*self.record.get('review_context_files',[]),*decision.repair_files]))
            self.record['skip_next_write']=True
        elif decision.kind=='procedure':
            self.record['procedure_revision']=self.record.get('procedure_revision',0)+1
            self.save(plan_key=None,procedure_failure={'failure':evidence,'instruction':decision.instruction})
            await self.plan()
        self.save()
        return {**feedback,'diagnosis':decision.model_dump()}

    def review_candidate(self):
        # Uma candidata já reprovada na execução precisa de novo reparo, não nova opinião.
        if self.record.get('repair_approved_key')==source_key(self.work.root):return None
        candidate=self.record.get('review_candidate')
        if candidate and candidate.get('source_key')==source_key(self.work.root):
            return WriterResult.model_validate(candidate['result'])
        p=self.e.progress[REPAIR]
        if p.get('gates',[])[:2]==['completed','completed']:
            changes,tree=self.work.audit(self.e.scopes[REPAIR])
            files=[name for name in changes if name in tree]
            if files:
                return WriterResult(summary='Retomar a candidata existente dos gates G0/G1; ainda exige revisão e execução.',files=files)
        return None

    def preserve_runtime_data(self):
        current=manifest(self.work.root)
        for name in self.work.base.keys()|current.keys():
            if source_name(name):continue
            if name in current:self.work.base[name]=current[name]
            else:self.work.base.pop(name,None)
        self.work.save()

    async def repair(self,diagnosis,failure):
        self.setup_repair(diagnosis)
        self.record['repair_failure']=failure
        self.save()
        e=self.e;p=e.progress[REPAIR];scope=e.scopes[REPAIR]
        self.preserve_runtime_data()
        scope.writes=[n for n in scope.writes if source_name(n)]
        feedback={'delivery_failure':failure,'diagnosis':diagnosis.model_dump()}
        candidate=self.review_candidate()
        while True:
            before=manifests(self.work.root)
            if candidate is not None:
                result=candidate;candidate=None
                self.record['skip_next_write']=False
            else:
                p['completed']=False;p['attempts']+=1;p['gates']=['running','waiting','waiting','waiting'];e.gate(REPAIR,0,'running')
                self.record['repair_approved_key']=None;self.save()
                before=manifests(self.work.root)
                data={**e.code_context(REPAIR,self.work,feedback),'manifests':before,'readiness_contract':self.record.get('plan'),'delivery_repair_attempt':p['attempts']}
                def valid_result(value):
                    for name in value.files:relative_path(name)
                    e.validate_scope_requests(value.scope_requests,REPAIR,self.work,feedback)
                try:result=await e.invoke('writer','delivery_repair',data,WriterResult,self.work.root,valid_result)
                except DiagnosisPending:
                    feedback=dict(feedback,invalid_contract=p.get('failed_contract'))
                    feedback=await self.reconsider_repair(feedback,failure)
                    p['diagnosis_pending']=False;p.pop('failed_contract',None)
                    continue
            changes,tree=self.work.changes()
            if 'README.md' in changes:
                self.store.write(f'delivery/readme-drafts/{self.store.run_id}-{p["attempts"]}.md',(self.work.root/'README.md').read_text() if (self.work.root/'README.md').exists() else '')
                shutil.copy2(self.project/'README.md',self.work.root/'README.md')
                result.files=[name for name in result.files if name!='README.md']
                changes,tree=self.work.changes()
                feedback=dict(feedback,readme_policy='O inventário atualiza README automaticamente; corrija a causa em manifestos/configuração/código.')
            for name in [*changes,*result.files]:
                relative_path(name)
                if not matches(name,scope.writes):scope.writes.append(name)
            for item in result.scope_requests:
                relative_path(item.file)
                if item.file not in scope.writes:scope.writes.append(item.file)
            if result.scope_requests and not result.files:
                feedback=dict(feedback,scope_granted=scope.writes);continue
            changes,tree=self.work.audit(scope)
            await asyncio.to_thread(self.work.sync_sources)
            e.gate(REPAIR,0,'completed');e.gate(REPAIR,1,'running')
            if not result.files or any(not (self.work.root/name).is_file() for name in result.files):
                feedback=dict(feedback,required_change='Aplique uma alteração real e entregue os arquivos necessários para resolver a falha.')
                if p['attempts']%2==0:feedback=await self.reconsider_repair(feedback,failure)
                await e.recover('delivery_repair','O escritor precisa entregar o reparo operacional.',p['attempts'],REPAIR)
                continue
            e.gate(REPAIR,1,'completed');e.gate(REPAIR,2,'running')
            self.record['review_candidate']={'source_key':source_key(self.work.root),'result':result.model_dump()}
            self.save()
            if operational_only(changes):
                review=DeliveryRepairReview(approved=True,preserves_functionality=True,preserves_validation=True,
                    reason='Reparo operacional sem alteração de código de negócio; aceite depende dos comandos reais do G3.')
                self.record['last_delivery_review']={'kind':'operational_execution','source_key':source_key(self.work.root),
                    'files':changes,'basis':'Execução real de instalação, build e início no G3; sem aprovação textual extra.'}
                self.save()
            else:
                requested=self.record.setdefault('review_context_files',[])
                while True:
                    evidence=repair_evidence(self.work,[*changes,*result.files,*scope.writes],requested=requested)
                    self.record['review_sequence']=self.record.get('review_sequence',0)+1
                    def validate_review(value):
                        for name in value.context_files:relative_path(name)
                        if value.needs_context and not value.context_files:
                            raise ValueError('Indique os arquivos exatos de contexto que faltam; não devolver uma alegação genérica de falta de evidência.')
                        provided={f['file']:f for f in evidence['files']}
                        if value.needs_context and all(n in provided and not provided[n]['before']['truncated'] and not provided[n]['after']['truncated'] for n in value.context_files):
                            raise ValueError('Os arquivos solicitados já estão em file_evidence com conteúdo base/atual, diff e hashes completos. Leia essa evidência; não solicitar ao escritor dados que o harness já forneceu.')
                    review=await e.invoke('reviewer','delivery_repair_review',{
                        **e.code_context(REPAIR,self.work,feedback),'before_manifests':before,
                        'original_manifests':manifests(self.project),'after_manifests':manifests(self.work.root),
                        'changed_files':changes,'file_evidence':evidence,'failure':failure,
                        'failure_context':failure_context(failure,self.record,self.work.root),
                        'delivery_repair_attempt':p['attempts'],'review_sequence':self.record['review_sequence'],
                        'baseline_directory':str(self.work.root/'.harness/baseline')},DeliveryRepairReview,self.work.root,validate_review)
                    self.record['last_delivery_review']=review.model_dump();self.save()
                    if not review.needs_context:break
                    requested[:]=list(dict.fromkeys([*requested,*review.context_files]))
                    self.record['skip_next_write']=True;self.save()
                    e.announce('delivery_review_context','Complementando o contexto do revisor, preservando o código candidato.',task_id=REPAIR)
            if not review.approved:
                e.gate(REPAIR,2,'rejected');feedback=dict(feedback,review=review.reason)
                if p['attempts']%2==0:feedback=await self.reconsider_repair(feedback,failure)
                if self.record.get('skip_next_write'):candidate=self.review_candidate()
                else:self.record.pop('review_candidate',None)
                await e.recover('delivery_repair',review.reason,p['attempts'],REPAIR);continue
            e.gate(REPAIR,2,'completed');e.gate(REPAIR,3,'running')
            self.record['repair_scope']=scope.model_dump();self.record['repair_review']=review.model_dump()
            self.record['repair_approved_key']=source_key(self.work.root)
            self.save(plan_key=None)
            return

    def restore_repair(self):
        if not self.work:return
        e=self.e
        if self.record.get('repair_task'):
            e.tasks[REPAIR]=self.record['repair_task']
            e.scopes[REPAIR]=TaskScope.model_validate(self.record['repair_scope'])
            e.progress.setdefault(REPAIR,{'gates':['completed','completed','completed','waiting'],'completed':False,'started':True,'attempts':0})

    async def ensure_host(self,plan,installs,checks):
        e=self.e
        try:
            control=self.store.path('delivery/host-install');control.mkdir(parents=True,exist_ok=True)
            dependencies=[d.model_copy(update={'manual_instructions':d.manual_instructions+'\n\nComandos manuais:\n'+'\n'.join(shlex.join(c.argv) for c in d.manual_commands)}) for d in plan.prerequisites]
            verified=await e.ensure_dependencies(dependencies,control)
            self.save(host_key=fingerprint({'checks':verified,'platform':self.host()}))
        except DependenciesBlocked as exc:
            missing=self.store.read_json('control/missing-dependencies.json',[])
            names={v['dependency']['name'] for v in missing}
            commands=[c for d in plan.prerequisites if d.name in names for c in d.manual_commands]
            if commands:
                self.store.write('install-manual.sh','#!/usr/bin/env bash\nset -euo pipefail\n'+
                    '\n'.join(shlex.join(c.argv).replace('sudo -n ','sudo ') for c in commands)+'\n')
                os.chmod(self.store.path('install-manual.sh'),0o700)
            self.document(plan,installs,checks,'código concluído; instalação pendente por pré-requisitos' if all(p.get('completed') for p in e.progress.values()) else 'reparo em andamento; instalação pendente por pré-requisitos')
            self.save(status='blocked')
            self.store.json('delivery/approval.json',{'status':'blocked','run_id':self.store.run_id})
            if all(p.get('completed') for p in e.progress.values()):
                raise DeliveryDependenciesPending(str(exc)) from exc
            raise

    async def run(self):
        e=self.e;self.restore_repair()
        previous=self.store.read_json('delivery/approval.json')
        if previous:self.store.json(f'delivery/history/{self.store.run_id}.json',previous)
        self.store.json('delivery/approval.json',{'status':'pending','run_id':self.store.run_id})
        while True:
            plan=await self.plan()
            try:installs=installation(self.root,plan);checks=compilation(self.root,plan)
            except BuildError as exc:
                failure={'stage':'install','observed':str(exc),'exit_code':1,'command':{}}
            else:
                self.document(plan,installs,checks,'validação em andamento')
                await self.ensure_host(plan,installs,checks)
                self.save(status='validating')
                if self.work and self.record.get('repair_approved_key')!=source_key(self.work.root):
                    await self.repair(DeliveryDiagnosis.model_validate(self.record['repair_diagnosis']),self.record['repair_failure'])
                    continue
                failure=await self.validate(plan,installs,checks)
                if failure is None:
                    if self.work:
                        scope=e.scopes[REPAIR]
                        e.progress[REPAIR].update(stage='publish',workspace=str(self.work.root),tested_tree=manifest(self.work.root))
                        self.save()
                        changes,accepted=await asyncio.to_thread(self.work.publish,scope,manifest(self.work.root))
                        e.saved['accepted_tree']=accepted;e.progress[REPAIR].update(completed=True,stage='completed',published=changes)
                        e.gate(REPAIR,3,'completed')
                        self.store.json(f'tasks/{REPAIR}/approval.json',{'task_id':REPAIR,'gates':['completed']*4,
                            'review':self.record['repair_review'],'published':changes,'proof':'delivery/approval.json'})
                        self.record.pop('repair_workspace',None);self.work=None
                    self.document(plan,installs,checks,'instalação, compilação e inicialização aprovadas')
                    self.save(status='approved',source_key=source_key(self.project),
                        readme_sha256=digest((self.project/'README.md').read_text()))
                    self.store.json('delivery/approval.json',self.record)
                    self.e.announce('delivery_approved','Entrega aprovada após instalação limpa e execução dos comandos operacionais.')
                    return self.record
            self.record.setdefault('failures',[]).append(failure);self.record['failures']=self.record['failures'][-8:]
            self.save(status='repairing')
            diagnosis=await self.diagnose(failure)
            if diagnosis.kind=='host_dependencies':
                merged={d.name:d for d in plan.prerequisites};merged.update({d.name:d for d in diagnosis.dependencies})
                self.record.setdefault('observed_prerequisites',{}).update({d.name:d.model_dump() for d in diagnosis.dependencies})
                plan.prerequisites=list(merged.values());self.save(plan=plan.model_dump())
            elif diagnosis.kind=='project':await self.repair(diagnosis,failure)
            elif diagnosis.kind=='procedure':
                self.record['procedure_revision']=self.record.get('procedure_revision',0)+1
                self.save(plan_key=None,procedure_failure={'failure':failure,'instruction':diagnosis.instruction})
            else:await e.recover('delivery_retry',diagnosis.reason,len(self.record['failures']))


async def validate_delivery(engine):
    return await DeliveryGate(engine).run()
