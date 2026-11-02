"""Verifica a cópia da aplicação com o executor existente, isolada da origem e da rede."""

import os
import shlex
import shutil
import time
from pathlib import Path

from capivara2ptn.change_source import ChangeError, IGNORED, BINARY, fingerprint, read_source, secret_file
from capivara2ptn.technical_verification import execute


def sandbox_command(executable, work, dependencies, directory, argv):
    command = [executable, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
               '--setenv', 'PATH', '/work/node_modules/.bin:/usr/local/bin:/usr/bin:/bin',
               '--setenv', 'HOME', '/tmp', '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    for path in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
        if Path(path).exists():
            command.extend(['--ro-bind', path, path])
    command.extend(['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--bind', str(work), '/work'])
    for source, relative in dependencies:
        command.extend(['--ro-bind', str(source), '/work/' + relative])
    command.extend(['--chdir', '/work' + ('' if directory == '.' else '/' + directory), '--', *argv])
    return shlex.join(command)


async def verify_change(store, log, secrets):
    specification = store.state['plan']['verification']
    if not specification['commands']:
        return {'status': 'indisponivel', 'comandos': [], 'motivo': specification['limitation']}
    executable = shutil.which('bwrap')
    if not executable:
        return {'status': 'indisponivel', 'comandos': [], 'motivo': 'Sem Bubblewrap disponível para executar verificações com segurança; nenhum comando foi executado no host.'}
    run = f'verificacoes/run-{time.time_ns()}'
    bundle = store.path(run)
    work, reports = bundle / 'projeto', bundle / 'relatorios'
    work.mkdir(mode=0o700, parents=True)
    reports.mkdir(mode=0o700)
    dependencies = []
    for parent, dirs, names in os.walk(store.root, followlinks=False):
        parent = Path(parent)
        (work / parent.relative_to(store.root)).mkdir(mode=0o700, parents=True, exist_ok=True)
        keep = []
        for name in sorted(dirs):
            path = parent / name
            if path.is_symlink():
                continue
            relative = path.relative_to(store.root).as_posix()
            if name in {'node_modules', '.venv', 'venv', 'vendor'}:
                dependencies.append((path, relative))
            elif name not in IGNORED:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(names):
            path = parent / name
            if name in IGNORED or path.is_symlink() or not path.is_file() or secret_file(path) or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}:
                continue
            relative = path.relative_to(store.root).as_posix()
            before = fingerprint(store.root, relative)
            raw = read_source(store.root, relative)
            if fingerprint(store.root, relative) != before:
                raise ChangeError('Arquivo mudou durante a cópia de verificação: ' + relative)
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() not in BINARY and b'\0' not in raw[:8192]:
                try:
                    raw = secrets.source(raw.decode('utf-8'), relative).encode('utf-8')
                except UnicodeError:
                    raw = secrets.source(raw.decode('latin-1'), relative).encode('latin-1')
            target.write_bytes(raw)
            target.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)
    commands, selected, unavailable = [], [], []
    for item in specification['commands']:
        argv = item['argv'][:]
        tool = Path(argv[0])
        if tool.is_absolute() and tool.is_relative_to(store.root):
            argv[0] = '/work/' + tool.relative_to(store.root).as_posix()
        elif not tool.is_absolute() and '/' in argv[0]:
            pass  # Executável do próprio projeto; sua ausência é uma falha real da entrega.
        else:
            found = shutil.which(argv[0], path=str(store.root / 'node_modules/.bin') + os.pathsep + os.environ.get('PATH', os.defpath))
            if not found:
                unavailable.append('Ferramenta não disponível: ' + argv[0])
                continue
            actual = Path(found).resolve()
            if actual.is_relative_to(store.root):
                argv[0] = '/work/' + Path(found).relative_to(store.root).as_posix()
            elif any(actual.is_relative_to(Path(p).resolve()) for p in ('/usr', '/bin', '/sbin', '/lib', '/lib64') if Path(p).exists()):
                argv[0] = str(actual)
            else:
                unavailable.append('Ferramenta fora dos diretórios acessíveis ao isolamento: ' + argv[0])
                continue
        selected.append(item)
        commands.append({'diretorio': 'projeto', 'comando': sandbox_command(executable, work, dependencies, item['directory'], argv)})
    try:
        if not commands:
            return {'status': 'indisponivel', 'comandos': [], 'motivo': '; '.join(unavailable)}
        result = await execute(bundle, {'comandos': commands, 'nao_aplicavel': None}, reports,
                               'verificacao', log, secrets.safe, context='Verificação localizada do CHANGE em cópia isolada.')
        for index, command in enumerate(result['comandos']):
            command['comando_executado_isolado'] = command['comando']
            command['comando'] = shlex.join(selected[index]['argv'])
            command['diretorio'] = selected[index]['directory']
            command['arquivo_saida'] = store.path(run + '/' + command['arquivo_saida']).relative_to(store.root).as_posix()
        if result['status'] == 'falha' and result['comandos'] and all(
                recheck_sandbox_failure(c.get('saida', '')) for c in result['comandos'] if c['codigo_retorno'] != 0):
            result.update(status='indisponivel', motivo='O sistema impediu criar o isolamento; nenhuma verificação foi executada fora dele.')
        if unavailable:
            result['limitacoes_ambiente'] = unavailable
            if result['status'] == 'sucesso':
                result.update(status='indisponivel', motivo='; '.join(unavailable))
        store.write_json(run + '/resultado.json', result)
        return result
    finally:
        # Só remove a cópia descartável criada pelo CHANGE, nunca a aplicação.
        shutil.rmtree(work)


def recheck_sandbox_failure(output):
    lower = output.lower()
    return lower.startswith('bwrap:') and any(word in lower for word in ('operation not permitted', 'permission denied', 'creating new namespace', 'no permissions'))
