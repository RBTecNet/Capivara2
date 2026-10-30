"""Cópias isoladas, reservas conservadoras e publicação com controle de versão."""

import fnmatch
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from uuid import uuid4

from .artifacts import atomic_write, sync_directory
from .build_artifacts import BuildError

IGNORED = {'.harness','.git','node_modules','.venv','venv','__pycache__','.pytest_cache',
           '.mypy_cache','.ruff_cache','dist','build','target','coverage','test-results','playwright-report'}
RUNTIMES = ('node_modules','.venv','venv')


def relative_path(value, *, pattern=False):
    path=PurePosixPath(value)
    if (not value or path.is_absolute() or '\\' in value or '\0' in value or '~' in value
            or '..' in path.parts or path.parts[0] in ('.harness','.git')
            or (not pattern and any(c in value for c in '*?['))):
        raise BuildError(f'Caminho fora do escopo do projeto: {value!r}.')
    if path.name=='.env' or (path.name.startswith('.env.') and not path.name.endswith(('.example','.sample'))):
        raise BuildError('Use arquivos de exemplo de variáveis; não produzir credenciais ou .env real.')
    return path.as_posix()


def matches(path,patterns):
    return any(p in ('**','*') or fnmatch.fnmatchcase(path,p) for p in patterns)


def prefix(pattern):
    parts=[]
    for part in PurePosixPath(pattern).parts:
        if any(c in part for c in '*?['): break
        parts.append(part)
    return '/'.join(parts)


def overlap(left,right):
    a,b=prefix(left),prefix(right)
    return not a or not b or a==b or a.startswith(b+'/') or b.startswith(a+'/')


def conflicts(left,right):
    return (any(overlap(a,b) for a in left.writes for b in right.writes+right.reads)
            or any(overlap(a,b) for a in left.reads for b in right.writes))


def manifest(root):
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in IGNORED)
        for name in dirs:
            if (Path(base)/name).is_symlink():
                raise BuildError(f'Link simbólico em fonte não pode ser isolado com segurança: {Path(base)/name}.')
        for name in sorted(files):
            if name.startswith('.capivara-writing-') or name.endswith(('.pyc','.pyo')) or name=='.env' or (name.startswith('.env.') and not name.endswith(('.example','.sample'))):
                continue
            path=Path(base)/name
            if path.is_symlink():
                raise BuildError(f'Link simbólico em fonte não pode ser integrado como arquivo: {path}.')
            relative=path.relative_to(root).as_posix()
            result[relative]=hashlib.sha256(path.read_bytes()).hexdigest()+('x' if os.access(path,os.X_OK) else '')
    return result


def diff(before,after):
    return sorted(p for p in before.keys()|after.keys() if before.get(p)!=after.get(p))


def sync_parents(directory,root):
    while directory.is_relative_to(root):
        sync_directory(directory)
        if directory==root:break
        directory=directory.parent


def copy_project(root,destination, *, runtimes=True):
    destination.mkdir(parents=True,mode=0o700,exist_ok=True)
    snapshot=manifest(root)
    for name in snapshot:
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/name,target)
    if runtimes:
        for name in RUNTIMES:
            source=root/name
            if not source.exists(): continue
            target=destination/name
            if shutil.which('cp'):
                result=subprocess.run(['cp','-a','--reflink=auto',str(source),str(target)],capture_output=True)
                if result.returncode==0: continue
                if target.exists(): shutil.rmtree(target)
            shutil.copytree(source,target,symlinks=True)
    return snapshot


