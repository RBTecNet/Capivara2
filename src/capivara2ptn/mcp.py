"""Subconjunto MCP de leitura: HTTP/HTTPS, sessão, recursos e prompts."""

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from capivara2ptn.mcp_config import McpServer

PROTOCOL_VERSION = "2025-06-18"
MAX_RESPONSE = 8 * 1024 * 1024
TIMEOUT = 20


class McpError(Exception):
    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class McpItem:
    kind: str
    name: str
    title: str
    uri: str | None = None
    arguments: tuple[dict, ...] = field(default=(), repr=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class McpClient:
    def __init__(self, server: McpServer) -> None:
        self._endpoint = server
        self._opener = build_opener(_NoRedirect())
        self._session = None
        self._version = None
        self._sequence = 0
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def _check(self, deadline: float) -> None:
        if self._cancelled.is_set():
            raise McpError("Consulta MCP cancelada.")
        if time.monotonic() >= deadline:
            raise McpError("O servidor MCP não concluiu a resposta dentro do prazo.")

    def _envelope(self, response, identifier: int, deadline: float) -> dict:
        if "text/event-stream" not in response.headers.get("Content-Type", ""):
            raw = bytearray()
            while True:
                self._check(deadline)
                chunk = response.read1(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise McpError("A resposta MCP excedeu o limite de leitura.")
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and envelope.get("jsonrpc") == "2.0" and envelope.get("id") == identifier:
                return envelope
        else:
            total = 0
            data = []
            while True:
                self._check(deadline)
                raw = response.readline(MAX_RESPONSE + 1)
                total += len(raw)
                if total > MAX_RESPONSE:
                    raise McpError("A resposta MCP excedeu o limite de leitura.")
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                if not line or not raw:
                    if data:
                        try:
                            envelope = json.loads("\n".join(data))
                        except ValueError:
                            envelope = None
                        data = []
                        if isinstance(envelope, dict) and envelope.get("jsonrpc") == "2.0" and envelope.get("id") == identifier:
                            return envelope
                    if not raw:
                        break
        raise McpError("O servidor MCP retornou uma resposta inválida ou incompleta.")

    def _call(self, method: str, params: dict | None = None, notification: bool = False) -> dict:
        deadline = time.monotonic() + TIMEOUT
        self._check(deadline)
        self._sequence += 1
        identifier = self._sequence
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            payload["id"] = identifier
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._endpoint.token:
            headers["Authorization"] = f"Bearer {self._endpoint.token}"
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self._version:
            headers["MCP-Protocol-Version"] = self._version
        request = Request(self._endpoint.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=TIMEOUT) as response:
                self._session = response.headers.get("Mcp-Session-Id") or self._session
                if notification:
                    return {}
                envelope = self._envelope(response, identifier, deadline)
            if "error" in envelope:
                error = envelope["error"]
                code = error.get("code", 0) if isinstance(error, dict) else 0
                # O corpo remoto pode ecoar Authorization. Nunca o apresentamos.
                raise McpError("O servidor MCP recusou a operação solicitada.", code)
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise McpError("O servidor MCP retornou um resultado inválido.")
            return result
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 401:
                raise McpError("MCP 401: token ausente, inválido ou expirado.", status) from None
            if status == 403:
                raise McpError("MCP 403: confira as permissões do token e a configuração do endpoint.", status) from None
            if 300 <= status < 400:
                raise McpError("O endpoint MCP redirecionou a conexão. Cadastre a URL final; credenciais não foram reenviadas.", status) from None
            raise McpError(f"O servidor MCP respondeu HTTP {status}.", status) from None
        except (TimeoutError, socket.timeout):
            raise McpError("O servidor MCP não respondeu dentro do prazo de 20 segundos.") from None
        except (OSError, URLError, ValueError, TypeError):
            raise McpError("Não foi possível consultar o MCP. Verifique o endpoint, a rede e o certificado HTTPS.") from None

    def initialize(self) -> None:
        result = self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "capivara2ptn", "version": "0.1.0"},
        })
        version = result.get("protocolVersion", PROTOCOL_VERSION)
        if not isinstance(version, str) or version not in ("2025-03-26", "2025-06-18", "2025-11-25"):
            raise McpError("A versão MCP negociada não é compatível com este cliente.")
        self._version = version
        self._call("notifications/initialized", notification=True)

    def _list(self, method: str, key: str) -> list[dict]:
        items = []
        cursor = None
        seen = set()
        while True:
            try:
                result = self._call(method, {"cursor": cursor} if cursor is not None else {})
            except McpError as error:
                if error.code == -32601:
                    return []
                raise
            page = result.get(key, [])
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise McpError("O catálogo retornado pelo MCP é inválido.")
            items.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return items
            if not isinstance(cursor, str) or cursor in seen:
                raise McpError("A paginação do catálogo MCP é inválida.")
            seen.add(cursor)

    def catalog(self) -> list[McpItem]:
        self.initialize()
        resources = self._list("resources/list", "resources")
        projects = {}
        for resource in resources:
            uri = resource.get("uri")
            if not isinstance(uri, str):
                continue
            try:
                parsed = urlsplit(uri)
            except ValueError:
                raise McpError("Um recurso MCP retornou um URI inválido.") from None
            if parsed.scheme != "capivara" or not parsed.netloc or parsed.netloc == "geral":
                continue
            projects.setdefault(parsed.netloc, None)
            if parsed.path == "/pedido":
                projects[parsed.netloc] = uri
        # O usuário escolhe somente o projeto. O pedido/prompt correspondente
        # é resolvido por read(), sem entradas ou prefixos adicionais na lista.
        return [McpItem("project", slug, slug, uri) for slug, uri in sorted(projects.items())]

    @staticmethod
    def _text(content: object) -> str:
        if not isinstance(content, list):
            content = [content]
        return "\n\n".join(item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()).strip()

    def read(self, item: McpItem, arguments: dict[str, str] | None = None) -> str:
        text = ""
        if item.kind == "project" and item.uri:
            text = self._text(self._call("resources/read", {"uri": item.uri}).get("contents", []))
        if not text:
            result = self._call("prompts/get", {"name": item.name, "arguments": arguments or {}})
            messages = result.get("messages", [])
            if not isinstance(messages, list):
                raise McpError("O prompt MCP retornou mensagens inválidas.")
            text = "\n\n".join(self._text(message.get("content")) for message in messages if isinstance(message, dict)).strip()
        if not text:
            raise McpError("O projeto ou prompt selecionado não contém um pedido textual.")
        return text
