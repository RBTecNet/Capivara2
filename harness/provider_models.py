"""Catálogos dos CLIs Claude e AGY, consultados sem enviar prompts ao modelo."""
import json
import os
from pathlib import Path
import re
import time

from .artifacts import atomic_write,digest
from .domain import Model,PROVIDERS
from .opencode_models import query


def parse_claude_models(text):
    entries=None
    for line in text.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict) or event.get('type')!='control_response':continue
        response=event.get('response',{})
        if response.get('request_id')!='capivara-models':continue
        if response.get('subtype')!='success':raise ValueError('Claude recusou a consulta de metadados.')
        entries=response.get('response',{}).get('models')
    if not isinstance(entries,list):raise ValueError('Claude não retornou seu catálogo de modelos.')
    models={}
    for entry in entries:
        if not isinstance(entry,dict):continue
        identifier=entry.get('resolvedModel') or entry.get('value')
        if not isinstance(identifier,str) or not identifier or identifier=='default':continue
        name=entry.get('displayName',identifier)
        levels=entry.get('supportedEffortLevels',[]) if entry.get('supportsEffort',True) else []
        levels=levels if isinstance(levels,list) else []
        efforts=tuple(dict.fromkeys(['default',*(e for e in levels if isinstance(e,str) and e)]))
        models[identifier]=Model(identifier,name if isinstance(name,str) else identifier,efforts)
    if not models:raise ValueError('Nenhum modelo compatível informado pelo Claude.')
    return tuple(models.values())


def parse_agy_models(text):
    families={};ungrouped={}
    for line in text.splitlines():
        match=re.fullmatch(r'([\w][\w./:-]*)(?:\t+| {2,})(.+)',line.strip())
        if not match:continue
        identifier,name=match.groups()
        variant=re.fullmatch(r'(.+)-(low|medium|high|xhigh|max)',identifier)
        if variant:
            base,effort=variant.groups()
            display=re.sub(r'\s*\((?:Low|Medium|High|XHigh|Max)\)\s*$','',name,flags=re.I)
            group=families.setdefault(base,{'name':display,'efforts':[]})
            if effort not in group['efforts']:group['efforts'].append(effort)
        else:ungrouped[identifier]=Model(identifier,name,('default',))
    models=[Model(key,value['name'],tuple(['default',*(e for e in ('low','medium','high','xhigh','max') if e in value['efforts'])])) for key,value in families.items()]
    models+=list(ungrouped.values())
    if not models:raise ValueError('AGY não retornou modelos reconhecíveis.')
    return tuple(models)


async def discover(provider,cwd):
    name=PROVIDERS[provider]
    root=Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache')))/'capivara2'
    path=root/f'{provider}-models-{digest(str(cwd.resolve()))[:20]}.json'
    try:
        data=json.loads(path.read_text())
        models=[]
        for item in data['models']:
            if (not isinstance(item['id'],str) or not item['id'] or not isinstance(item['name'],str)
                    or not isinstance(item['efforts'],list) or not item['efforts']
                    or any(not isinstance(e,str) or not e for e in item['efforts'])):raise ValueError('Cache inválido.')
            models.append(Model(item['id'],item['name'],tuple(item['efforts'])))
        saved=data['saved_at']
        if not isinstance(saved,(float,int)):raise ValueError('Cache inválido.')
    except (OSError,ValueError,KeyError,TypeError):models=[];saved=0
    if models and saved>time.time()-60:return tuple(models),f'Modelos e efforts do {name} · cache recente do CLI'
    try:
        if provider=='agy':
            output=await query(['agy','models'],cwd,timeout=30,provider_name=name)
            current=parse_agy_models(output)
        elif provider=='claude':
            request={'type':'control_request','request_id':'capivara-models',
                     'request':{'subtype':'initialize','hooks':None}}
            command=['claude','-p','--input-format','stream-json','--output-format','stream-json','--verbose',
                '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                '--settings','{"disableAllHooks":true}','--setting-sources','','--no-session-persistence']
            output=await query(command,cwd,input_text=json.dumps(request)+'\n',provider_name=name)
            current=parse_claude_models(output)
        else:raise ValueError('Provedor sem descoberta de CLI.')
    except (OSError,ValueError,TimeoutError) as exc:
        if models:return tuple(models),f'Último catálogo válido do {name} · consulta atual indisponível'
        raise ValueError(f'Não foi possível listar modelos do {name}. Confira a instalação e autenticação do CLI; Enter tenta novamente.') from exc
    safe={'saved_at':time.time(),'models':[{'id':m.id,'name':m.name,'efforts':list(m.efforts)} for m in current]}
    try:atomic_write(path,json.dumps(safe,ensure_ascii=False,indent=2)+'\n')
    except OSError:pass
    return current,f'Modelos e efforts informados pelo CLI do {name}'
