"""Estado retomável do build e pré-requisito de plan aprovado e íntegro."""

from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path

from .artifacts import ArtifactStore, InitError, atomic_write, digest, now, read_status
from .plan_artifacts import load_init, plan_completed
from .plan_models import FinalPlan


class BuildError(InitError):
    pass


class DependenciesBlocked(BuildError):
    pass


def load_plan(project_dir):
    source = load_init(project_dir)
    if not plan_completed(project_dir,source['source_prompt_sha256']):
        raise BuildError('Conclua o plan e mantenha seus artefatos aprovados íntegros antes de executar o build.')
    store = ArtifactStore(project_dir)
    text = store.path('plan/final.json').read_text(encoding='utf-8')
    return {'plan':FinalPlan.model_validate_json(text).model_dump(), 'plan_sha256':digest(text),
            'init':source,'classification':store.read_json('plan/control/classification.json',{})}


class BuildStore(ArtifactStore):
    def __init__(self, project_dir):
        super().__init__(project_dir)
        self.project_dir=project_dir
        self.project_root=self.root
        self.root=self.root/'build'

    def begin(self,state):
        source=load_plan(self.project_dir)
        if self.root.is_symlink() or self.project_root.is_symlink():
            raise BuildError('Pastas de controle do build não podem ser links simbólicos.')
        marker=ArtifactStore(self.project_dir).read_json('owner.json')
        if not isinstance(marker,dict) or marker.get('owner')!='capivara2':
            raise BuildError('A pasta .harness não é reconhecida; foi preservada.')
        lock_path=self.project_root/'.init.lock'
        if lock_path.is_symlink(): raise BuildError('Controle de execução inválido.')
        self.lock=open(lock_path,'a',encoding='utf-8')
        os.chmod(lock_path,0o600)
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            raise BuildError('Já existe um módulo em execução para este projeto.') from None
        source=load_plan(self.project_dir)
        if state.prompt_source=='plan' and state.prompt and state.prompt != self.project_root.joinpath('plan/final.md').read_text(encoding='utf-8'):
            raise BuildError('O plan mudou após o wizard. Volte à seleção do módulo e escolha build novamente.')
        if self.root.exists() and any(self.root.iterdir()) and not self.path('state.json').is_file():
            raise BuildError('A pasta .harness/build contém material não reconhecido e foi preservada.')
        self.root.mkdir(mode=0o700,exist_ok=True)
        for directory in ('log','control','work','checks','tasks'):
            self.path(directory).mkdir(parents=True,mode=0o700,exist_ok=True)
        previous=self.read_json('state.json',{})
        if not isinstance(previous,dict) or (previous and previous.get('owner')!='capivara2'):
            raise BuildError('Estado de build não reconhecido; os arquivos foram preservados.')
        if previous.get('plan_sha256')!=source['plan_sha256']:
            previous={'owner':'capivara2','plan_sha256':source['plan_sha256'],'tasks':{},'accepted_tree':{},'blueprint':None}
        self.json('state.json',previous)
        self.json('source.json',source)
        self.json(f'control/settings-{self.run_id}.json',{'writer':asdict(state.writer),'reviewer':asdict(state.reviewer),
            'mode':state.build_mode,'max_agents':state.max_agents})
        data=read_status(self.project_dir)
        data['modules']['build']={'status':'running','run_id':self.run_id,'started_at':now(),
            'stage':'dependencies','plan_sha256':source['plan_sha256']}
        self.status(data)
        self.started=True
        return source,previous

    def status(self,data):
        atomic_write(self.project_root/'status.json',json.dumps(data,ensure_ascii=False,indent=2)+'\n')

    def event(self,stage,message,**details):
        path=self.path('log/events.jsonl')
        with path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':now(),'run_id':self.run_id,'stage':stage,'message':message,**details},ensure_ascii=False)+'\n')
        os.chmod(path,0o600)
        data=read_status(self.project_dir)
        data['modules']['build'].update(stage=stage,updated_at=now())
        self.status(data)

    def finish(self,status,**details):
        if self.started:
            data=read_status(self.project_dir)
            data['modules']['build'].update(status=status,finished_at=now(),**details)
            self.status(data)
