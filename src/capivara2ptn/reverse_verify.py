"""Verificação opcional autorizada em cópia descartável, sem rede ou origem montada."""

import asyncio
import os
import shlex
import shutil
import signal
import tempfile
from pathlib import Path

from capivara2ptn.reverse_source import ReverseError, read_source, redact, relevant, digest, known_secrets


async def verify(store, inv: dict, command: str, log) -> dict:
    executable = shutil.which('bwrap')
    secrets = known_secrets(Path(store.state['source']), inv)
    def safe(text):
        for value in secrets:
            text = text.replace(value, '[OCULTO]')
        return redact(text)
    record = {'command': safe(command), 'inventory': inv['sha256'], 'status': 'unavailable',
              'limitation': '', 'artifact': ''}
    if not executable:
        record['limitation'] = 'Bubblewrap indisponível; nenhuma execução prática foi iniciada.'
        return record
    argv = shlex.split(command)
    if not argv:
        raise ReverseError('Comando de verificação vazio.')
    # A cópia é criada no destino e só contém texto sanitizado; sem .env, links ou bancos.
    parent = store.target('execucoes')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='verificacao-', dir=parent) as temporary:
        scratch = Path(temporary)
        for entry in relevant(inv):
            raw = read_source(Path(store.state['source']), entry['path'])
            if digest(raw) != entry['sha256']:
                raise ReverseError('Legado mudou antes da verificação. Retome para atualizar.')
            target = scratch / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(safe(raw.decode(entry['encoding'])), encoding='utf-8')
            target.chmod(0o700 if entry.get('mode', 0) & 0o111 else 0o600)
        sandbox = [executable, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
                   '--setenv', 'PATH', '/usr/local/bin:/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
                   '--setenv', 'LANG', 'C.UTF-8']
        for directory in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
            if Path(directory).exists():
                sandbox += ['--ro-bind', directory, directory]
        sandbox += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                    '--bind', str(scratch), '/work', '--chdir', '/work', '--', *argv]
        process = await asyncio.create_subprocess_exec(*sandbox, cwd=store.destination,
                    env={'PATH': os.defpath}, start_new_session=True,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output = []
        try:
            # read() não impõe limite de linha; aplicação pode emitir linhas extensas.
            while chunk := await process.stdout.read(8192):
                output.append(chunk)
            code = await process.wait()
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        text = safe(b''.join(output).decode('utf-8', errors='replace'))
        artifact = f"evidencias/verificacao-{len(store.state['verification']) + 1}.txt"
        store.write(artifact, text)
        log(text)
        record.update(status='observed' if code == 0 else 'failed', exit_code=code, artifact=artifact,
                      limitation='' if code == 0 else 'Verificação falhou no isolamento; comportamento prático não confirmado.')
        return record
