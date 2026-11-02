"""Inspeção localizada e caminhos de escrita restritos do CHANGE."""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from capivara2ptn.build_g1 import inventory
from capivara2ptn.reverse_source import read_source, secret_file
from capivara2ptn.workspace import ensure_project_destination


class ChangeError(Exception):
    pass


class ChangeConflict(ChangeError):
    def __init__(self, paths):
        self.paths = sorted(set(paths))
        super().__init__('Arquivos mudaram desde o checkpoint: ' + ', '.join(self.paths))


IGNORED = {'.harness', '.git', '.hg', '.svn', 'node_modules', 'vendor', '.venv', 'venv',
           '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.cache', '.next', 'dist', 'build', 'target'}
BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.deb',
          '.exe', '.dll', '.so', '.pyc', '.woff', '.woff2', '.ttf', '.mp4', '.mp3', '.db', '.sqlite', '.sqlite3'}
SECRET_NAME = r'[\w.-]*(?:secret|password|passwd|senha|segredo|token|api[_-]?key|chave[_-]?(?:api|privada)|private[_-]?key|credential|credencial|authorization|cookie|dsn|connection[_-]?string)[\w.-]*|APP_KEY|DATABASE_URL|DB_PASSWORD'
MASK = '[CAPIVARA_SEGREDO_OCULTO]'


def sha(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode('utf-8')).hexdigest()


def validate_project(root):
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ChangeError('O diretório da aplicação deve existir.')
    ensure_project_destination(root)
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / 'src/capivara2ptn/app.py').is_file() and root.is_relative_to(checkout):
        raise ChangeError('Escolha a aplicação fora do código-fonte da CAPIVARA2PTN.')
    return root


def app_path(root, relative, *, writable=False):
    if not isinstance(relative, str) or not relative or any(ord(c) < 32 or ord(c) == 127 for c in relative) or '\\' in relative:
        raise ChangeError('Caminho de arquivo inválido.')
    path = Path(relative)
    if path.is_absolute() or path.as_posix() != relative or relative.startswith('~') or any(p in {'.', '..', *IGNORED} for p in relative.split('/')):
        raise ChangeError('Caminho fora da área autorizável da aplicação: ' + relative)
    target = root / path
    for part in (target, *target.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ChangeError('Links não podem ser lidos ou alterados pelo CHANGE: ' + relative)
    if not target.resolve().is_relative_to(root):
        raise ChangeError('Arquivo fora do projeto: ' + relative)
    if writable and (secret_file(path) or path.suffix.lower() in BINARY):
        raise ChangeError('Arquivo sensível ou binário não pode ser alterado por esta execução: ' + relative)
    return target


def fingerprint(root, relative):
    path = app_path(root, relative)
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ChangeError('Esperado arquivo regular: ' + relative)
    return f'{stat.S_IMODE(info.st_mode):o}:' + sha(read_source(root, relative))


def text_file(root, relative):
    path = app_path(root, relative)
    if secret_file(path) or path.suffix.lower() in BINARY:
        raise ChangeError('Arquivo sensível ou binário omitido: ' + relative)
    raw = read_source(root, relative)
    if b'\0' in raw:
        raise ChangeError('Arquivo binário omitido: ' + relative)
    try:
        text, encoding = raw.decode('utf-8'), 'utf-8'
    except UnicodeError:
        text, encoding = raw.decode('latin-1'), 'latin-1'
    return text, encoding


class Secrets:
    def __init__(self, root):
        self.values = set()
        for path in root.glob('.env*'):
            if path.is_file() and not path.is_symlink():
                try:
                    raw = read_source(root, path.name).decode('utf-8', errors='replace')
                    for key, value in re.findall(r'^\s*(?:export\s+)?([\w.-]+)\s*=\s*(.+?)\s*$', raw, re.M):
                        value = value.strip().strip('\"\'')
                        if len(value) >= 4 and re.search(SECRET_NAME, key, re.I):
                            self.values.add(value)
                except OSError:
                    pass

    def safe(self, text):
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, MASK)
        text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'(?i)((?:[\"\']?(?:' + SECRET_NAME + r')[\"\']?)\s*(?:=>|:|=)\s*)([\"\'])([^\n]*?)\2',
                      lambda m: m[1] + m[2] + MASK + m[2], text)
        text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----',
                      lambda m: MASK + '\n' * m[0].count('\n'), text)
        text = re.sub(r'(?i)(Bearer\s+|(?:sk-|ghp_|github_pat_|AKIA|xox[baprs]-))[\w./+\-=]+', MASK, text)
        text = re.sub(r'(?i)([\w+]+://)[^\s/\"\']+:[^\s/@\"\']+@', r'\1' + MASK + '@', text)
        return text

    def protect(self, value):
        if isinstance(value, str):
            return self.safe(value)
        if isinstance(value, list):
            return [self.protect(v) for v in value]
        if isinstance(value, dict):
            return {k: self.protect(v) for k, v in value.items()}
        return value

    def prose(self, text):
        text = self.safe(text)
        return re.sub(r'(?im)^([ \t]*(?:' + SECRET_NAME + r')[ \t]*[:=][ \t]*)([^\s\"\'][^\n]*)',
                      lambda m: m[1] + json.dumps(MASK), text)

    def source(self, text, path):
        return self.prose(text) if Path(path).suffix.lower() in {'.yaml', '.yml', '.ini', '.conf', '.toml', '.json'} else self.safe(text)


