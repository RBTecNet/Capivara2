"""Persistência exclusiva do REVERSE, com checkpoints atômicos e tempo próprio."""

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from capivara2ptn.reverse_source import ReverseError, digest, validate_roots
from capivara2ptn.workspace import ensure_harness


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReverseStore:
    def __init__(self, destination: Path) -> None:
        self.destination = destination.resolve()
        self.directory = self.destination / '.harness/reverse'
        self.state: dict | None = None
        self.started: float | None = None

    def target(self, relative: str) -> Path:
        path = self.directory / relative
        if not path.is_relative_to(self.directory) or '..' in Path(relative).parts:
            raise ReverseError('Destino inválido do REVERSE.')
        for part in [self.destination / '.harness', self.directory, *path.relative_to(self.directory).parents]:
            check = part if part.is_absolute() else self.directory / part
            if check.is_symlink():
                raise ReverseError('Resultados do REVERSE não podem atravessar links.')
        if path.is_symlink():
            raise ReverseError('Artefato do REVERSE não pode ser um link.')
        return path

    def read_json(self, relative: str) -> dict | None:
        path = self.target(relative)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            raise ReverseError(f'Artefato inválido ou ilegível: {relative}. Resultados preservados.') from None

    def load(self) -> dict | None:
        state = self.read_json('estado.json')
        if state is not None:
            try:
                if state['version'] != 1 or state['destination'] != str(self.destination):
                    raise ValueError
                if state['status'] not in {'pending', 'inventory', 'discovery', 'approval', 'investigation', 'consolidation', 'done', 'failed', 'interrupted'}:
                    raise ValueError
                if not isinstance(state['source'], str) or not isinstance(state['domains'], list) or not isinstance(state['units'], dict):
                    raise ValueError
                if not math.isfinite(state['elapsed']) or state['elapsed'] < 0:
                    raise ValueError
                for domain in state['domains']:
                    if not isinstance(domain['files'], list) or not isinstance(domain['id'], str):
                        raise ValueError
                for record in state['units'].values():
                    self.target(record['artifact'])
            except (KeyError, TypeError, ValueError):
                raise ReverseError('Estado do REVERSE inválido. Nenhum resultado foi sobrescrito.') from None
        self.state = state
        return state

    def write(self, relative: str, text: str) -> None:
        path = self.target(relative)
        ensure_harness(self.destination)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.reverse-', dir=path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.target(relative))
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def write_json(self, relative: str, value: dict) -> None:
        self.write(relative, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def save(self) -> None:
        if self.state is not None:
            self.checkpoint()
            self.write_json('estado.json', self.state)

    def begin(self, source: Path) -> dict:
        dest, source = validate_roots(self.destination, source)
        if self.state is None:
            if self.directory.exists() and any(self.directory.iterdir()):
                if any(p.name not in {'historico', 'prompts'} for p in self.directory.iterdir()):
                    raise ReverseError('Há artefatos do REVERSE sem estado válido. Resultados preservados.')
            self.state = {'version': 1, 'destination': str(dest), 'source': str(source),
                          'status': 'pending', 'stage': 'inventory', 'error': '', 'elapsed': 0.0,
                          'domains': [], 'approved': False, 'discovery': {}, 'units': {},
                          'files': {}, 'history': [], 'limitations': [], 'verification': []}
        elif self.state['source'] != str(source):
            raise ReverseError('O legado selecionado difere da investigação salva. Confirme Refazer antes de trocar a origem.')
        self.state['error'] = ''
        self.state['history'].append({'at': now(), 'event': 'start'})
        self.save()
        return self.state

    def start_clock(self) -> None:
        if self.started is None:
            self.started = monotonic()

    def checkpoint(self) -> None:
        if self.started is not None and self.state is not None:
            current = monotonic()
            self.state['elapsed'] += current - self.started
            self.started = current

    def pause_clock(self) -> None:
        self.checkpoint()
        self.started = None
        self.save()

    def elapsed(self) -> float:
        return (self.state or {}).get('elapsed', 0.0) + (monotonic() - self.started if self.started is not None else 0.0)

    def fail(self, message: str, interrupted: bool = False) -> None:
        if self.state is None or self.state['status'] == 'done':
            return
        self.state.update(status='interrupted' if interrupted else 'failed', error=message)
        self.state['history'].append({'at': now(), 'event': self.state['status'], 'message': message})
        self.pause_clock()

    def archive(self) -> None:
        """Somente após confirmação e início; não toca dados de INIT/PLAN/BUILD."""
        self.pause_clock()
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        archive = self.target('historico/' + suffix)
        if self.directory.exists():
            # Recusa links em toda a árvore antes de mover qualquer resultado.
            for parent, dirs, names in os.walk(self.directory, followlinks=False):
                for name in dirs + names:
                    if (Path(parent) / name).is_symlink():
                        raise ReverseError('Histórico contém links. Reexecução interrompida sem limpar resultados.')
            archive.mkdir(parents=True, mode=0o700)
            for path in list(self.directory.iterdir()):
                if path.name not in {'historico', 'prompts'}:
                    path.rename(archive / path.name)
        self.state = None

    def verify_artifacts(self) -> None:
        if not self.state:
            return
        for record in self.state['units'].values():
            try:
                content = self.target(record['artifact']).read_bytes()
            except OSError:
                raise ReverseError('Resultado intermediário ausente; não é seguro declarar cobertura. Resultados preservados.') from None
            if digest(content) != record['sha256']:
                raise ReverseError('Resultado intermediário foi alterado. Corrija ou confirme Refazer; não será sobrescrito silenciosamente.')
