"""Solicitações independentes do CHANGE, checkpoints atômicos e exclusão mútua."""

import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from capivara2ptn.change_source import ChangeError, ChangeConflict, fingerprint, sha, validate_project
from capivara2ptn.workspace import ensure_harness

STATUSES = {'draft', 'investigation', 'interview', 'planning', 'approval', 'execution', 'verification',
            'scope_review', 'conflict', 'interrupted', 'failed', 'done', 'done_with_limits', 'cancelled'}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def file_lock(path):
    if path.is_symlink():
        raise ChangeError('O bloqueio do CHANGE não pode ser um link.')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ChangeError('Já existe uma solicitação CHANGE em execução neste projeto.') from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ChangeError('Já existe uma solicitação CHANGE em execução neste projeto.') from None
        yield
    finally:
        os.close(fd)


class ChangeStore:
    def __init__(self, root):
        self.root = root.resolve()
        self.base = self.root / '.harness/change'
        self.identifier = None
        self.state = None
        self.started = None

    def base_path(self, relative):
        path = self.base / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ChangeError('Caminho interno inválido do CHANGE.')
        for candidate in (path, *path.parents):
            if candidate == self.root.parent:
                break
            if candidate.is_symlink():
                raise ChangeError('Arquivos de controle do CHANGE não podem atravessar links.')
        return path

    def path(self, relative):
        if not self.identifier:
            raise ChangeError('Selecione uma solicitação CHANGE.')
        return self.base_path(f'solicitacoes/{self.identifier}/{relative}')

    def prepare(self):
        validate_project(self.root)
        ensure_harness(self.root)
        self.base_path('solicitacoes').mkdir(mode=0o700, parents=True, exist_ok=True)

    def atomic(self, path, text):
        self.base_path(path.relative_to(self.base).as_posix())
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.change-', dir=path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            self.base_path(path.relative_to(self.base).as_posix())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def write(self, relative, text):
        self.atomic(self.path(relative), text)

    def write_json(self, relative, value):
        self.write(relative, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def read_json(self, relative):
        path = self.path(relative)
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (ValueError, OSError, UnicodeError):
            raise ChangeError('Registro inválido ou ilegível do CHANGE: ' + relative) from None
        if not isinstance(value, dict):
            raise ChangeError('Registro do CHANGE deve ser um objeto: ' + relative)
        return value

    def create(self, original):
        self.prepare()
        if not original.strip():
            raise ChangeError('Descreva a alteração desejada.')
        with file_lock(self.base_path('catalogo.lock')):
            numbers = [int(p.name.split('-')[1]) for p in self.base_path('solicitacoes').iterdir()
                       if re.fullmatch(r'change-\d+', p.name) and not p.is_symlink()]
            self.identifier = f'change-{max(numbers, default=0) + 1:03d}'
            self.path('').mkdir(mode=0o700)
            self.state = {'version': 1, 'id': self.identifier, 'project': str(self.root), 'original': original,
                          'created': now(), 'status': 'draft', 'phase': 'investigation', 'elapsed': 0.0,
                          'error': '', 'read_files': {}, 'knowledge': [], 'history': [], 'question': None,
                          'interview_done': False, 'investigation': None, 'plan': None, 'approval': None,
                          'tasks': [], 'attempts': {}, 'verification': None, 'limitations': [], 'events': [], 'expected': {}}
            self.write('pedido.md', original + '\n')
            self.save()
        return self.state

    def select(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'change-\d+', identifier):
            raise ChangeError('Identificador de solicitação inválido.')
        self.identifier = identifier
        state = self.read_json('estado.json')
        try:
            if (not state or state['version'] != 1 or state['id'] != identifier or state['project'] != str(self.root)
                    or state['status'] not in STATUSES or state['phase'] not in STATUSES
                    or not isinstance(state['tasks'], list) or not isinstance(state['read_files'], dict)
                    or not isinstance(state['history'], list) or not isinstance(state['original'], str)
                    or type(state['elapsed']) not in (float, int) or not math.isfinite(state['elapsed']) or state['elapsed'] < 0):
                raise ValueError
        except (TypeError, KeyError, ValueError):
            raise ChangeError('Estado da solicitação inválido; resultados preservados.') from None
        self.state = state
        return state

    def requests(self):
        directory = self.base_path('solicitacoes')
        if not directory.exists():
            return []
        values = []
        for path in sorted(directory.iterdir()):
            if re.fullmatch(r'change-\d+', path.name):
                if path.is_symlink():
                    raise ChangeError('Uma solicitação salva é um link.')
                try:
                    value = json.loads(self.base_path(f'solicitacoes/{path.name}/estado.json').read_text())
                    values.append((path.name, str(value.get('original', ''))[:70], value.get('status', 'failed')))
                except (ValueError, OSError):
                    values.append((path.name, 'Registro ilegível; arquivos preservados', 'failed'))
        return values

    def save(self):
        if self.state is not None:
            self.checkpoint()
            self.write_json('estado.json', self.state)

    def event(self, kind, message):
        self.state['events'].append({'at': now(), 'kind': kind, 'message': message})
        self.save()

    def log(self, message):
        path = self.path('execucao.log')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(message + '\n')

    def start_clock(self):
        if self.started is None:
            self.started = monotonic()

    def checkpoint(self):
        if self.started is not None:
            current = monotonic()
            self.state['elapsed'] += current - self.started
            self.started = current

    def pause_clock(self):
        self.checkpoint()
        self.started = None
        self.save()

    def elapsed(self):
        return (self.state or {}).get('elapsed', 0.0) + (monotonic() - self.started if self.started is not None else 0.0)

    @contextmanager
    def execution(self):
        self.prepare()
        with file_lock(self.base_path('execucao.lock')):
            yield

    def check_sources(self):
        expected = {**self.state['read_files'], **self.state['expected']}
        conflicts = [p for p, old in expected.items() if fingerprint(self.root, p) != old]
        if conflicts:
            raise ChangeConflict(conflicts)

    def check_approval(self):
        plan = self.state['plan']
        approved = self.state['approval']
        if not plan or not approved or approved['sha256'] != sha(json.dumps(plan, ensure_ascii=False, sort_keys=True)):
            raise ChangeError('Não há aprovação válida para este plano.')
        definitions = {task['id']: task for task in plan['tasks']}
        if len(self.state['tasks']) != len(definitions) or any(task['id'] not in definitions or
                any(task.get(k) != v for k, v in definitions[task['id']].items()) for task in self.state['tasks']):
            raise ChangeError('As tasks divergem do plano aprovado. Nenhum código foi escrito.')
        path = self.path('plano.md')
        if path.is_symlink() or not path.exists() or sha(path.read_bytes()) != approved['markdown_sha256']:
            raise ChangeError('O plano aprovado foi alterado ou removido. Nenhum código foi escrito.')

    def refresh_conflicts(self, paths):
        if self.state.get('pending_apply'):
            journal_name = self.state['pending_apply']
            journal = self.read_json(journal_name)
            if not journal or not isinstance(journal.get('files'), list):
                raise ChangeError('Transação interrompida ilegível; preserve os arquivos para recuperação.')
            paths = sorted(set(paths) | {item['path'] for item in journal['files']})
            journal['status'] = 'conflict_preserved'
            journal['actual_fingerprints'] = {path: fingerprint(self.root, path) for path in paths}
            self.write_json(journal_name, journal)
            self.state.pop('pending_apply')
        self.state['stale_paths'] = paths
        self.state['read_files'] = {p: h for p, h in self.state['read_files'].items() if p not in paths}
        self.state['expected'] = {p: h for p, h in self.state['expected'].items() if p not in paths}
        for item in self.state['knowledge']:
            if set(item.get('paths', [])) & set(paths):
                item['stale'] = True
        for task in self.state['tasks']:
            if set(task['files']) & set(paths):
                task.update(status='pending', writer='pending', g0='pending', g1='pending', g2='pending',
                            error='Código alterado externamente; revisar no novo plano.')
        self.state.update(approval=None, phase='investigation', status='investigation', error='')
        self.event('conflict_refresh', 'Reexaminar somente arquivos afetados: ' + ', '.join(paths))