def catalog(root):
    result = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if name in IGNORED or path.is_symlink() or not path.is_file() or secret_file(path) or path.suffix.lower() in BINARY:
                continue
            result.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size})
    return result


def read_chunk(root, path, start, secrets, budget=12000):
    if type(start) is not int or start < 1:
        raise ChangeError('Linha inicial inválida.')
    before = fingerprint(root, path)
    text, encoding = text_file(root, path)
    if fingerprint(root, path) != before:
        raise ChangeConflict([path])
    lines = secrets.source(text, path).splitlines() or ['']
    if start > len(lines):
        raise ChangeError(f'Linha {start} inexistente em {path}.')
    rows, size, end = [], 0, start
    for number, line in enumerate(lines[start - 1:], start):
        if len(line) > budget:
            raise ChangeError(f'Linha extensa em {path}:{number}; solicite uma busca localizada ou documente a limitação.')
        row = f'{number}: {line}'
        if rows and size + len(row) + 1 > budget:
            break
        rows.append(row)
        size += len(row) + 1
        end = number
    return {'path': path, 'start': start, 'end': end, 'total_lines': len(lines), 'text': '\n'.join(rows),
            'fingerprint': before, 'encoding': encoding, 'newline': 'crlf' if '\r\n' in text and '\n' not in text.replace('\r\n', '') else 'lf'}


def search(root, entries, query, secrets, offset=0, pattern='*'):
    if not isinstance(query, str) or not query.strip() or type(offset) is not int or offset < 0:
        raise ChangeError('Busca inválida.')
    hits, inspected, total = [], {}, 0
    for entry in entries:
        path = entry['path']
        if not fnmatch.fnmatchcase(path, pattern):
            continue
        try:
            text, encoding = text_file(root, path)
        except (ChangeError, OSError):
            continue
        for number, line in enumerate(secrets.source(text, path).splitlines(), 1):
            if query.casefold() in line.casefold():
                if offset <= total < offset + 30:
                    column = max(0, line.casefold().find(query.casefold()) - 160)
                    hits.append({'path': path, 'line': number, 'column': column, 'text': line[column:column + 800]})
                    if path not in inspected:
                        current = fingerprint(root, path)
                        if current is None or current.split(':', 1)[1] != sha(text.encode(encoding)):
                            raise ChangeConflict([path])
                        inspected[path] = current
                total += 1
    return {'query': query, 'hits': hits, 'fingerprints': inspected, 'total': total,
            'next_offset': offset + 30 if offset + 30 < total else None}, inspected


def project_snapshot(root):
    return inventory(root)


def git_context(root):
    executable = shutil.which('git')
    if not executable:
        return {'available': False, 'recovery': 'Sem ponto de restauração completo garantido; cópias dos arquivos alterados serão preservadas.'}
    def git(*args):
        return subprocess.run([executable, '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    try:
        head = git('rev-parse', '--verify', 'HEAD')
        status = git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.', ':(exclude).harness')
        if head.returncode or status.returncode:
            raise OSError
        return {'available': True, 'head': head.stdout.decode().strip(),
                'status': status.stdout.decode('utf-8', errors='replace').split('\0'),
                'recovery': 'HEAD e alterações pendentes registrados. Não há commit, limpeza ou rollback automático; arquivos tocados terão cópias privadas.'}
    except (OSError, subprocess.TimeoutExpired):
        return {'available': False, 'recovery': 'Git sem referência de recuperação válida; não há restauração completa garantida.'}


def available_tools():
    return {name: shutil.which(name) for name in ('python', 'python3', 'node', 'npm', 'pnpm', 'yarn', 'tsc', 'go', 'cargo',
            'dotnet', 'javac', 'mvn', 'gradle', 'php', 'composer', 'ruby', 'bundle', 'make', 'cmake', 'pytest', 'bwrap')}
