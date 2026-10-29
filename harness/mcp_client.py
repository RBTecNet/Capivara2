"""Transporte MCP oficial e adaptador do contrato documental do doc-center."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .domain import MCPContent, MCPContext, MCPDocument, MCPProject
from .mcp_registry import MCPError, MCPInstance, resolve_api_key


def connection_error(error: Exception) -> str:
    if isinstance(error, MCPError):
        return str(error)
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            if isinstance(child, Exception):
                message = connection_error(child)
                if message != "Não foi possível acessar o MCP. Confira o endereço, a chave API e a conexão.":
                    return message
        return "Não foi possível acessar o MCP. Confira o endereço, a chave API e a conexão."
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return f"Falha de autenticação ou permissão (HTTP {status}). Confira o endereço e a chave API."
        return f"O MCP respondeu HTTP {status}. Confira o endereço completo e a disponibilidade do servidor."
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "O MCP excedeu o tempo de resposta. Confira o endereço e tente novamente."
    return "Não foi possível acessar o MCP. Confira o endereço, a chave API e a conexão."


class DocCenterSource:
    """Interpreta URIs do contrato, sem conhecer endpoints, tenants ou chaves."""

    def __init__(self, session: ClientSession, capabilities: types.ServerCapabilities) -> None:
        self.session = session
        self.capabilities = capabilities

    async def index(self) -> tuple[tuple[MCPProject, ...], list[types.Resource], list[types.Prompt]]:
        if self.capabilities.resources is None:
            raise MCPError("Este MCP não oferece os recursos necessários para carregar projetos do doc-center.")
        resources = []
        cursor = None
        seen = set()
        while True:
            page = await self.session.list_resources(cursor=cursor)
            resources.extend(page.resources)
            cursor = page.nextCursor
            if not cursor:
                break
            if cursor in seen:
                raise MCPError("O MCP repetiu uma página de recursos. Tente novamente.")
            seen.add(cursor)
        prompts = []
        if self.capabilities.prompts is not None:
            cursor = None
            seen = set()
            while True:
                page = await self.session.list_prompts(cursor=cursor)
                prompts.extend(page.prompts)
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen:
                    raise MCPError("O MCP repetiu uma página de prompts. Tente novamente.")
                seen.add(cursor)
        projects = {}
        for resource in resources:
            uri = urlsplit(str(resource.uri))
            if uri.scheme != "capivara" or not uri.netloc or uri.netloc == "geral":
                continue
            projects.setdefault(uri.netloc, uri.netloc)
            if uri.path == "/pedido":
                projects[uri.netloc] = resource.name.removeprefix("pedido — ").removeprefix("pedido - ")
        for prompt in prompts:
            if prompt.name and "/" not in prompt.name and prompt.name != "geral":
                title = prompt.title or prompt.name
                projects[prompt.name] = title.removeprefix("pedido — ").removeprefix("pedido - ")
        ordered = sorted((MCPProject(slug, name) for slug, name in projects.items()), key=lambda p: (p.name.casefold(), p.slug))
        return tuple(ordered), resources, prompts

    async def load(self, instance: MCPInstance, project: MCPProject) -> MCPContext:
        projects, resources, prompts = await self.index()
        available = next((p for p in projects if p.slug == project.slug), None)
        if available is None:
            raise MCPError("O projeto não está mais disponível para esta chave API. Atualize a listagem.")
        prompt_resources = []
        document_resources = []
        seen = set()
        for resource in resources:
            uri = urlsplit(str(resource.uri))
            if uri.scheme != "capivara" or uri.netloc != project.slug or str(resource.uri) in seen:
                continue
            seen.add(str(resource.uri))
            parts = uri.path.strip("/").split("/")
            if parts == ["pedido"]:
                prompt_resources.append(resource)
            elif len(parts) >= 3 and parts[0] == "docs":
                document_resources.append((resource, parts[1], len(parts) > 3))

        async def read(resource: types.Resource) -> tuple[MCPContent, ...]:
            result = await self.session.read_resource(resource.uri)
            contents = []
            for content in result.contents:
                if isinstance(content, types.TextResourceContents):
                    contents.append(MCPContent(str(content.uri), content.mimeType or resource.mimeType or "text/plain", text=content.text))
                elif isinstance(content, types.BlobResourceContents):
                    try:
                        base64.b64decode(content.blob, validate=True)
                    except ValueError:
                        raise MCPError("O MCP devolveu um arquivo binário inválido. Tente carregar o projeto novamente.") from None
                    contents.append(MCPContent(str(content.uri), content.mimeType or resource.mimeType or "application/octet-stream", blob=content.blob))
            if not contents:
                raise MCPError("Um recurso do projeto não pôde ser carregado por completo. Tente novamente.")
            return tuple(contents)

        prompt_parts = []
        for resource in prompt_resources:
            prompt_parts.extend(content.text for content in await read(resource) if content.text is not None)
        if not prompt_parts and any(p.name == project.slug for p in prompts):
            result = await self.session.get_prompt(project.slug)
            prompt_parts.extend(message.content.text for message in result.messages
                                if isinstance(message.content, types.TextContent))
        prompt = "\n".join(prompt_parts)
        if not prompt.strip():
            raise MCPError("Este projeto não tem um prompt preenchido. Cadastre o pedido no doc-center ou escolha outro projeto.")
        # Lê o texto completo de cada URI; não usa o resumo truncado de obter_contexto.
        semaphore = asyncio.Semaphore(6)

        async def document(resource: types.Resource, kind: str, attachment: bool) -> MCPDocument:
            async with semaphore:
                return MCPDocument(str(resource.uri), resource.name, kind, attachment, await read(resource))

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(document(*entry)) for entry in document_resources]
        documents = [task.result() for task in tasks]
        return MCPContext(instance.id, instance.name, instance.url, available, prompt, tuple(documents))


class MCPService:
    def __init__(self, *, timeout: float = 30) -> None:
        self.timeout = timeout

    @asynccontextmanager
    async def source(self, instance: MCPInstance):
        token = resolve_api_key(instance)
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                             timeout=httpx.Timeout(self.timeout), follow_redirects=False) as http:
                    async with streamable_http_client(instance.url, http_client=http) as (reader, writer, _):
                        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=self.timeout),
                                                 client_info=types.Implementation(name="capivara2", version="0.1.0")) as session:
                            info = await session.initialize()
                            yield DocCenterSource(session, info.capabilities)
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(connection_error(exc)) from None

    async def projects(self, instance: MCPInstance) -> tuple[MCPProject, ...]:
        async with self.source(instance) as source:
            projects, _, _ = await source.index()
            return projects

    async def validate(self, instance: MCPInstance) -> None:
        await self.projects(instance)

    async def load_project(self, instance: MCPInstance, project: MCPProject) -> MCPContext:
        async with self.source(instance) as source:
            return await source.load(instance, project)
