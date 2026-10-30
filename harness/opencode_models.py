"""Descoberta somente de metadados do OpenCode, sem ler/exportar credenciais."""

import asyncio
import json
import os
from pathlib import Path
import re
import signal
import time

from .artifacts import atomic_write, digest
from .domain import Model


def parse_model_info(payload):
    entries=payload.get('data',payload) if isinstance(payload,dict) else payload
    if not isinstance(entries,list):raise ValueError('Resposta de modelos inválida.')
    result={}
    for item in entries:
        if not isinstance(item,dict) or item.get('enabled',True) is False or item.get('type','chat')!='chat':continue
        provider=item.get('providerID');identifier=item.get('id')
        if not isinstance(provider,str) or not isinstance(identifier,str) or not provider or not identifier:continue
        full=identifier if identifier.startswith(provider+'/') else provider+'/'+identifier
        variants=item.get('variants',[])
        variants=[v.get('id') if isinstance(v,dict) else v for v in variants] if isinstance(variants,list) else list(variants) if isinstance(variants,dict) else []
        efforts=tuple(dict.fromkeys(['default',*(v for v in variants if isinstance(v,str) and v and v!='default')]))
        name=item.get('name')
        result[full]=Model(full,name if isinstance(name,str) and name else identifier,efforts)
    if not result:raise ValueError('O OpenCode não retornou modelos habilitados.')
    return tuple(sorted(result.values(),key=lambda m:(m.name.casefold(),m.id)))


def parse_model_lines(text):
    ids=dict.fromkeys(line.strip() for line in text.splitlines()
        if re.fullmatch(r'[\w.-]+/[^\s\x1b]+',line.strip()))
    if not ids:raise ValueError('Não foi possível interpretar a listagem do OpenCode.')
    return tuple(Model(identifier,identifier.split('/',1)[1],('default',)) for identifier in ids)


async def query(argv,cwd,timeout=20,*,input_text=None,provider_name="OpenCode"):
    process=await asyncio.create_subprocess_exec(*argv,cwd=cwd,stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,
        env=dict(os.environ,PWD=str(cwd.resolve()),NO_COLOR='1',CI='1'))
    try:
        stdout,_=await asyncio.wait_for(process.communicate(input_text.encode() if input_text is not None else None),timeout)
    except (asyncio.CancelledError,TimeoutError):
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),3)
            except TimeoutError:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
        raise
    if process.returncode:raise ValueError(f'O CLI {provider_name} não conseguiu consultar os modelos. Confira sua conexão/autenticação.')
    return stdout.decode('utf-8',errors='replace')


def cache_path(cwd):
    root=Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache')))/'capivara2'
    return root/f'opencode-models-{digest(str(cwd.resolve()))[:20]}.json'


async def discover(cwd):
    path=cache_path(cwd)
    try:cached=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):cached={}
    if not isinstance(cached,dict):cached={}
    def models_from_cache():
        models=[]
        for m in cached.get('models',[]):
            if (not isinstance(m,dict) or not isinstance(m.get('id'),str) or '/' not in m['id']
                    or not isinstance(m.get('name'),str) or not isinstance(m.get('efforts'),list)
                    or not m['efforts'] or any(not isinstance(e,str) for e in m['efforts'])):
                raise ValueError('Cache de modelos inválido.')
            models.append(Model(m['id'],m['name'],tuple(m['efforts'])))
        return tuple(models)
    try:cached_models=models_from_cache()
    except (TypeError,ValueError):cached_models=()
    saved_at=cached.get('saved_at',0)
    if isinstance(saved_at,(float,int)) and saved_at>time.time()-60 and cached_models:
        return cached_models,'Modelos e variantes do OpenCode autenticado · cache recente'
    try:
        try:
            output=await query(['opencode','api','--standalone','model.list'],cwd)
            models=parse_model_info(json.loads(output))
        except (OSError,ValueError,TimeoutError):
            output=await query(['opencode','models'],cwd)
            models=parse_model_lines(output)
        # Nunca armazenar headers, body, tokens ou configuração retornados pela API.
        safe={'saved_at':time.time(),'models':[{'id':m.id,'name':m.name,'efforts':list(m.efforts)} for m in models]}
        try:atomic_write(path,json.dumps(safe,ensure_ascii=False,indent=2)+'\n')
        except OSError:pass  # Catálogo válido continua disponível em cache somente na memória.
        return models,'Modelos e variantes consultados no OpenCode autenticado'
    except (OSError,ValueError,TimeoutError) as exc:
        models=cached_models
        if models:return models,'Último catálogo válido do OpenCode · consulta atual indisponível'
        raise ValueError('Não foi possível listar modelos do OpenCode. Confira se o CLI está instalado, autenticado e acessível; Enter permite tentar novamente.') from exc
