"""Leitura determinística do legado. Nunca executa ou escreve na origem."""

import hashlib
import asyncio
import json
import os
import re
import stat
import tomllib
from pathlib import Path

from capivara2ptn.workspace import ensure_project_destination


class ReverseError(Exception):
    pass


EXCLUDED = {
    '.git': 'metadados de versionamento', '.harness': 'artefatos do harness',
    'node_modules': 'dependências instaladas', 'vendor': 'dependências instaladas',
    '.venv': 'ambiente instalado', 'venv': 'ambiente instalado', '__pycache__': 'cache',
    '.pytest_cache': 'cache', '.mypy_cache': 'cache', '.next': 'compilados',
    'dist': 'compilados', 'build': 'compilados', 'target': 'compilados',
    '.cache': 'cache', '.idea': 'configuração local do editor',
}
LANGUAGES = {
    '.py': 'Python', '.php': 'PHP', '.js': 'JavaScript', '.jsx': 'JavaScript',
    '.ts': 'TypeScript', '.tsx': 'TypeScript', '.java': 'Java', '.kt': 'Kotlin',
    '.cs': 'C#', '.go': 'Go', '.rs': 'Rust', '.rb': 'Ruby', '.swift': 'Swift',
    '.c': 'C', '.h': 'C/C++', '.cpp': 'C++', '.sql': 'SQL', '.sh': 'Shell',
    '.vue': 'Vue', '.svelte': 'Svelte', '.html': 'HTML', '.css': 'CSS',
    '.scss': 'SCSS', '.dart': 'Dart', '.ex': 'Elixir', '.erl': 'Erlang',
    '.pl': 'Perl', '.lua': 'Lua', '.r': 'R', '.pas': 'Pascal', '.dfm': 'Delphi',
    '.vb': 'Visual Basic', '.cob': 'COBOL', '.prg': 'xBase',
}
MANIFESTS = {'package.json', 'composer.json', 'pyproject.toml', 'requirements.txt',
             'go.mod', 'Cargo.toml', 'Gemfile', 'pom.xml', 'build.gradle',
             'Dockerfile', 'Makefile', 'Procfile', 'setup.py', 'setup.cfg'}
CONFIG_SUFFIXES = {'.json', '.toml', '.yaml', '.yml', '.ini', '.conf', '.config', '.xml'}
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip',
                   '.gz', '.deb', '.exe', '.dll', '.so', '.pyc', '.woff', '.woff2',
                   '.ttf', '.mp4', '.mp3', '.sqlite', '.sqlite3', '.db', '.class', '.jar'}
SECRET_KEY = r'(?:[\w.-]*(?:secret|password|passwd|token|api[_-]?key|private[_-]?key|credential|authorization|cookie|dsn)[\w.-]*|DB_[A-Z_]+|DATABASE_URL|APP_KEY|ACCESS_KEY)'


def redact(text: str) -> str:
    """Remoção conservadora de valores, incluindo configs e URLs autenticadas."""
    text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----',
                  lambda match: '[CHAVE OCULTA]' + '\n' * match[0].count('\n'), text)
    text = re.sub(r'(?im)([\"\'\s$]*' + SECRET_KEY + r'[\"\'\s]*\s*[:=]\s*)[^\n,;}]+', r'\1"[OCULTO]"', text)
    text = re.sub(r'(?i)(https?://|[\w+]+://)[^\s/\"\']+:[^\s/@\"\']+@', r'\1[OCULTO]@', text)
    text = re.sub(r'(?i)(Bearer\s+|(?:sk-|ghp_|github_pat_|AKIA|xox[baprs]-))[\w./+\-=]+', '[OCULTO]', text)
    text = re.sub(r'(?i)([?&](?:key|token|secret|password|api_key)=)[^&\s\"\']+', r'\1[OCULTO]', text)
    text = re.sub(r'(?i)(--(?:password|token|secret|api-key)(?:=|\s+))[^\s]+', r'\1[OCULTO]', text)
    # Construtores e arrays comuns de configuração: getenv/default, define, PDO.
    text = re.sub(r'(?i)((?:define|env|getenv)\(\s*[\"\']' + SECRET_KEY + r'[\"\']\s*,\s*)[^\n)]+', r'\1"[OCULTO]"', text)
    return text


def digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_roots(destination: Path, source: Path) -> tuple[Path, Path]:
    try:
        dest, legacy = destination.expanduser().resolve(strict=True), source.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ReverseError('Os diretórios de destino e legado devem existir e ser acessíveis.') from None
    if not dest.is_dir() or not legacy.is_dir():
        raise ReverseError('Selecione diretórios existentes.')
    if dest.is_relative_to(legacy) or legacy.is_relative_to(dest):
        raise ReverseError('Destino e legado devem ser diferentes e não podem conter um ao outro (inclusive por links).')
    ensure_project_destination(dest)
    ensure_project_destination(legacy)
    # Protege também o checkout quando executado sem instalação editável.
    package = Path(__file__).resolve().parent
    checkout = package.parent.parent
    if (checkout / 'pyproject.toml').is_file() and (checkout / 'src/capivara2ptn').is_dir():
        if dest.is_relative_to(checkout) or legacy.is_relative_to(checkout) or checkout.is_relative_to(legacy):
            raise ReverseError('REVERSE não pode utilizar o código-fonte do próprio harness.')
    return dest, legacy


def read_source(root: Path, relative: str) -> bytes:
    """openat com NOFOLLOW em cada componente; recusa links e arquivos especiais."""
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in ('..', '.') for p in path.parts):
        raise ReverseError('Caminho relativo inválido no inventário.')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ReverseError('A origem deixou de ser um arquivo regular.')
            return stream.read()
    finally:
        os.close(descriptor)


def secret_file(path: Path) -> bool:
    return (path.name == '.env' or path.name.startswith('.env.') or path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}
            or path.name.lower() in {'id_rsa', 'id_ed25519', 'credentials', '.npmrc', '.pypirc', 'auth.json'})


