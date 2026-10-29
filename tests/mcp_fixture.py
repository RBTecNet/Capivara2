"""Servidor HTTP de teste com o contrato MCP atual do doc-center."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time


class MCPFixture:
    def __init__(self):
        self.calls = []
        self.revoked = set()
        self.error_uri = None
        self.delay = 0
        self.sse = False
        self.prompt_only = False
        self.empty = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def reply(self, status, data=None):
                body = json.dumps(data, ensure_ascii=False).encode() if data is not None else b""
                content_type = "application/json"
                if fixture.sse and status == 200:
                    body = b"event: message\ndata: " + body + b"\n\n"
                    content_type = "text/event-stream"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                self.reply(405)

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                fixture.calls.append((token, request))
                if token not in ("tenant-a", "tenant-b") or token in fixture.revoked:
                    self.reply(401, {"erro": "sem acesso"})
                    return
                if fixture.delay:
                    time.sleep(fixture.delay)
                if "id" not in request:
                    self.reply(202)
                    return
                method = request["method"]
                params = request.get("params", {})
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"resources": {}, "prompts": {}},
                              "serverInfo": {"name": "doc-center", "version": "0.1.0"}}
                elif method == "resources/list":
                    resources = fixture.resources(token)
                    offset = int(params.get("cursor", "0"))
                    result = {"resources": resources[offset:offset + 3]}
                    if offset + 3 < len(resources):
                        result["nextCursor"] = str(offset + 3)
                elif method == "prompts/list":
                    result = {"prompts": [] if fixture.empty else [
                        {"name": "shared", "title": f"pedido — Projeto {token}", "description": "Pedido atual"},
                        {"name": "geral/outro", "description": "Não pertence ao projeto"}]}
                elif method == "prompts/get":
                    result = {"messages": [{"role": "user", "content": {"type": "text", "text": f"Prompt {token}\nSegunda linha"}}]}
                elif method == "resources/read":
                    uri = params["uri"]
                    if uri == fixture.error_uri:
                        self.reply(200, {"jsonrpc": "2.0", "id": request["id"],
                                         "error": {"code": -32602, "message": "Não foi possível ler o recurso"}})
                        return
                    if uri.endswith("imagem.png"):
                        result = {"contents": [{"uri": uri, "mimeType": "image/png", "blob": base64.b64encode(b"binary-reference").decode()}]}
                    else:
                        text = fixture.text(uri, token)
                        result = {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
                else:
                    self.reply(200, {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Método desconhecido"}})
                    return
                self.reply(200, {"jsonrpc": "2.0", "id": request["id"], "result": result})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def resources(self, token):
        if self.empty:
            return []
        entries = [
            ("capivara://shared/pedido", f"pedido — Projeto {token}"),
            ("capivara://shared/docs/skill/frontend", "Frontend"),
            ("capivara://shared/docs/skill/frontend/references/guia.md", "Guia"),
            ("capivara://shared/docs/skill/frontend/imagem.png", "Imagem"),
            ("capivara://shared/docs/hook/check", "Hook"),
            ("capivara://shared/docs/memoria/decisao", "Decisão"),
            ("capivara://geral/docs/skill/nao-selecionada", "Não selecionada"),
            ("capivara://shared/historico", "Histórico"),
        ]
        if self.prompt_only:
            entries = [entry for entry in entries if not entry[0].endswith("/pedido")]
        if token == "tenant-a":
            entries.append(("capivara://outro/pedido", "pedido — Outro projeto"))
        return [{"uri": uri, "name": name, "mimeType": "text/markdown"} for uri, name in entries]

    def text(self, uri, token):
        if uri.endswith("/pedido"):
            return f"Prompt {token}\nSegunda linha"
        if uri.endswith("/frontend"):
            return f"# Skill {token}\n" + "x" * 25_000
        return f"Conteúdo {token}: {uri}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
