"""Campos opcionais Doc-Center somente nos controles REVERSE e CHANGE."""

import asyncio
import json
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from capivara2ptn.doc_center import session_for
from capivara2ptn.doc_center_client import DocCenterClient, project_slug
from capivara2ptn.doc_center_store import DocCenterStore
from capivara2ptn.mcp_config import load_servers

NONE = object()
MANUAL = object()


class DocProjectDialog(ModalScreen[str | None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Cancelar')]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Projeto Doc-Center pelo slug', classes='dialog-title')
            yield Static('Projetos vazios podem não aparecer no catálogo MCP. O slug será validado por leitura; nenhum projeto será criado.', classes='question')
            yield Input(placeholder='slug-do-projeto', id='doc-project-slug')
            yield Static('', id='doc-project-error', classes='error', markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar', id='doc-project-cancel')
                yield Button('Validar projeto', id='doc-project-confirm', variant='primary')

    @on(Button.Pressed, '#doc-project-confirm')
    @on(Input.Submitted, '#doc-project-slug')
    def confirm(self):
        try:value = project_slug(self.query_one(Input).value.strip())
        except Exception as error:
            self.query_one('#doc-project-error', Static).update(str(error));return
        self.dismiss(value)

    @on(Button.Pressed, '#doc-project-cancel')
    def cancel(self):self.dismiss(None)


class DocCenterFields(Vertical):
    DEFAULT_CSS = '''
    DocCenterFields { height: auto; margin-top: 1; }
    DocCenterFields .dc-project-box { height: auto; }
    DocCenterFields .dc-status { height: auto; max-height: 5; margin-top: 1; }
    DocCenterFields .dc-actions { height: 3; }
    DocCenterFields .dc-actions Button { width: 1fr; min-width: 10; margin: 0; }
    '''

    def __init__(self, module):
        super().__init__(id=module + '-doc-center')
        self.module = module
        self.store = None
        self.servers = []
        self.projects = []
        self.server = None
        self.server_value = NONE
        self.revision = 0
        self.client = None
        self.ready = False

    def compose(self) -> ComposeResult:
        yield Static('Servidor Doc-Center', classes='field-label')
        yield Select([('Nenhum', NONE)], value=NONE, allow_blank=False, classes='dc-server', id=self.module + '-dc-server')
        with Vertical(classes='dc-project-box'):
            yield Static('Projeto Doc-Center', classes='field-label')
            yield Select([('Informar slug do projeto…', MANUAL)], prompt='Selecione ou consulte projetos', classes='dc-project', id=self.module + '-dc-project')
        with Horizontal(classes='dc-actions'):
            yield Button('Atualizar', classes='dc-query')
            yield Button('Sincronizar', classes='dc-sync', disabled=True)
        yield Static('Somente artefatos locais.', classes='dc-status', markup=False)

    def on_mount(self):
        self.store = DocCenterStore(self.app.working_directory)
        self.reload()
        self.ready = True

    def status(self, message, error=False):
        if not self.query('.dc-status'):return
        widget = self.query_one('.dc-status', Static)
        widget.update(message)
        widget.set_class(error, 'error')

    def reload(self):
        try:
            selected = self.store.selection(self.module)
        except Exception as error:
            self.status(str(error), True);selected = None
        try:self.servers = load_servers()
        except Exception:
            self.servers = []
            if selected:self.status('Não foi possível ler o cadastro MCP privado existente.', True)
        options = [('Nenhum', NONE), *[(server.name, server.id) for server in self.servers]]
        self.server = next((server for server in self.servers if selected and server.id == selected['server_id']), None)
        if selected and not self.server:options.append(('Servidor selecionado não está no cadastro', selected['server_id']))
        with self.prevent(Select.Changed):
            selector = self.query_one('.dc-server', Select)
            selector.set_options(options)
            selector.value = selected['server_id'] if selected else NONE
            self.projects = [selected['project']] if selected and selected.get('project') else []
            project = self.query_one('.dc-project', Select)
            project.set_options([(value, value) for value in self.projects] + [('Informar slug do projeto…', MANUAL)])
            project.value = selected['project'] if selected and selected.get('project') else Select.BLANK
        self.layout_selection(bool(selected))
        self.server_value = selected['server_id'] if selected else NONE
        self.refresh_status()

    def layout_selection(self, enabled):
        self.query_one('.dc-project-box').display = enabled
        self.query_one('.dc-query', Button).label = 'Consultar' if enabled else 'Atualizar'
        self.query_one('.dc-sync', Button).disabled = not bool(enabled and self.server and self.store.target(self.module))

    def refresh_status(self):
        try:
            target = self.store.target(self.module)
            selection = self.store.selection(self.module)
            if selection is None:
                self.status('Somente artefatos locais.');return
            if target is None:
                self.status('Selecione o projeto para habilitar consulta e publicação.');return
            pending, confirmed = self.store.counts(self.module)
            previous = self.store.read('sincronizacao-' + self.module + '.json', {})
            error = previous.get('error', '') if previous.get('target') == target and pending else ''
            self.status(f'Sincronização: {pending} pendência(s), {confirmed} confirmação(ões).' + (' ' + error if error else ''), bool(pending))
        except Exception as error:self.status(str(error), True)

    def cancel_network(self):
        self.revision += 1
        if self.client:self.client.cancel()
        self.workers.cancel_group(self, 'doc-center')

    @on(Select.Changed, '.dc-server')
    def server_changed(self, event):
        if not self.ready:return
        event.stop()
        if event.value != self.query_one('.dc-server', Select).value or event.value == self.server_value:return
        self.cancel_network()
        self.server = next((server for server in self.servers if server.id == event.value), None)
        try:
            self.store.select(self.module, self.server)
            self.server_value = event.value
            self.projects = []
            with self.prevent(Select.Changed):
                project = self.query_one('.dc-project', Select)
                project.set_options([('Informar slug do projeto…', MANUAL)])
                project.value = Select.BLANK
            self.layout_selection(self.server is not None)
            self.refresh_status()
        except Exception as error:self.status(str(error), True)

    @on(Select.Changed, '.dc-project')
    def project_changed(self, event):
        if not self.ready:return
        event.stop()
        if event.value != self.query_one('.dc-project', Select).value:return
        if not self.server:return
        self.cancel_network()
        try:
            if event.value is MANUAL:
                self.store.select(self.module, self.server, None)
                self.layout_selection(True)
                self.app.push_screen(DocProjectDialog(), lambda value: self.validate_manual(value) if value else self.refresh_status())
            elif isinstance(event.value, str):
                self.store.select(self.module, self.server, project_slug(event.value))
                self.layout_selection(True)
                self.refresh_status()
            else:
                self.store.select(self.module, self.server, None)
                self.layout_selection(True)
                self.refresh_status()
        except Exception as error:self.status(str(error), True)

    @on(Button.Pressed, '.dc-query')
    def query_projects(self):
        if not self.server:
            self.reload()  # só cadastro local; Nenhum nunca faz comunicação MCP
            return
        self.cancel_network()
        self.fetch_projects(self.server, self.revision)

    @work(group='doc-center', exclusive=True, exit_on_error=False)
    async def fetch_projects(self, server, revision):
        self.status('Consultando projetos disponíveis no MCP…')
        client = DocCenterClient(server)
        self.client = client
        try:
            projects = await asyncio.to_thread(client.discover)
            if revision != self.revision:return
            selected = self.store.selection(self.module)
            saved = selected.get('project') if selected else None
            self.projects = sorted(set(projects) | ({saved} if saved else set()))
            with self.prevent(Select.Changed):
                selector = self.query_one('.dc-project', Select)
                selector.set_options([(p, p) for p in self.projects] + [('Informar slug do projeto…', MANUAL)])
                selector.value = saved if saved else Select.BLANK
            self.status(f'{len(projects)} projeto(s) anunciado(s). Projetos vazios podem ser validados pelo slug.')
        except asyncio.CancelledError:
            client.cancel();raise
        except Exception as error:
            if revision == self.revision:self.status(str(error), True)

    @work(group='doc-center', exclusive=True, exit_on_error=False)
    async def validate_manual(self, value):
        if not value or not self.server:return
        server, revision = self.server, self.revision
        client = DocCenterClient(server)
        self.client = client
        self.status('Validando projeto existente, sem criar ou alterar o pedido…')
        try:
            await asyncio.to_thread(client.discover)
            await asyncio.to_thread(client.read_uri, f'capivara://{value}/pedido', value)
            if revision != self.revision:return
            self.projects = sorted(set(self.projects) | {value})
            with self.prevent(Select.Changed):
                selector = self.query_one('.dc-project', Select)
                selector.set_options([(p, p) for p in self.projects] + [('Informar slug do projeto…', MANUAL)])
                selector.value = value
            self.store.select(self.module, server, value)
            self.layout_selection(True)
            self.refresh_status()
        except asyncio.CancelledError:
            client.cancel();raise
        except Exception as error:
            if revision == self.revision:self.status(str(error), True)

    def legacy_source(self):
        if self.module != 'reverse':return None
        try:
            from capivara2ptn.doc_center_artifacts import local_text
            state = json.loads(local_text(self.app.working_directory, self.app.working_directory / '.harness/reverse/estado.json'))
            return Path(state['source'])
        except Exception:return None

    async def prepare(self, query='', source=None):
        try:
            session = session_for(self.app.working_directory, self.module, self.status, source, query)
            if session is not None:await session.packet({'request': query})
            return session
        except asyncio.CancelledError:raise
        except Exception as error:
            self.status('Consulta Doc-Center indisponível; execução local preservada. ' + str(error), True)
            return None

    @on(Button.Pressed, '.dc-sync')
    def sync_pressed(self):
        self.synchronize()

    @work(group='doc-center', exclusive=True, exit_on_error=False)
    async def synchronize(self):
        session = None
        try:
            session = session_for(self.app.working_directory, self.module, self.status, self.legacy_source())
            if session is None:
                self.refresh_status();return
            identifier = self.app.change.store.identifier if self.module == 'change' else None
            if self.module == 'change' and not identifier:
                self.status('Selecione a solicitação concluída para sincronizar seus resultados.');return
            await session.finish(identifier)
        except asyncio.CancelledError:
            if session:session.cancel()
            raise
        except Exception as error:self.status(str(error), True)

    def on_unmount(self):
        if self.client:self.client.cancel()
