"""Arquivos privados, sanitização e processos exclusivos da integração REA."""

import asyncio
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from capivara2ptn.change_source import Secrets,SECRET_NAME
from capivara2ptn.change_source import ChangeError
from capivara2ptn.change_state import file_lock
from capivara2ptn.technical_verification import _stop


class ReaError(Exception):
    pass


class Safe:
    def __init__(self, root, target=None, tokens=()):
        self.filters = [Secrets(root)]
        if target:
            self.filters.append(Secrets(target if target.is_dir() else target.parent))
        self.tokens = set(tokens)
        self.tokens.update(v for k,v in os.environ.items() if v and re.search(SECRET_NAME,k,re.I))
    def text(self, value):
        for token in sorted(self.tokens,key=len,reverse=True):
            if token:value=value.replace(token,'[OCULTO]')
        for filter in self.filters:value=filter.prose(value)
        return value
    def value(self, value):
        if isinstance(value,str):return self.text(value)
        if isinstance(value,list):return [self.value(item) for item in value]
        if isinstance(value,dict):return {key:self.value(item) for key,item in value.items()}
        return value


def checked(path):
    path=Path(path).absolute()
    for candidate in (path,*path.parents):
        if candidate.is_symlink():raise ReaError('O controle do REA não pode atravessar links: '+str(path.name))
    return path


def write_text(path, value):
    path=checked(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.rea-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(value);stream.flush();os.fsync(stream.fileno())
        os.replace(name,checked(path))
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path,value):write_text(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')


@contextmanager
def locked(path):
    try:
        with file_lock(path):yield
    except ChangeError as error:raise ReaError(str(error).replace('CHANGE','REA')) from None


def read_json(path, default=None):
    try:
        value=json.loads(checked(path).read_text(encoding='utf-8'))
    except FileNotFoundError:return default
    except (OSError,ValueError,UnicodeError):raise ReaError('Registro REA inválido: '+Path(path).name) from None
    if not isinstance(value,dict):raise ReaError('Registro REA não é um objeto: '+Path(path).name)
    return value


async def run(argv,cwd,env,log,safe,*,input_text=None,separate_stderr=False):
    """Sem shell, com cancelamento do grupo e saída real sanitizada."""
    process=await asyncio.create_subprocess_exec(*map(str,argv),cwd=cwd,env=env,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE if separate_stderr else asyncio.subprocess.STDOUT,
        start_new_session=True,limit=32*1024*1024)
    output=[]
    async def errors():
        async for raw in process.stderr:
            if log:log(safe(raw.decode('utf-8',errors='replace')).rstrip())
    stderr=asyncio.create_task(errors()) if separate_stderr else None
    try:
        if input_text is not None:
            try:process.stdin.write(input_text.encode());await process.stdin.drain()
            except (BrokenPipeError,ConnectionResetError):pass
            finally:process.stdin.close()
        async for raw in process.stdout:
            text=raw.decode('utf-8',errors='replace');output.append(text)
            if log:log(safe(text).rstrip())
        code=await process.wait()
        if stderr:await stderr
        return code,''.join(output)
    finally:
        if process.returncode is None or asyncio.current_task().cancelling():await _stop(process)
        if stderr:
            if not stderr.done():stderr.cancel()
            await asyncio.gather(stderr,return_exceptions=True)


def server_environment(extra=None):
    # Credenciais dos modelos não são necessárias ao servidor de análise local.
    env={k:v for k,v in os.environ.items() if k=='REA_REFERENCE_SECRET_PATTERNS_JSON' or not re.search(SECRET_NAME,k,re.I)}
    for key in ('SSH_AUTH_SOCK','GPG_AGENT_INFO'):env.pop(key,None)
    for key in ('HOPPER_TARGET_PATH','HOPPER_TARGET_KIND','HOPPER_LOADER_ARGS_JSON'):env.pop(key,None)
    env.update(extra or {});env.update(NO_COLOR='1',REA_LOG_LEVEL='info')
    return env