class Workspace:
    def __init__(self,project,store,task_id):
        self.project=project
        self.root=store.path(f'work/{task_id}-{uuid4().hex[:10]}')
        self.base_root=copy_project(project,self.root)
        self.base=manifest(self.root)
        (self.root/'.harness/checks').mkdir(parents=True,mode=0o700)
        self.frozen_checks={}
        self.save()

    def save(self):
        atomic_write(self.root/'.harness/workspace.json',json.dumps({
            'base_root':self.base_root,'base':self.base,
            'checks':{k:base64.b64encode(v).decode('ascii') for k,v in self.frozen_checks.items()}}))
        sync_directory(self.root)
        sync_directory(self.root.parent)

    @classmethod
    def resume(cls,project,store,path):
        root=Path(path).resolve()
        if not root.is_relative_to(store.path('work').resolve()):
            raise BuildError('A cópia salva da task está fora do controle do build.')
        data=json.loads((root/'.harness/workspace.json').read_text())
        workspace=cls.__new__(cls)
        workspace.project=project;workspace.root=root
        workspace.base_root=data['base_root'];workspace.base=data['base']
        workspace.frozen_checks={k:base64.b64decode(v) for k,v in data.get('checks',{}).items()}
        return workspace

    def sync_sources(self):
        for name in manifest(self.root):
            with (self.root/name).open('rb') as stream:os.fsync(stream.fileno())
            sync_parents((self.root/name).parent,self.root)
        self.save()

    def changes(self):
        after=manifest(self.root)
        return diff(self.base,after),after

    def audit(self,scope):
        changes,after=self.changes()
        invalid=[p for p in changes if not matches(p,scope.writes)]
        if invalid:
            raise BuildError('O escritor alterou arquivos fora da reserva: '+', '.join(invalid))
        for path in changes: relative_path(path)
        return changes,after

    def freeze_checks(self):
        directory=self.root/'.harness/checks'
        for path in directory.rglob('*'):
            if path.is_symlink(): raise BuildError('Checks não podem conter links simbólicos.')
            if path.is_file(): self.frozen_checks[path.relative_to(self.root).as_posix()]=path.read_bytes()
        self.save()

    def restore_checks(self):
        directory=self.root/'.harness/checks'
        if directory.is_symlink(): raise BuildError('Diretório de checks foi substituído por link.')
        for name,content in self.frozen_checks.items():
            target=self.root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.is_symlink(): target.unlink()
            target.write_bytes(content)

    def clear_bytecode(self):
        # Mesmo tamanho + gravação no mesmo segundo pode reutilizar .pyc antigo.
        for base,dirs,_ in os.walk(self.root):
            if '__pycache__' in dirs:
                shutil.rmtree(Path(base)/'__pycache__')
            dirs[:]=[d for d in dirs if d not in IGNORED]

    def publish(self,scope,expected_tree=None):
        changes,after=self.audit(scope)
        if expected_tree is not None and after!=expected_tree:
            raise BuildError('A cópia da task mudou após o G3; código não verificado não será integrado.')
        current=manifest(self.project)
        touched=diff(self.base_root,current)
        if any(matches(p,scope.writes+scope.reads) for p in touched):
            raise BuildError('A base da task mudou antes da integração. A cópia foi preservada para resolver o conflito, sem sobrescrever alterações.')
        # Todos os conflitos são verificados antes da primeira substituição.
        backups={p:(self.project/p).read_bytes() if (self.project/p).is_file() else None for p in changes}
        modes={p:(self.project/p).stat().st_mode for p in changes if (self.project/p).is_file()}
        self.sync_sources()
        transaction={'status':'prepared','before':current,'after':after,'changes':changes,
            'backups':{name:{'content':base64.b64encode(data).decode('ascii') if data is not None else None,
                             'mode':modes.get(name)} for name,data in backups.items()}}
        atomic_write(self.root/'.harness/publication.json',json.dumps(transaction))
        temporary_files=[]
        try:
            for name in changes:
                target=self.project/name
                if name in after:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temporary=target.with_name('.capivara-writing-'+uuid4().hex)
                    temporary_files.append(temporary)
                    shutil.copy2(self.root/name,temporary)
                    actual=hashlib.sha256(temporary.read_bytes()).hexdigest()+('x' if os.access(temporary,os.X_OK) else '')
                    if actual!=after[name]:raise BuildError('A fonte mudou durante a integração; alterações foram restauradas.')
                    with temporary.open('rb') as stream:os.fsync(stream.fileno())
                    os.replace(temporary,target)
                elif target.exists(): target.unlink()
                sync_parents(target.parent,self.project)
        except Exception:
            for name,data in backups.items():
                target=self.project/name
                if data is None: target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temporary=target.with_name('.capivara-writing-'+uuid4().hex)
                    temporary_files.append(temporary)
                    with temporary.open('wb') as stream:
                        stream.write(data);stream.flush();os.fsync(stream.fileno())
                    os.chmod(temporary,modes[name])
                    os.replace(temporary,target)
                sync_parents(target.parent,self.project)
            transaction['status']='aborted'
            atomic_write(self.root/'.harness/publication.json',json.dumps(transaction))
            raise
        finally:
            for path in temporary_files:path.unlink(missing_ok=True)
        transaction['status']='completed'
        atomic_write(self.root/'.harness/publication.json',json.dumps(transaction))
        return changes,manifest(self.project)

    def recover_publication(self,expected_tree):
        path=self.root/'.harness/publication.json'
        if not path.exists():return None
        transaction=json.loads(path.read_text())
        if transaction['status']=='aborted':return None
        if manifest(self.root)!=expected_tree or transaction['after']!=expected_tree:
            raise BuildError('A cópia aprovada mudou durante a integração interrompida; arquivos preservados.')
        current=manifest(self.project);before=transaction['before'];after=transaction['after']
        for name in transaction['changes']:
            if current.get(name) not in (before.get(name),after.get(name)):
                raise BuildError(f'Conflito ao retomar integração: {name} foi editado fora do harness. Cópias preservadas.')
        for name in transaction['changes']:
            if current.get(name)==after.get(name):continue
            target=self.project/name
            if name in after:
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_name('.capivara-writing-'+uuid4().hex)
                try:
                    shutil.copy2(self.root/name,temporary)
                    with temporary.open('rb') as stream:os.fsync(stream.fileno())
                    os.replace(temporary,target)
                finally:temporary.unlink(missing_ok=True)
            else:target.unlink(missing_ok=True)
            sync_parents(target.parent,self.project)
        transaction['status']='completed'
        atomic_write(path,json.dumps(transaction))
        accepted=dict(before)
        for name in transaction['changes']:
            if name in after:accepted[name]=after[name]
            else:accepted.pop(name,None)
        # Edições externas em outros arquivos continuam visíveis para a invalidação de reservas.
        return transaction['changes'],accepted
