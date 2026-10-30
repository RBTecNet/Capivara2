"""Comandos não interativos com evidência de saída, timeout e cancelamento."""

import asyncio
import os
from pathlib import Path
import signal


async def run_command(spec,cwd,store,prefix, *, test=False):
    argv=list(spec.argv)
    if Path(argv[0]).name=='sudo' and '-n' not in argv: argv.insert(1,'-n')
    env=dict(os.environ) if not test else {k:v for k,v in os.environ.items()
        if k in ('PATH','HOME','LANG','LC_ALL','DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','PLAYWRIGHT_BROWSERS_PATH')}
    env.update({v.name:v.value for v in spec.env})
    env.update(PWD=str(cwd.resolve()),CI='1',PIP_NO_INPUT='1',GIT_TERMINAL_PROMPT='0',DEBIAN_FRONTEND='noninteractive',NO_COLOR='1')
    if test:
        env['PYTHONDONTWRITEBYTECODE']='1'
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        scratch=cwd/'.harness/checks/tmp'
        scratch.mkdir(parents=True,exist_ok=True)
        env.update(CAPIVARA2_TEST_DIR=str(scratch),CAPIVARA2_TEST_PORT=str(port),TMPDIR=str(scratch),
                   CAPIVARA2_BUILD_TOOLS=str(store.path('tools')),NODE_PATH=str(store.path('tools/node_modules')))
    store.json(prefix+'.command.json',{'argv':argv,'purpose':spec.purpose,'cwd':str(cwd),'timeout':spec.timeout})
    try:
        process=await asyncio.create_subprocess_exec(*argv,cwd=cwd,env=env,stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
    except OSError as exc:
        result={'exit_code':127,'stdout':'','stderr':str(exc),'timeout':False}
    else:
        async def stop():
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),3)
                except TimeoutError:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.wait()
        try:
            stdout,stderr=await asyncio.wait_for(process.communicate(),spec.timeout)
            result={'exit_code':process.returncode,'stdout':stdout.decode(errors='replace'),
                    'stderr':stderr.decode(errors='replace'),'timeout':False}
        except asyncio.CancelledError:
            await stop();raise
        except TimeoutError:
            await stop()
            result={'exit_code':124,'stdout':'','stderr':'Tempo limite do comando excedido.','timeout':True}
    store.write(prefix+'.stdout.log',result['stdout'])
    store.write(prefix+'.stderr.log',result['stderr'])
    store.json(prefix+'.result.json',{'exit_code':result['exit_code'],'timeout':result['timeout']})
    return result
