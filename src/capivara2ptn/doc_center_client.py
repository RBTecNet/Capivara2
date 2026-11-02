"""Operações Doc-Center comprovadas, sobre o transporte MCP já existente."""

import re
from urllib.parse import urlsplit

from capivara2ptn.mcp import McpClient, McpError

KINDS = {'skill', 'hook', 'memoria', 'prompt', 'documentacao'}


def project_slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', value) or value == 'geral':
        raise McpError('Informe o slug de um projeto Doc-Center, diferente da área geral.')
    return value


def scoped_uri(uri, project):
    try:
        parsed = urlsplit(uri)
    except (ValueError, TypeError):
        return False
    if parsed.scheme != 'capivara' or parsed.netloc != project or parsed.query or parsed.fragment:
        return False
    parts = parsed.path.split('/')
    if parts == ['', 'pedido']:
        return True
    return (len(parts) >= 4 and parts[:2] == ['', 'docs'] and parts[2] in KINDS
            and bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', parts[3]))
            and all(part not in {'', '.', '..'} for part in parts[4:]))


class DocCenterClient(McpClient):
    """Não implementa HTTP, sessão, autenticação ou paginação novamente."""

    def discover(self):
        self.initialize()
        self.resources = self._list('resources/list', 'resources')
        self.tools = {item['name']: item for item in self._list('tools/list', 'tools') if isinstance(item.get('name'), str)}
        projects = set()
        for item in self.resources:
            uri = item.get('uri')
            if not isinstance(uri, str):
                continue
            try:
                project = project_slug(urlsplit(uri).netloc)
            except (McpError, ValueError):
                continue
            if scoped_uri(uri, project):
                projects.add(project)
        return sorted(projects)

    def read_uri(self, uri, project):
        if not scoped_uri(uri, project):
            raise McpError('Leitura recusada: URI fora do projeto Doc-Center selecionado.')
        result = self._call('resources/read', {'uri': uri})
        contents = result.get('contents')
        if not isinstance(contents, list) or not contents or any(not isinstance(item, dict) or item.get('uri') != uri for item in contents):
            raise McpError('A resposta MCP não corresponde ao recurso solicitado.')
        if any('blob' in item for item in contents):
            raise McpError('Recursos binários do Doc-Center não são importados como instruções.')
        if any(not isinstance(item.get('text'), str) for item in contents):
            raise McpError('O recurso Doc-Center não contém texto válido.')
        return '\n\n'.join(item['text'] for item in contents)

    def project_resources(self, project):
        project_slug(project)
        return [item for item in self.resources if isinstance(item.get('uri'), str) and scoped_uri(item['uri'], project)]

    def publish(self, operation, arguments):
        if operation not in {'registrar_levantamento', 'registrar_memoria'}:
            raise McpError('Operação de publicação não autorizada pela integração.')
        schema = getattr(self, 'tools', {}).get(operation, {}).get('inputSchema', {})
        required = {'projeto', 'conteudo'} | ({'natureza', 'titulo'} if operation == 'registrar_memoria' else set())
        if not required <= set(schema.get('properties', {})):
            raise McpError(f'O servidor/token não oferece a publicação {operation}. Confira a permissão workflow no cadastro existente.')
        project_slug(arguments.get('projeto'))
        if any(not isinstance(value, str) for value in arguments.values()):
            raise McpError('O contrato Doc-Center exige argumentos textuais.')
        if operation == 'registrar_memoria' and arguments.get('natureza') not in {'decisao', 'convencao', 'armadilha'}:
            raise McpError('A integração não substitui a memória de estado do projeto.')
        result = self._call('tools/call', {'name': operation, 'arguments': arguments})
        if result.get('isError'):
            # O texto remoto pode ecoar credenciais; não é mensagem de erro confiável.
            raise McpError(f'Doc-Center recusou {operation}. Confira projeto e permissão workflow do token.')
        return self._text(result.get('content', []))
