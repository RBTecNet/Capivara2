"""Contratos nativos dos CLIs; configuração efêmera e respostas finais explícitas."""
import json
import re


def json_config(text):
    # JSONC: remover comentários/vírgulas finais preservando strings, URLs e escapes.
    token=r'("(?:\\.|[^"\\])*")|/\*[\s\S]*?\*/|//[^\n]*'
    clean=re.sub(token,lambda m:m[1] if m[1] is not None else '',text)
    clean=re.sub(r'("(?:\\.|[^"\\])*")|,\s*(?=[}\]])',lambda m:m[1] if m[1] is not None else '',clean)
    value=json.loads(clean)
    if not isinstance(value,dict):raise ValueError('A configuração precisa ser um objeto JSON.')
    return value


def opencode_document_config(store,env):
    settings={'agents':{'capivara-documentation':{
        'mode':'primary','system':'Gere somente o JSON documental solicitado. Não use ferramentas.',
        'permissions':[{'action':'*','resource':'*','effect':'deny'}]}},
        'policies':[{'action':'tool.use','resource':'*','effect':'deny'}]}
    store.json('control/work/.opencode/opencode.json',settings)
    inherited=json_config(env.get('OPENCODE_CONFIG_CONTENT','{}'))
    agents=dict(inherited.get('agents',{}));agents.update(settings['agents'])
    inherited['agents']=agents
    inherited['policies']=[*inherited.get('policies',[]),*settings['policies']]
    # run resolve a sessão na raiz Git: a configuração aninhada pode não ser descoberta.
    # Conteúdo herdado fica somente no ambiente do filho; nunca exportar suas credenciais.
    env['OPENCODE_CONFIG_CONTENT']=json.dumps(inherited)


def agy_command(selection,schema,*,allow_tools):
    settings={'artifactReviewPolicy':'always-proceed','statusLine':False,'title':False}
    if not allow_tools:
        settings['permissions']={'deny':[name+'(*)' for name in (
            'command','read_file','write_file','read_url','execute_url','mcp','unsandboxed')]}
    command=['agy','--input-format','stream-json','--output-format','stream-json',
        '--disable-slash-commands','--print-timeout','0','--settings',json.dumps(settings),
        '--json-schema',json.dumps(schema)]
    if allow_tools:command+=['--dangerously-skip-permissions']
    if selection.model!='default':command+=['--model',selection.model]
    if selection.effort!='default':command+=['--effort',selection.effort]
    return command,'agy'


def safe_error(message):
    text=str(message)
    text=re.sub(r'(?i)((?:api[_-]?key|token|authorization)\s*[:=]\s*)(?:bearer\s+)?[^\s,;}]+',r'\1[oculto]',text)
    text=re.sub(r'(?i)(bearer\s+)[^\s,;}]+',r'\1[oculto]',text)
    text=re.sub(r'\b(?:sk-[\w-]{16,}|gh[pousr]_[\w]{20,}|github_pat_[\w]+)\b','[oculto]',text)
    text=re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@',r'\1[oculto]@',text)
    return text[:600]


def events(stdout):
    try:whole=json.loads(stdout)
    except ValueError:whole=None
    if isinstance(whole,dict):return [whole]
    if isinstance(whole,list):return [v for v in whole if isinstance(v,dict)]
    result=[]
    for line in stdout.splitlines():
        try:v=json.loads(line)
        except ValueError:continue
        if isinstance(v,dict):result.append(v)
    return result


def native_error(mode,stdout):
    for event in events(stdout):
        if mode=='opencode' and event.get('type')=='error':
            error=event.get('error',{})
            if isinstance(error,dict):
                return safe_error(str(error.get('type','error'))+': '+str(error.get('message','Falha no OpenCode.')))
            return safe_error(error)
        if mode=='claude' and (event.get('is_error') or event.get('type')=='result' and event.get('subtype','success')!='success'):
            errors=event.get('errors') or event.get('result') or event.get('subtype')
            return safe_error('; '.join(map(str,errors)) if isinstance(errors,list) else errors)
        if mode=='agy':
            result=event.get('result') if event.get('event')=='result' else event if 'status' in event else None
            if isinstance(result,dict) and result.get('status')!='SUCCESS':
                return safe_error(result.get('error') or result.get('status','Falha no AGY.'))
    return None


def tool_event(event):
    if event.get('type') in ('tool_use','tool_call') or event.get('event') in ('tool_use','tool_call','tool_start'):return True
    update=event.get('step_update')
    if event.get('event')=='step_update' and isinstance(update,dict) and update.get('step_type')=='tool':return True
    message=event.get('message',{})
    if isinstance(message,dict):
        content=message.get('content')
        return isinstance(content,list) and any(isinstance(block,dict) and block.get('type')=='tool_use' for block in content)
    return False


def native_result(mode,stdout,*,allow_tools):
    error=native_error(mode,stdout)
    if error:raise ValueError(error)
    stream=events(stdout)
    if not allow_tools and any(tool_event(e) for e in stream):
        raise ValueError('O agente tentou usar ferramentas durante a execução documental.')
    if mode=='opencode':
        text=[e['part']['text'] for e in stream if e.get('type')=='text' and isinstance(e.get('part'),dict) and isinstance(e['part'].get('text'),str)]
        if text:return '\n'.join(text)
    elif mode=='claude':
        result=next((e for e in reversed(stream) if e.get('type')=='result' or 'structured_output' in e or 'result' in e),None)
        if result:
            structured=result.get('structured_output')
            if isinstance(structured,dict):return json.dumps(structured,ensure_ascii=False)
            if isinstance(result.get('result'),str) and result['result'].strip():return result['result']
    elif mode=='agy':
        result=next((e['result'] for e in reversed(stream) if e.get('event')=='result' and isinstance(e.get('result'),dict)),None)
        if result is None:result=next((e for e in reversed(stream) if 'status' in e),None)
        if result and result.get('status')=='SUCCESS':
            if isinstance(result.get('structured_output'),dict):return json.dumps(result['structured_output'],ensure_ascii=False)
            if isinstance(result.get('response'),str) and result['response'].strip():return result['response']
    raise ValueError('O CLI não entregou uma resposta final válida. Consulte os logs da chamada.')
