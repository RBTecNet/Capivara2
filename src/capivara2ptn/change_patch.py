"""Aplica somente edições aprovadas, com âncoras exatas e recuperação por arquivo."""

import json
import difflib
import os
import re
import stat
import tempfile
import time
from pathlib import Path

from capivara2ptn.change_source import ChangeError, ChangeConflict, MASK, app_path, fingerprint, read_source, sha, text_file


def atomic_app(root, relative, raw, mode, expected):
    path = app_path(root, relative, writable=True)
    if fingerprint(root, relative) != expected:
        raise ChangeConflict([relative])
    if raw is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.capivara-change-', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        app_path(root, relative, writable=True)
        if fingerprint(root, relative) != expected:
            raise ChangeConflict([relative])
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def private_bytes(store, name, raw):
    path = store.path(name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def capture_before(store, task):
    before = task.setdefault('before', {})
    copies = task.setdefault('before_copies', {})
    baseline = task.setdefault('baseline_id', str(time.time_ns()))
    for path in task['files']:
        current = fingerprint(store.root, path)
        before.setdefault(path, current)
        if path in copies:
            continue
        if current != before[path]:
            raise ChangeConflict([path])
        if current is None:
            copies[path] = None
            continue
        name = f'tasks/{task["id"]}/baselines/{baseline}/antes-{sha(path)}.bin'
        raw = read_source(store.root, path)
        if fingerprint(store.root, path) != current:
            raise ChangeConflict([path])
        if store.path(name).exists():
            if sha(store.path(name).read_bytes()) != current.split(':', 1)[1]:
                raise ChangeError('Cópia anterior da task foi alterada.')
        else:
            private_bytes(store, name, raw)
        copies[path] = name
    store.save()


def task_diff(store, task, secrets, offset=0):
    if type(offset) is not int or offset < 0:
        raise ChangeError('Posição de diff inválida.')
    parts = []
    for path, copy in task.get('before_copies', {}).items():
        if copy:
            raw = store.path(copy).read_bytes()
            if sha(raw) != task['before'][path].split(':', 1)[1]:
                raise ChangeError('Cópia anterior da task foi alterada.')
            try:old = raw.decode('utf-8')
            except UnicodeError:old = raw.decode('latin-1')
        else:
            old = ''
        new = text_file(store.root, path)[0] if fingerprint(store.root, path) is not None else ''
        parts.extend(difflib.unified_diff(secrets.source(old, path).splitlines(True), secrets.source(new, path).splitlines(True),
                                        fromfile='antes/' + path, tofile='depois/' + path))
    complete = ''.join(parts)
    return {'text': complete[offset:offset + 14000], 'offset': offset, 'total_chars': len(complete),
            'next_offset': offset + 14000 if offset + 14000 < len(complete) else None}


def stage_patch(store, task, value, secrets):
    if store.state.get('pending_apply'):
        raise ChangeError('Recupere a aplicação anterior antes de propor novas edições.')
    operations = value.get('changes')
    if not isinstance(operations, list):
        raise ChangeError('O escritor deve declarar uma lista changes.')
    paths = [item.get('path') for item in operations if isinstance(item, dict)]
    if len(paths) != len(operations) or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ChangeError('Operações de arquivo inválidas ou duplicadas.')
    outside = sorted(set(paths) - set(task['files']))
    if outside:
        return {'scope_expansion': {'reason': 'O escritor propôs arquivos fora da task aprovada.', 'files': outside}}
    store.check_approval()
    store.check_sources()
    prepared = []
    for operation in operations:
        path = operation['path']
        target = app_path(store.root, path, writable=True)
        before = fingerprint(store.root, path)
        action = operation.get('action')
        raw_before = read_source(store.root, path) if before else None
        mode = stat.S_IMODE(target.stat().st_mode) if before else 0o644
        if action == 'create':
            if before is not None:
                raise ChangeConflict([path])
            content = operation.get('content')
            mode = 0o755 if operation.get('executable') is True else 0o644
            if not isinstance(content, str):
                raise ChangeError('Arquivo novo sem conteúdo textual: ' + path)
            if store.state['plan']['forbid_tests'] and (re.search(r'(^|/)(tests?|__tests__)/|(?:\.test\.|_test\.|test_)', path, re.I)):
                raise ChangeError('A opção de execução proíbe criar novos testes automatizados.')
            raw_after = content.encode('utf-8')
        elif action in {'edit', 'delete'}:
            if before is None or operation.get('base_fingerprint') != before:
                raise ChangeError('Fingerprint da versão editada não corresponde ao arquivo atual: ' + path)
            if action == 'delete':
                if store.state['plan']['forbid_tests'] and re.search(r'(^|/)(tests?|__tests__)/|(?:\.test\.|_test\.|test_)', path, re.I):
                    raise ChangeError('Não remova testes existentes.')
                raw_after = None
            else:
                content, encoding = text_file(store.root, path)
                edits = operation.get('edits')
                if not isinstance(edits, list) or not edits:
                    raise ChangeError('Uma edição precisa declarar âncoras old/new.')
                crlf = '\r\n' in content and '\n' not in content.replace('\r\n', '')
                for edit in edits:
                    if not isinstance(edit, dict) or not isinstance(edit.get('old'), str) or not isinstance(edit.get('new'), str):
                        raise ChangeError('Edição textual inválida.')
                    old, new = edit['old'], edit['new']
                    if crlf:
                        old = old.replace('\r\n', '\n').replace('\n', '\r\n')
                        new = new.replace('\r\n', '\n').replace('\n', '\r\n')
                    if MASK in old or MASK in new or content.count(old) != 1:
                        raise ChangeError('Âncora não é única, contém segredo oculto ou não corresponde ao código: ' + path)
                    content = content.replace(old, new, 1)
                try:
                    raw_after = content.encode(encoding)
                except UnicodeError:
                    raise ChangeError('A edição não preserva a codificação original: ' + path) from None
        else:
            raise ChangeError('Ação de arquivo inválida; use create, edit ou delete.')
        if raw_after is not None:
            proposed = raw_after.decode('utf-8', errors='replace')
            if MASK in proposed or any(secret in proposed for secret in secrets.values):
                # Valores já existentes podem permanecer, mas não podem ser introduzidos/reescritos pelo modelo.
                before_text = (raw_before or b'').decode('utf-8', errors='replace')
                if MASK in proposed or any(proposed.count(s) > before_text.count(s) for s in secrets.values):
                    raise ChangeError('A alteração contém segredo ou marcador de sanitização: ' + path)
        prepared.append((path, before, raw_before, raw_after, mode))
    transaction = f'transacoes/{time.time_ns()}'
    records = []
    for index, (path, before, raw_before, raw_after, mode) in enumerate(prepared):
        old_name, new_name = f'{transaction}/{index}.antes.bin', f'{transaction}/{index}.depois.bin'
        if raw_before is not None:
            private_bytes(store, old_name, raw_before)
        if raw_after is not None:
            private_bytes(store, new_name, raw_after)
        records.append({'path': path, 'before': before, 'after': f'{mode:o}:' + sha(raw_after) if raw_after is not None else None,
                        'before_copy': old_name if raw_before is not None else None,
                        'after_copy': new_name if raw_after is not None else None, 'mode': mode})
    journal = {'task': task['id'], 'files': records, 'status': 'pending'}
    store.write_json(transaction + '/registro.json', journal)
    store.state['pending_apply'] = transaction + '/registro.json'
    store.save()
    recover_patch(store)
    return {'applied': [record['path'] for record in records]}


def recover_patch(store):
    name = store.state.get('pending_apply')
    if not name:
        return
    store.check_approval()
    journal = store.read_json(name)
    if not journal or not isinstance(journal.get('files'), list):
        raise ChangeError('Registro de aplicação inválido; arquivos preservados.')
    task = next((t for t in store.state['tasks'] if t['id'] == journal.get('task')), None)
    if not task:
        raise ChangeError('Task do registro de aplicação não pertence ao plano.')
    for record in journal['files']:
        path = record['path']
        if path not in task['files']:
            raise ChangeError('A transação excede o escopo aprovado.')
        actual = fingerprint(store.root, path)
        if actual not in {record['before'], record['after']}:
            raise ChangeConflict([path])
        if record['after_copy']:
            raw = store.path(record['after_copy']).read_bytes()
            if f"{record['mode']:o}:" + sha(raw) != record['after']:
                raise ChangeError('Cópia de aplicação alterada; retomada bloqueada.')
    for record in journal['files']:
        path = record['path']
        if fingerprint(store.root, path) != record['after']:
            raw = store.path(record['after_copy']).read_bytes() if record['after_copy'] else None
            atomic_app(store.root, path, raw, record['mode'], record['before'])
        store.state['expected'][path] = record['after']
        if path in store.state['read_files']:
            store.state['read_files'][path] = record['after']
    task.update(writer='done', operation='g1', status='running')
    store.state.pop('pending_apply')
    store.save()
    journal['status'] = 'applied'
    store.write_json(name, journal)
