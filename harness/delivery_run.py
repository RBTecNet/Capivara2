"""Execução real e isolada dos comandos de entrega, incluindo smoke HTTP curto."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import http.client

from .build_commands import run_command
from .build_models import CommandSpec


def validate_command(spec,*,host=False):
    if not host and Path(spec.argv[0]).is_absolute() and any(p in ('node_modules','.venv','venv','__pypackages__') for p in Path(spec.argv[0]).parts):
        raise ValueError('Use o ambiente relativo criado na cópia limpa, não um runtime instalado de outro workspace.')


def delivery_environment(root):
    env=dict(os.environ)
    for name in ('NODE_PATH','NODE_OPTIONS','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','CONDA_PREFIX'):
        env.pop(name,None)
    env['PATH']=os.pathsep.join(p for p in env.get('PATH','').split(os.pathsep)
        if not any(part in ('node_modules','.venv','venv','__pypackages__') for part in Path(p).parts))
    env.update(CI='1',NO_COLOR='1',PWD=str(root.resolve()))
    return env


async def startup(probe,root,store,prefix):
    validate_command(probe.command)
    spec=probe.command.model_copy(update={'timeout':min(probe.command.timeout,60)})
    executable=Path(spec.argv[0])
    local_python=(root/executable).absolute() if not executable.is_absolute() else executable
    local_environment=local_python.is_relative_to(root.absolute()) and (local_python.parent.parent/'pyvenv.cfg').is_file()
    if executable.name in ('python','python3') and not local_environment:
        runtime=root/'.harness/python-runtime'
        interpreter=runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not interpreter.exists():
            create=CommandSpec(argv=[spec.argv[0],'-m','venv','--without-pip',str(runtime)],purpose='Isolar o smoke de pacotes globais do Python.',timeout=60)
            created=await run_command(create,root,store,prefix+'-python-runtime',clean=True)
            if created['exit_code']!=0:return created
        spec=spec.model_copy(update={'argv':[str(interpreter),*spec.argv[1:]]})
    if probe.mode=='exit':
        result=await run_command(spec,root,store,prefix,clean=True)
        if result['exit_code']==0 and probe.expected_stdout is not None and result['stdout'].strip()!=probe.expected_stdout.strip():
            result=dict(result,exit_code=1,stderr=result['stderr']+'\nA saída da inicialização difere da esperada.')
        return result
    if not probe.path.startswith('/') or '://' in probe.path:raise ValueError('O smoke HTTP só pode acessar o processo local iniciado pelo harness.')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    argv=[arg.replace('{port}',str(port)) for arg in spec.argv]
    env=delivery_environment(root);env.update({v.name:v.value.replace('{port}',str(port)) for v in spec.env})
    for name in ('NODE_PATH','NODE_OPTIONS','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','CONDA_PREFIX'):env.pop(name,None)
    scratch=root/'.harness/delivery-runtime';scratch.mkdir(parents=True,exist_ok=True)
    env.update(PORT=str(port),DATABASE_PATH=str(scratch/'smoke.sqlite'),CAPIVARA2_TEST_DIR=str(scratch))
    store.json(prefix+'.command.json',{'argv':argv,'purpose':probe.command.purpose,'cwd':str(root),'timeout':probe.command.timeout})
    try:
        process=await asyncio.create_subprocess_exec(*argv,cwd=root,env=env,stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
    except OSError as exc:return {'exit_code':127,'stdout':'','stderr':str(exc),'timeout':False}
    collecting=asyncio.create_task(process.communicate());passed=False;detail='O serviço não respondeu no prazo.'
    def request():
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=.5)
        try:
            connection.request('GET',probe.path)
            response=connection.getresponse()
            return response.status,response.read(65536).decode(errors='replace')
        except (OSError,ValueError,http.client.HTTPException):return None,''
        finally:connection.close()
    try:
        deadline=asyncio.get_running_loop().time()+min(probe.command.timeout,60)
        while asyncio.get_running_loop().time()<deadline:
            if process.returncode is not None:
                detail=f'O serviço encerrou antes de responder (saída {process.returncode}).';break
            status,body=await asyncio.to_thread(request)
            if status==probe.status and (probe.expected_text is None or probe.expected_text in body):passed=True;break
            if status is not None:detail=f'O serviço respondeu HTTP {status}, esperado {probe.status}.'
            await asyncio.sleep(.1)
    finally:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:stdout,stderr=await asyncio.wait_for(asyncio.shield(collecting),3)
        except TimeoutError:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            stdout,stderr=await collecting
        store.write(prefix+'.stdout.log',stdout.decode(errors='replace'))
        store.write(prefix+'.stderr.log',stderr.decode(errors='replace'))
    result={'exit_code':0 if passed else 1,'stdout':stdout.decode(errors='replace'),
            'stderr':stderr.decode(errors='replace')+('' if passed else '\n'+detail),'timeout':False}
    store.json(prefix+'.result.json',{'exit_code':result['exit_code'],'timeout':False,'http_path':probe.path})
    return result
