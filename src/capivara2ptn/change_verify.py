"""Verificação direta do CHANGE, pelo executor técnico existente, sem gates."""

import shlex
import time

from capivara2ptn.change_source import ChangeError, app_path
from capivara2ptn.technical_verification import execute


def verification_spec(value, root):
    if not isinstance(value, dict) or not isinstance(value.get('commands'), list):
        raise ChangeError('Informe os comandos de verificação técnica em verification.commands.')
    commands = []
    for item in value['commands']:
        if not isinstance(item, dict):raise ChangeError('Comando de verificação inválido.')
        argv, directory = item.get('argv'), item.get('directory', '.')
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or '\x00' in arg for arg in argv) or not argv[0].strip():
            raise ChangeError('A verificação exige executável e argumentos textuais explícitos.')
        if directory != '.':
            if not app_path(root, directory).is_dir():raise ChangeError('Diretório de verificação inexistente.')
        commands.append({'directory': directory, 'argv': argv, 'purpose': str(item.get('purpose', 'Verificação localizada.'))})
    limitation = value.get('limitation')
    if limitation is not None and not isinstance(limitation, str):raise ChangeError('Limitação de verificação inválida.')
    if not commands and not (limitation or '').strip():
        raise ChangeError('Sem comandos seguros, documente a limitação de verificação; não declare sucesso.')
    return {'commands': commands, 'limitation': limitation}


async def verify_change(store, log, secrets):
    specification = verification_spec(store.state['direct']['result']['verification'], store.root)
    if not specification['commands']:
        return {'status': 'indisponivel', 'comandos': [], 'motivo': specification['limitation']}
    folder = store.path(f'verificacoes/run-{time.time_ns()}')
    folder.mkdir(mode=0o700, parents=True)
    config = {'comandos': [{'diretorio': c['directory'], 'comando': shlex.join(c['argv'])} for c in specification['commands']],
              'nao_aplicavel': None}
    try:
        result = await execute(store.root, config, folder, 'verificacao', log, secrets.prose)
        if specification['limitation']:result['limitacao'] = specification['limitation']
        return result
    finally:
        # O executor compartilhado permanece intacto. Sanitizar suas saídas locais,
        # inclusive quando o usuário interrompe o comando.
        for path in folder.iterdir():
            if path.is_file() and not path.is_symlink():
                store.atomic(path, secrets.prose(path.read_text(encoding='utf-8', errors='replace')))
        recorded = folder / 'verificacao.json'
        if recorded.exists():
            store.write_json('ultima-verificacao.json', store.read_json(recorded.relative_to(store.path('')).as_posix()))
