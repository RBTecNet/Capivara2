"""Área de origem do pedido do INIT; não inicia módulos ou modelos."""

import asyncio
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, Input, Label, Select, Static, TextArea

from capivara2ptn.dialogs import McpArgumentsDialog, McpServerDialog, PromptFileDialog
from capivara2ptn.mcp import McpClient, McpError, McpItem
from capivara2ptn.mcp_config import ConfigError, McpServer, load_servers, remove_server


class PromptSource(Vertical):
    def __init__(self, working_directory: Path) -> None:
        super().__init__(id="prompt-source-area")
        self.working_directory = working_directory
        self.servers: list[McpServer] = []
        self.items: list[McpItem] = []
        self.mcp_text = ""
        self._server_id: str | None = None
        self._client: McpClient | None = None
        self._revision = 0

    def compose(self) -> ComposeResult:
        yield Label("Origem do prompt", classes="field-label")
        yield Select(
            [("Manual", "manual"), ("Arquivo", "file"), ("Servidor MCP", "mcp")],
            value="manual", allow_blank=False, id="prompt-source",
        )
        with ContentSwitcher(initial="manual-source", id="prompt-content"):
            with Vertical(id="manual-source"):
                yield TextArea(placeholder="Digite ou cole o pedido do INIT…", id="manual-prompt", soft_wrap=True, tab_behavior="focus")
            with Vertical(id="file-source"):
                with Horizontal(id="prompt-row"):
                    yield Input(placeholder="Caminho relativo ao diretório de trabalho", id="prompt-path")
                    yield Button("Arquivo…", id="choose-prompt")
            with Vertical(id="mcp-source"):
                yield Select([], prompt="Selecione um servidor", id="mcp-server")
                with Horizontal(id="mcp-actions"):
                    yield Button("Adicionar", id="add-mcp")
                    yield Button("Remover", id="remove-mcp", disabled=True)
                    yield Button("Consultar", id="query-mcp", variant="primary", disabled=True)
                yield Label("Projeto", classes="field-label")
                yield Select([], prompt="Consulte um servidor", id="mcp-item", disabled=True)
                yield Static("Selecione ou cadastre um servidor MCP.", id="mcp-status", classes="muted", markup=False)
                yield TextArea(read_only=True, show_cursor=False, id="mcp-preview", tab_behavior="focus")

    def on_mount(self) -> None:
        self.query_one("#prompt-path").tooltip = "Caminhos relativos usam o diretório de onde a CAPIVARA2PTN foi iniciada."
        self.reload_servers()

    def status(self, message: str, error: bool = False) -> None:
        widget = self.query_one("#mcp-status", Static)
        widget.update(message)
        widget.set_class(error, "error")

    def reload_servers(self, selected: str | None = None) -> None:
        try:
            self.servers = load_servers()
        except ConfigError as error:
            self.status(str(error), error=True)
            return
        selector = self.query_one("#mcp-server", Select)
        value = selected or self._server_id
        with self.prevent(Select.Changed):
            selector.set_options([(server.name, server.id) for server in self.servers])
            if any(server.id == value for server in self.servers):
                selector.value = value
        if selector.value != self._server_id:
            self.select_server(selector.value)

    @on(Select.Changed, "#prompt-source")
    def source_changed(self, event: Select.Changed) -> None:
        names = {"manual": "manual-source", "file": "file-source", "mcp": "mcp-source"}
        self.query_one(ContentSwitcher).current = names[str(event.value)]

    @on(Button.Pressed, "#choose-prompt")
    def choose_file(self) -> None:
        self.app.push_screen(PromptFileDialog(self.working_directory), self.file_selected)

    def file_selected(self, path: Path | None) -> None:
        if path is not None:
            self.query_one("#prompt-path", Input).value = str(path)

    @property
    def file_path(self) -> Path | None:
        value = self.query_one("#prompt-path", Input).value.strip()
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.working_directory / path

    @on(Button.Pressed, "#add-mcp")
    def add_mcp(self) -> None:
        self.app.push_screen(McpServerDialog(), self.server_added)

    def server_added(self, identifier: str | None) -> None:
        if identifier is not None:
            self.reload_servers(identifier)
            self.status("Servidor cadastrado. Selecione Consultar para listar projetos.")

    @on(Button.Pressed, "#remove-mcp")
    def remove_mcp(self) -> None:
        if self._server_id is None:
            return
        try:
            remove_server(self._server_id)
        except ConfigError as error:
            self.status(str(error), error=True)
            return
        self.select_server(Select.BLANK)
        self.reload_servers()
        self.status("Servidor removido do cadastro global.")

    @on(Select.Changed, "#mcp-server")
    def server_changed(self, event: Select.Changed) -> None:
        self.select_server(event.value)

    def select_server(self, value: object) -> None:
        self._revision += 1
        self.workers.cancel_group(self, "mcp")
        if self._client is not None:
            self._client.cancel()
        self._client = None
        self._server_id = value if isinstance(value, str) else None
        self.clear_catalog()
        selected = any(server.id == self._server_id for server in self.servers)
        self.query_one("#remove-mcp", Button).disabled = not selected
        self.query_one("#query-mcp", Button).disabled = not selected
        self.status("Selecione Consultar para listar projetos." if selected else "Selecione ou cadastre um servidor MCP.")

    def clear_catalog(self) -> None:
        self.items = []
        self.mcp_text = ""
        self.query_one("#mcp-preview", TextArea).load_text("")
        selector = self.query_one("#mcp-item", Select)
        selector.expanded = False
        selector.disabled = True
        with self.prevent(Select.Changed):
            selector.set_options([])
        selector.prompt = "Consulte um servidor"

    @on(Button.Pressed, "#query-mcp")
    def query_mcp(self) -> None:
        server = next((server for server in self.servers if server.id == self._server_id), None)
        if server is None:
            return
        self._revision += 1
        if self._client is not None:
            self._client.cancel()
        self._client = None
        self.clear_catalog()
        self.status("Consultando projetos…")
        self.query_catalog(server, self._revision)

    @work(group="mcp", exclusive=True, exit_on_error=False)
    async def query_catalog(self, server: McpServer, revision: int) -> None:
        client = McpClient(server)
        self._client = client
        try:
            items = await asyncio.to_thread(client.catalog)
        except asyncio.CancelledError:
            client.cancel()
            raise
        except McpError as error:
            if revision == self._revision:
                self.status(str(error), error=True)
            return
        if revision != self._revision:
            client.cancel()
            return
        self._client = client
        self.items = items
        selector = self.query_one("#mcp-item", Select)
        with self.prevent(Select.Changed):
            selector.set_options([(item.title, str(index)) for index, item in enumerate(items)])
        selector.disabled = not items
        selector.prompt = "Selecione um projeto" if items else "Nenhum projeto disponível"
        self.status(f"{len(items)} projeto(s) disponível(is)." if items else "O servidor não disponibilizou projetos.")

    @on(Select.Changed, "#mcp-item")
    def item_changed(self, event: Select.Changed) -> None:
        self._revision += 1
        self.workers.cancel_group(self, "mcp")
        if self._client is not None:
            self._client.cancel()
        self.mcp_text = ""
        self.query_one("#mcp-preview", TextArea).load_text("")
        if not isinstance(event.value, str) or self._client is None:
            return
        item = self.items[int(event.value)]
        revision = self._revision
        if item.arguments:
            self.app.push_screen(
                McpArgumentsDialog(item.arguments),
                lambda arguments: self.arguments_ready(item, revision, arguments),
            )
        else:
            self.arguments_ready(item, revision, {})

    def arguments_ready(self, item: McpItem, revision: int, arguments: dict[str, str] | None) -> None:
        if revision != self._revision or self._client is None:
            return
        if arguments is None:
            with self.prevent(Select.Changed):
                self.query_one("#mcp-item", Select).value = Select.BLANK
            self.status("Consulta ao prompt cancelada.")
            return
        server = next((server for server in self.servers if server.id == self._server_id), None)
        if server is None:
            return
        # Uma consulta nova possui sua própria sessão. Cancelar uma leitura
        # anterior não invalida a próxima nem mistura respostas entre itens.
        client = McpClient(server)
        self._client = client
        self.status("Lendo o pedido selecionado…")
        self.read_item(client, item, revision, arguments)

    @work(group="mcp", exclusive=True, exit_on_error=False)
    async def read_item(self, client: McpClient, item: McpItem, revision: int, arguments: dict[str, str]) -> None:
        def read_request() -> str:
            client.initialize()
            return client.read(item, arguments)

        try:
            text = await asyncio.to_thread(read_request)
        except asyncio.CancelledError:
            client.cancel()
            raise
        except McpError as error:
            if revision == self._revision:
                self.status(str(error), error=True)
            return
        if revision == self._revision:
            self.mcp_text = text
            self.query_one("#mcp-preview", TextArea).load_text(text)
            self.status("Pedido carregado para leitura. Nenhum módulo foi executado.")

    def on_unmount(self) -> None:
        if self._client is not None:
            self._client.cancel()
