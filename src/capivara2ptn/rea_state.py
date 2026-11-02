"""Checkpoints e evidências REA por projeto, sem tocar estados dos outros módulos."""

import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from time import monotonic

from capivara2ptn.rea_common import ReaError,checked,read_json,write_json,locked
from capivara2ptn.workspace import ensure_harness


def now():return datetime.now(timezone.utc).isoformat()


def fingerprint(target,project):
    """Somente identidade de arquivos para retomada; análise é exclusiva do MCP."""
    target=Path(target).resolve(strict=True);project=Path(project).resolve()
    if not target.is_file() and not target.is_dir():raise ReaError('Selecione um arquivo regular ou diretório existente.')
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if target.is_file():return {'kind':'file','sha256':digest(target),'mode':stat.S_IMODE(target.stat().st_mode)}
    files={}
    for parent,dirs,names in os.walk(target,followlinks=False):
        parent=Path(parent)
        links=[d for d in dirs if (parent/d).is_symlink()]
        dirs[:]=sorted(d for d in dirs if not (parent/d).is_symlink() and d!='.git' and (parent/d)!=project/'.harness')
        for name in sorted([*names,*links]):
            path=parent/name
            if path.is_symlink():files[path.relative_to(target).as_posix()]='link:'+hashlib.sha256(os.readlink(path).encode()).hexdigest()
            elif path.is_file():files[path.relative_to(target).as_posix()]=str(stat.S_IMODE(path.stat().st_mode))+':'+digest(path)
    return {'kind':'directory','files':files}


class ReaStore:
    def __init__(self,project):
        self.project=Path(project).resolve();self.base=self.project/'.harness/rea'
        self.state=None;self.started=None
    def path(self,name):
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ReaError('Caminho de controle REA inválido.')
        return checked(self.base/name)
    def write(self,name,value):
        ensure_harness(self.project);write_json(self.path(name),value)
    def read(self,name,default=None):return read_json(self.path(name),default)
    def load(self):
        state=self.read('estado.json')
        if state and (state.get('version')!=1 or state.get('project')!=str(self.project) or not isinstance(state.get('calls'),list)):
            raise ReaError('Estado REA inválido; dados preservados.')
        self.state=state;return state
    def save(self):
        if self.state:
            if self.started is not None:
                current=monotonic();self.state['elapsed']+=current-self.started;self.started=current
            self.write('estado.json',self.state)
    def start_clock(self):
        if self.started is None:self.started=monotonic()
    def pause_clock(self):self.save();self.started=None
    def elapsed(self):return (self.state or {}).get('elapsed',0)+(monotonic()-self.started if self.started is not None else 0)
    def event(self,kind,message):
        self.state['history'].append({'at':now(),'kind':kind,'message':message});self.save()
    def log(self,text):
        self.path('').mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.path('execucao.log')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(text+'\n')
    def begin(self,target,request,signature):
        if self.state:
            if self.state['target']!=str(target) or self.state['request']!=request:
                raise ReaError('Alvo/pedido difere da investigação salva. Use Nova investigação para preservar a anterior.')
            if self.state.get('fingerprint')==signature:return
            self.archive()
        self.state={'version':1,'project':str(self.project),'target':str(target),'request':request,'fingerprint':signature,
            'status':'pending','operation':'setup','elapsed':0.0,'error':'','history':[],'calls':[],'pending':None,
            'model_attempts':0,'feedback':'','documentation':None,'bundle':None,'active_target':None,'limitations':[]}
        self.event('created','Investigação REA registrada; nenhum motor interno utilizado.')
    def archive(self):
        if not self.base.exists():self.state=None;return
        self.pause_clock()
        folder=self.path('historico/'+now().replace(':','-'));folder.mkdir(parents=True,mode=0o700)
        for path in list(self.base.iterdir()):
            if path.name in {'historico','execucao.lock'}:continue
            checked(path);shutil.move(str(path),folder/path.name)
        self.state=None
    @contextmanager
    def execution(self):
        ensure_harness(self.project);self.path('').mkdir(parents=True,exist_ok=True,mode=0o700)
        with locked(self.path('execucao.lock')):yield
    def record(self,name,args,result):
        index=len(self.state['calls'])+1;path=f'evidencias/chamada-{index:05d}.json'
        self.write(path,{'tool':name,'arguments':args,'result':result})
        checksum=hashlib.sha256(self.path(path).read_bytes()).hexdigest()
        self.state['calls'].append({'tool':name,'artifact':path,'sha256':checksum,'error':result.get('isError',False),'at':now()})
        self.state['pending']=None;self.save();return self.state['calls'][-1]
    def result(self,call):
        raw=self.path(call['artifact']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=call['sha256']:raise ReaError('Uma evidência REA foi alterada; retomada interrompida para preservar integridade.')
        return json.loads(raw)
