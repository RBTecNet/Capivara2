"""Seleções e outbox opcionais, separados dos estados dos módulos funcionais."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from capivara2ptn.change_source import sha
from capivara2ptn.change_state import file_lock
from capivara2ptn.doc_center_client import project_slug
from capivara2ptn.mcp import McpError
from capivara2ptn.mcp_config import load_servers
from capivara2ptn.workspace import ensure_harness

PREFIX = '<!-- CAPIVARA2PTN-SNAPSHOT '
SEPARATOR = '\n<!-- CAPIVARA2PTN-CONTENT -->\n'


def snapshot(text):
    if not text.startswith(PREFIX):return None
    head, _, rest = text.partition('\n')
    if not head.endswith(' -->') or SEPARATOR not in '\n' + rest:return None
    try:metadata = json.loads(head[len(PREFIX):-4])
    except ValueError:return None
    body = ('\n' + rest).split(SEPARATOR, 1)[1].strip()
    if not isinstance(metadata, dict) or metadata.get('artifact_sha256') != sha(body):return None
    return metadata, body


def equivalent(remote, job):
    if remote.strip() == job['content'].strip():return True
    left, right = snapshot(remote), snapshot(job['content'])
    return bool(left and right and left[1] == right[1] and all(left[0].get(k) == right[0].get(k)
                for k in ('publisher', 'module', 'artifact', 'artifact_sha256')))


class DocCenterStore:
    def __init__(self, root):
        self.root = root.resolve()
        self.base = self.root / '.harness/doc-center'

    def path(self, relative):
        path = self.base / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise McpError('Caminho interno Doc-Center inválido.')
        for part in (path, *path.parents):
            if part == self.root.parent:
                break
            if part.is_symlink():
                raise McpError('A integração Doc-Center não pode usar arquivos de controle que sejam links.')
        return path

    def prepare(self):
        ensure_harness(self.root)
        self.path('').mkdir(mode=0o700, parents=True, exist_ok=True)

    def read(self, name, default=None):
        path = self.path(name)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default
        except (ValueError, OSError, UnicodeError):
            raise McpError('Registro local Doc-Center inválido; artefatos dos módulos permanecem preservados.') from None
        if not isinstance(value, dict):
            raise McpError('Formato do registro local Doc-Center inválido.')
        return value

    def write(self, name, value):
        self.prepare()
        path = self.path(name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.doc-center-', dir=path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(name))
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def settings(self):
        value = self.read('selecao.json', {'version': 1, 'reverse': None, 'change': None})
        if value.get('version') != 1 or not set(value) <= {'version', 'publisher', 'reverse', 'change'}:
            raise McpError('Versão da configuração Doc-Center não reconhecida.')
        return value

    def selection(self, module):
        value = self.settings().get(module)
        if value is not None and (not isinstance(value, dict) or set(value) != {'server_id', 'endpoint_sha256', 'project'} or not isinstance(value.get('server_id'), str)
                or not isinstance(value.get('endpoint_sha256'), str)
                or (value.get('project') is not None and not isinstance(value['project'], str))):
            raise McpError('Seleção Doc-Center inválida.')
        return value

    def select(self, module, server=None, project=None):
        if module not in {'reverse', 'change'}:
            raise McpError('Módulo sem integração Doc-Center.')
        if project is not None:project_slug(project)
        if server is None and not self.path('selecao.json').exists():
            return
        self.prepare()
        with file_lock(self.path('selecao.lock')):
            value = self.settings()
            value[module] = {'server_id': server.id, 'endpoint_sha256': sha(server.url), 'project': project} if server else None
            value.setdefault('publisher', uuid4().hex)
            self.write('selecao.json', value)

    def target(self, module):
        value = self.selection(module)
        if value and value.get('project'):project_slug(value['project'])
        return dict(value) if value and value.get('project') else None

    @staticmethod
    def server(target):
        server = next((server for server in load_servers() if server.id == target['server_id']), None)
        if server is None:
            raise McpError('O servidor Doc-Center selecionado não está mais no cadastro MCP.')
        if sha(server.url) != target['endpoint_sha256']:
            raise McpError('O endpoint cadastrado mudou. Selecione novamente o servidor e o projeto; pendências não foram redirecionadas.')
        return server

    def queue(self, module, target, artifacts):
        if not target or self.target(module) != target:
            return []
        self.prepare()
        identifiers = []
        with file_lock(self.path('fila.lock')):
            publisher = self.settings().get('publisher')
            if not publisher:
                raise McpError('Identificador local da publicação ausente; selecione o projeto novamente.')
            for artifact in artifacts:
                content = artifact['content'].strip()
                if not content:continue
                checksum = sha(content)
                publication = sha(publisher + '\0' + module + '\0' + artifact['key'] + '\0' + checksum)
                identifier = sha(json.dumps(target, sort_keys=True) + publication)
                name = 'publicacoes/' + identifier + '.json'
                identifiers.append(identifier)
                if self.path(name).exists():
                    continue
                label = artifact['label']
                title = f"{module.upper()} {label[:16]} {publication[:32]}"
                metadata = {'publisher': publisher, 'module': module, 'artifact': artifact['key'],
                            'artifact_sha256': checksum, 'observed_at': artifact.get('observed_at', datetime.now(timezone.utc).isoformat())}
                content = PREFIX + json.dumps(metadata, ensure_ascii=False) + ' -->\n\n'
                content += '> Snapshot histórico da execução local. Confirme o código e as decisões atuais antes de reutilizar.\n'
                content += '> Origem temporal: ' + metadata['observed_at'] + '\n' + SEPARATOR + artifact['content'].strip()
                self.write(name, {'version': 1, 'id': identifier, 'module': module, 'target': target,
                                 'key': artifact['key'], 'kind': artifact['kind'], 'nature': artifact.get('nature', 'convencao'),
                                 'title': title, 'content': content, 'content_sha256': sha(content),
                                 'status': 'pending', 'attempts': 0, 'error': '', 'confirmation': None})
        return identifiers

    def jobs(self, module, target):
        if not target:
            return []
        directory = self.path('publicacoes')
        if not directory.exists():
            return []
        result = []
        for path in sorted(directory.glob('*.json')):
            value = self.read('publicacoes/' + path.name)
            if value.get('module') == module and value.get('target') == target:
                if value.get('id') != path.stem or value.get('content_sha256') != sha(value.get('content', '')):
                    raise McpError('Uma publicação pendente foi alterada. Artefatos locais preservados.')
                result.append(value)
        return result

    def counts(self, module):
        target = self.target(module)
        jobs = self.jobs(module, target)
        return sum(job['status'] != 'confirmed' for job in jobs), sum(job['status'] == 'confirmed' for job in jobs)