def technical_inventory(path: Path, content: str) -> dict:
    """Indícios determinísticos; não executa manifestos nem presume comportamento."""
    result = {'dependencies': [], 'frameworks': [], 'startup': [], 'routes': [],
              'data_definitions': [], 'modules': [], 'routine_candidates': [], 'limitations': []}
    def dependency(name, version, scope):
        result['dependencies'].append({'name': str(name), 'version': redact(str(version)), 'scope': scope})
    try:
        if path.name in {'package.json', 'composer.json'}:
            manifest = json.loads(content)
            if not isinstance(manifest, dict):
                raise ValueError
            for scope in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies', 'require', 'require-dev'):
                for name, version in manifest.get(scope, {}).items():
                    dependency(name, version, scope)
            for name, command in manifest.get('scripts', {}).items():
                result['startup'].append({'name': name, 'declaration': redact(str(command))})
            for name in ('main', 'module', 'bin', 'autoload', 'exports'):
                if name in manifest:
                    result['modules'].append({'declaration': redact(f'{name}: {manifest[name]}')})
        elif path.name in {'pyproject.toml', 'Cargo.toml'}:
            manifest = tomllib.loads(content)
            project = manifest.get('project', {})
            for declaration in project.get('dependencies', []):
                dependency(declaration, '', 'project.dependencies')
            for scope in ('dependencies', 'dev-dependencies', 'build-dependencies'):
                for name, version in manifest.get(scope, {}).items():
                    dependency(name, version, scope)
            for name, command in project.get('scripts', {}).items():
                result['startup'].append({'name': name, 'declaration': redact(str(command))})
        elif path.name.startswith('requirements') and path.suffix == '.txt':
            for declaration in content.splitlines():
                if declaration.strip() and not declaration.lstrip().startswith(('#', '-')):
                    dependency(declaration.strip(), '', 'requirements')
        elif path.name == 'go.mod':
            for name, version in re.findall(r'(?m)^\s*(?:require\s+)?([\w./-]+)\s+(v[\w.+-]+)', content):
                dependency(name, version, 'require')
        elif path.name == 'Gemfile':
            for name, version in re.findall(r'''gem\s+['"]([^'"]+)['"](?:,\s*['"]([^'"]+)['"])?''', content):
                dependency(name, version, 'gem')
        elif path.name.endswith('.csproj'):
            for name, version in re.findall(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', content):
                dependency(name, version, 'PackageReference')
    except (ValueError, TypeError, AttributeError):
        result['limitations'].append('Manifesto não pôde ser interpretado estruturalmente; requer investigação textual.')
    frameworks = {'laravel': 'Laravel', 'symfony': 'Symfony', 'django': 'Django', 'flask': 'Flask',
                  'fastapi': 'FastAPI', 'react': 'React', 'next': 'Next.js', 'vue': 'Vue',
                  'svelte': 'Svelte', 'angular': 'Angular', 'express': 'Express', 'rails': 'Rails',
                  'spring': 'Spring', 'aspnet': 'ASP.NET'}
    for dep in result['dependencies']:
        for key, label in frameworks.items():
            if key in dep['name'].casefold():
                result['frameworks'].append(label)
    result['frameworks'] = sorted(set(result['frameworks']))
    for number, line in enumerate(content.splitlines(), 1):
        def reference():
            return {'line': number, 'declaration': redact(line.strip())[:500]}
        if re.search(r'Route::(?:get|post|put|delete|patch|resource)|(?:@\w+\.|\bapp\.|\brouter\.)(?:route|get|post|put|patch|delete)\(', line):
            result['routes'].append(reference())
        if re.search(r'(?i)CREATE\s+TABLE|Schema::create|^\s*(?:model|entity)\s+\w+|class\s+\w+.*\b(?:Model|Entity)\b', line):
            result['data_definitions'].append(reference())
        if re.search(r'^\s*(?:namespace|package|module|import|from)\s+', line):
            result['modules'].append(reference())
        routine = re.search(r'^\s*(?:(?:public|private|protected|static|async|export|pub)\s+)*(?:def|function|func|fn)\s+(\w+)', line)
        if routine:
            result['routine_candidates'].append({**reference(), 'symbol': routine[1]})
        if re.search(r'__name__\s*==|\b(?:public static void main|func main|fn main)\b', line):
            result['startup'].append(reference())
    return result


def inventory_entries(root: Path):
    def visit(directory: Path):
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            raise ReverseError(f'Inventário incompleto: sem acesso a {directory.relative_to(root)}.') from None
        for path in children:
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            item = {'path': relative, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'mode': stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                item.update(kind='link', classification='excluded', reason='link não seguido por segurança')
            elif stat.S_ISDIR(info.st_mode):
                item.update(kind='directory', classification='directory')
                if path.name in EXCLUDED:
                    item.update(classification='excluded', reason=EXCLUDED[path.name], subtree=True)
                else:
                    yield item
                    yield from visit(path)
                    continue
            elif stat.S_ISREG(info.st_mode):
                item.update(kind='file', language=LANGUAGES.get(path.suffix.lower(), 'Não identificada'))
                raw = read_source(root, relative)
                item['sha256'] = digest(raw)
                if secret_file(path):
                    item.update(classification='excluded', reason='configuração sensível; valores não lidos pelo modelo')
                    if path.name.startswith('.env'):
                        item['config_keys'] = sorted(set(re.findall(r'^\s*([A-Za-z_][\w]*)\s*=', raw.decode('utf-8', errors='replace'), re.M)))
                elif path.suffix.lower() in BINARY_SUFFIXES or b'\0' in raw[:8192]:
                    item.update(classification='excluded', reason='recurso binário/compilado; comportamento não inspecionável como texto')
                else:
                    try:
                        content = raw.decode('utf-8-sig')
                        item['encoding'] = 'utf-8-sig'
                    except UnicodeError:
                        content = raw.decode('latin-1')
                        item['encoding'] = 'latin-1'
                    item.update(classification='relevant', lines=len(content.splitlines()), signals=[])
                    if path.name in MANIFESTS or path.name.endswith(('.csproj', '.lock')):
                        item['signals'].append('dependências/inicialização')
                    if path.name.startswith('.') or path.suffix.lower() in CONFIG_SUFFIXES:
                        item['signals'].append('configuração')
                    for pattern, label in ((r'(?i)route|endpoint|controller', 'rotas/interfaces'),
                                           (r'(?i)migrat|schema|model|entity', 'dados/modelos/migrações'),
                                           (r'(?i)main\.|index\.|manage\.|artisan|__main__', 'entrada/inicialização')):
                        if re.search(pattern, relative):
                            item['signals'].append(label)
                    # Indícios técnicos rastreáveis, nunca requisitos confirmados.
                    item['hints'] = [redact(line.strip())[:300] for line in content.splitlines()
                                     if re.search(r'(?i)^\s*(?:import |from |use |require\(|.*Route::|.*@.*route|CREATE TABLE|.*app\.(?:get|post)\()', line)][:20]
                    item['technical'] = technical_inventory(path, content)
            else:
                item.update(kind='special', classification='excluded', reason='arquivo especial não lido')
            yield item
    yield from visit(root)


def finish_inventory(entries: list[dict]) -> dict:
    return {'version': 1, 'entries': entries, 'sha256': digest(entries),
            'frameworks': sorted({name for e in entries for name in e.get('technical', {}).get('frameworks', [])}),
            'languages': sorted({e['language'] for e in entries if e.get('classification') == 'relevant'})}


def inventory(root: Path) -> dict:
    return finish_inventory(list(inventory_entries(root)))


async def inventory_async(root: Path) -> dict:
    entries = []
    for item in inventory_entries(root):
        entries.append(item)
        await asyncio.sleep(0)
    return finish_inventory(entries)


def known_secrets(root: Path, inv: dict) -> list[str]:
    values = set()
    public_database_keys = {'DB_CONNECTION', 'DB_HOST', 'DB_PORT', 'DB_DATABASE'}
    for entry in inv['entries']:
        if entry['kind'] == 'file' and secret_file(Path(entry['path'])):
            raw = read_source(root, entry['path']).decode('utf-8', errors='replace')
            for line in raw.splitlines():
                match = re.match(r'\s*(?:export\s+)?([\w.-]+)\s*=\s*(.+?)\s*$', line)
                if (match and match[1].upper() not in public_database_keys
                        and re.fullmatch(SECRET_KEY + r'|[\w.-]*(?:senha|segredo|credencial|username|connection[_-]?string)[\w.-]*|PASS|PWD', match[1], re.I)):
                    value = match[2].strip().strip('\"\'')
                    if len(value) >= 4 and not value.startswith('#'):
                        values.add(value)
    return sorted(values, key=len, reverse=True)


def relevant(inv: dict) -> list[dict]:
    return [entry for entry in inv['entries'] if entry['classification'] == 'relevant']


def source_chunks(root: Path, entry: dict, budget: int = 14000) -> list[dict]:
    raw = read_source(root, entry['path'])
    if digest(raw) != entry['sha256']:
        raise ReverseError(f"O arquivo {entry['path']} mudou durante a execução. Retome para atualizar o inventário.")
    text = redact(raw.decode(entry['encoding']))
    # Linhas muito longas são divididas sem descartar conteúdo.
    chunks, lines, size, start = [], [], 0, 1
    for number, line in enumerate(text.splitlines() or [''], 1):
        for offset in range(0, max(1, len(line)), budget):
            part = line[offset:offset + budget]
            if lines and size + len(part) + 16 > budget:
                chunks.append({'start': start, 'end': number - 1 if offset == 0 else number, 'text': '\n'.join(lines)})
                lines, size, start = [], 0, number
            lines.append(f'{number}: {part}')
            size += len(part) + 16
    if lines:
        chunks.append({'start': start, 'end': max(start, len(text.splitlines())), 'text': '\n'.join(lines)})
    return chunks
