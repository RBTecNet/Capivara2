"""Evidência de revisão capturada pelo controlador, vinculada aos arquivos reais."""
import difflib
import hashlib
from pathlib import Path

from .build_workspace import relative_path
from .delivery_source import source_key


def file_state(path,limit):
    if not path.is_file():return {'exists':False,'sha256':None,'bytes':0,'content':None,'truncated':False}
    data=path.read_bytes()
    return {'exists':True,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
        'content':data[:limit].decode('utf-8',errors='replace'),'truncated':len(data)>limit}


def repair_evidence(workspace,paths,*,requested=()):
    requested=set(requested)
    names=list(dict.fromkeys([*paths,*sorted(requested)]))
    entries=[]
    for name in names:
        name=relative_path(name)
        limit=1048576 if name in requested else 65536
        before=file_state(workspace.root/'.harness/baseline'/name,limit)
        after=file_state(workspace.root/name,limit)
        difference=''.join(difflib.unified_diff((before['content'] or '').splitlines(True),
            (after['content'] or '').splitlines(True),fromfile='base/'+name,tofile='candidate/'+name))
        entries.append({'file':name,'before':before,'after':after,'diff':difference})
    return {'candidate_source_key':source_key(workspace.root),'files':entries,
        'authority':'Bytes e hashes lidos pelo harness. before é a base preservada; after é a candidata atual. Não são alegações do escritor.'}


def failure_context(failure,record,current_root):
    failed=failure.get('source_key')
    clean=record.get('clean',{})
    if not failed and any(r.get('log')==failure.get('log') for r in clean.get('results',{}).values()):
        path=Path(clean.get('path',''))
        # Só inferir a origem de recibos legados quando a cópia registrada ainda existe.
        if path.is_dir() and (path/'.harness/delivery-owner.json').is_file():failed=clean.get('source_key') or source_key(path)
    current=source_key(current_root)
    return {'failed_source_key':failed,'candidate_source_key':current,
        'applies_to_candidate':current==failed if failed else None,
        'policy':'A saída é histórica se a fonte mudou ou é desconhecida. Use-a para entender o reparo; o G3 executará novos comandos após o G2. Ausência de log novo não reprova, por si só, preservação de código.'}


def operational_only(paths):
    """Configuração/dependências/testes não exigem outra opinião sobre estilo de código."""
    if not paths:return True
    known={'package.json','package-lock.json','pnpm-lock.yaml','yarn.lock','requirements.txt','pyproject.toml',
        'uv.lock','Cargo.toml','Cargo.lock','go.mod','go.sum','composer.json','composer.lock','Gemfile','Gemfile.lock'}
    for name in paths:
        path=Path(name);base=path.name
        if name in known or base.startswith(('tsconfig','vite.config','vitest.config','playwright.config')):continue
        if '.test.' in base or '.spec.' in base or path.parts[0] in ('tests','test','e2e'):continue
        return False
    return True
