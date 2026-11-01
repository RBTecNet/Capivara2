"""Controles e executor exclusivos do REVERSE; integrações localizadas na aplicação."""

import asyncio
import json
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, ProgressBar, RadioButton, RadioSet, Select, Static, TextArea

from capivara2ptn.init_progress import InitProgress
from capivara2ptn.module_clock import display_time
from capivara2ptn.plan_panel import PlanPanel
from capivara2ptn.project_config import read_auditor
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.providers import CliRunner, Selection
from capivara2ptn.reverse_flow import domains_valid, run_reverse
from capivara2ptn.reverse_source import ReverseError, digest, redact, validate_roots
from capivara2ptn.reverse_state import ReverseStore


class ReverseConfirmDialog(ModalScreen[bool]):
    BINDINGS = [Binding('escape', 'dismiss(False)', 'Cancelar')]

    def __init__(self, title: str, message: str, accept: str) -> None:
        super().__init__()
        self.heading, self.message, self.accept = title, message, accept

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static(self.heading, classes='dialog-title', markup=False)
            yield Static(self.message, classes='question', markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Agora não', id='reverse-no')
                yield Button(self.accept, id='reverse-yes', variant='primary')

    @on(Button.Pressed, '#reverse-no')
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, '#reverse-yes')
    def confirm(self) -> None:
        self.dismiss(True)


class ReverseDirectoryDialog(ModalScreen[Path | None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Cancelar')]
    DEFAULT_CSS = '''
    ReverseDirectoryDialog .dialog { height: 85%; }
    #reverse-tree { height: 1fr; min-height: 5; }
    #reverse-folder-error { height: auto; }
    '''

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory if directory.is_dir() else Path.home()

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Selecionar projeto legado', classes='dialog-title')
            yield Input(str(self.directory), id='reverse-folder-path')
            with Horizontal(classes='dialog-actions'):
                yield Button('Pasta acima', id='reverse-parent')
                yield Button('Navegar', id='reverse-navigate')
            yield DirectoryTree(self.directory, id='reverse-tree')
            yield Static('', classes='error', id='reverse-folder-error', markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar', id='reverse-folder-cancel')
                yield Button('Selecionar pasta', id='reverse-folder-confirm', variant='primary')

    @on(DirectoryTree.DirectorySelected)
    def selected(self, event: DirectoryTree.DirectorySelected) -> None:
        event.stop()
        self.query_one('#reverse-folder-path', Input).value = str(event.path)

    @on(Button.Pressed, '#reverse-parent')
    def go_parent(self) -> None:
        self.query_one('#reverse-folder-path', Input).value = str(Path(self.query_one('#reverse-folder-path', Input).value).expanduser().parent)
        self.navigate()

    def path(self) -> Path | None:
        try:
            path = Path(self.query_one('#reverse-folder-path', Input).value).expanduser().resolve(strict=True)
            if not path.is_dir():
                raise ValueError
            return path
        except (ValueError, OSError, RuntimeError):
            self.query_one('#reverse-folder-error', Static).update('Selecione um diretório existente.')
            return None

    @on(Input.Submitted, '#reverse-folder-path')
    @on(Button.Pressed, '#reverse-navigate')
    def navigate(self) -> None:
        path = self.path()
        if path is not None:
            self.query_one(DirectoryTree).path = path

    @on(Button.Pressed, '#reverse-folder-confirm')
    def confirm(self) -> None:
        path = self.path()
        if path is not None:
            self.dismiss(path)

    @on(Button.Pressed, '#reverse-folder-cancel')
    def cancel(self) -> None:
        self.dismiss(None)


class ReverseDomainsDialog(ModalScreen[list[dict] | None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Cancelar')]
    DEFAULT_CSS = '''
    ReverseDomainsDialog .dialog { width: 95%; max-width: 120; height: 90%; }
    #reverse-domains-editor { height: 1fr; min-height: 8; }
    #reverse-domains-error { height: auto; }
    '''

    def __init__(self, domains: list[dict]) -> None:
        super().__init__()
        self.domains = domains
        self.paths = {p for d in domains for p in d['files']}

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Confirmar domínios do legado', classes='dialog-title')
            yield Static('Revise nomes, finalidade, arquivos e relações. Você pode ajustar o JSON; todos os arquivos relevantes devem permanecer classificados.', classes='question')
            editable = [{k: v for k, v in d.items() if k not in ('id', 'status')} for d in self.domains]
            yield TextArea(json.dumps(editable, ensure_ascii=False, indent=2), id='reverse-domains-editor', tab_behavior='indent')
            yield Static('', id='reverse-domains-error', classes='error', markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Interromper', id='reverse-domains-cancel')
                yield Button('Aprovar domínios', id='reverse-domains-confirm', variant='primary')

    @on(Button.Pressed, '#reverse-domains-confirm')
    def confirm(self) -> None:
        try:
            value = json.loads(self.query_one(TextArea).text)
            domains = domains_valid(value, self.paths)
            if {p for d in domains for p in d['files']} != self.paths:
                raise ReverseError('Existem arquivos relevantes sem domínio.')
        except (ValueError, ReverseError) as error:
            self.query_one('#reverse-domains-error', Static).update(str(error))
            return
        self.dismiss(domains)

    @on(Button.Pressed, '#reverse-domains-cancel')
    def cancel(self) -> None:
        self.dismiss(None)


class ReverseDocumentDialog(ModalScreen[None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Fechar')]
    DEFAULT_CSS = '''
    ReverseDocumentDialog .dialog { width: 95%; max-width: 140; height: 90%; }
    #reverse-document { height: 1fr; min-height: 8; }
    '''

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Documentação REVERSE', classes='dialog-title')
            yield TextArea(self.text, read_only=True, id='reverse-document', tab_behavior='focus')
            yield Button('Fechar', id='reverse-document-close')

    @on(Button.Pressed, '#reverse-document-close')
    def close(self) -> None:
        self.dismiss(None)


class ReverseControls(Vertical):
    DEFAULT_CSS = '''
    ReverseControls { display: none; height: auto; }
    ReverseControls Button { width: 100%; margin-top: 1; }
    #reverse-command { height: 3; }
    '''

    def __init__(self) -> None:
        super().__init__(id='reverse-controls')

    def compose(self) -> ComposeResult:
        yield Static('Diretório legado', classes='field-label')
        yield Input(placeholder='Caminho do projeto somente para leitura', id='reverse-source')
        yield Button('Selecionar projeto legado', id='reverse-choose')
        yield Static('Verificação opcional: comando autorizado em cópia sanitizada, sem rede, com Bubblewrap. Deixe vazio para análise estática.', classes='muted')
        yield Input(placeholder='Ex.: python3 --version', id='reverse-command')
        yield Button('Preparar reexecução', id='reverse-restart')

    @on(Button.Pressed, '#reverse-choose')
    def choose(self) -> None:
        self.app.reverse.choose_directory()

    @on(Button.Pressed, '#reverse-restart')
    def restart(self) -> None:
        self.app.reverse.prepare_restart()


class ReversePanel(Vertical):
    DEFAULT_CSS = '''
    ReversePanel { display: none; height: 1fr; min-height: 10; margin-bottom: 1; }
    #reverse-progress { height: 1; width: 100%; }
    #reverse-progress Bar { width: 1fr; }
    #reverse-count { height: auto; }
    #reverse-domains { height: 1fr; min-height: 4; }
    #reverse-content { height: auto; }
    #reverse-actions { height: auto; layout: horizontal; }
    #reverse-actions Button { width: 1fr; min-width: 12; }
    .reverse #log-card { height: 25%; min-height: 5; max-height: 9; }
    .reverse #progress-card { min-height: 5; }
    '''

    def __init__(self) -> None:
        super().__init__(id='reverse-panel', classes='card')
        self.bright = True

    def compose(self) -> ComposeResult:
        yield ProgressBar(total=1, show_percentage=False, show_eta=False, id='reverse-progress')
        yield Static('Inventário ainda não iniciado.', id='reverse-count', markup=False)
        with VerticalScroll(id='reverse-domains'):
            yield Static('', id='reverse-content', markup=False)
        with Horizontal(id='reverse-actions'):
            yield Button('Interromper', id='reverse-stop', disabled=True)
            yield Button('Consultar', id='reverse-open', disabled=True)
            yield Button('Continuar para INIT', id='reverse-init', disabled=True)

    def on_mount(self) -> None:
        self.border_title = 'Etapas e domínios'
        self.set_interval(0.45, self.pulse)

    def pulse(self) -> None:
        if self.app.reverse.running:
            self.bright = not self.bright
            self.app.reverse.draw()

    @on(Button.Pressed, '#reverse-stop')
    def stop(self) -> None:
        self.app.reverse.cancel()

    @on(Button.Pressed, '#reverse-open')
    def open(self) -> None:
        self.app.reverse.consult()

    @on(Button.Pressed, '#reverse-init')
    def continue_init(self) -> None:
        self.app.reverse.continue_init()


class ReverseController:
    def __init__(self, app) -> None:
        self.app = app
        self.store = ReverseStore(app.working_directory)
        self.running = False
        self.worker = None
        self.restart = False
        self.error = ''

    def selected(self) -> bool:
        if not self.app.query('#module') or not self.app.query('#elapsed'):
            # Consome apenas o callback da integração quando a tela já foi
            # desmontada; tick() não acessa controles ausentes nem outros clocks.
            return True
        button = self.app.query_one('#module', RadioSet).pressed_button
        return bool(button and button.id == 'module-reverse')

    def mount(self) -> None:
        try:
            state = self.store.load()
            if state:
                self.app.query_one('#reverse-source', Input).value = state['source']
        except Exception as error:
            self.error = str(error)
            self.app.write_log('erro', self.error)

    def show(self, reverse: bool) -> None:
        app = self.app
        app.set_class(reverse, 'reverse')
        app.query_one(ReverseControls).display = reverse
        app.query_one(ReversePanel).display = reverse
        # Retira somente a sobreposição da vista REVERSE; CSS original decide as demais.
        app.query_one(PlanPanel).styles.display = 'none' if reverse else None
        app.query_one('#plan-progress-area').styles.display = 'none' if reverse else None
        if not reverse:
            return
        app.set_class(False, 'planning')
        app.query_one(PromptSource).display = False
        app.query_one(InitProgress).display = False
        app.query_one('#progress-module', Static).update('Módulo: REVERSE')
        app.query_one('#scope', Static).update('REVERSE · investigação somente de leitura')
        self.draw()
        self.tick()

    def tick(self) -> None:
        if not self.app.query('#elapsed'):
            return
        self.app.query_one('#elapsed', Static).update('Tempo: ' + display_time(self.store.elapsed()))
        self.app.query_one('#elapsed', Static).tooltip = 'Tempo efetivo do REVERSE; pausas e espera por aprovação não são contabilizadas.'
        if self.running and self.store.started is not None:
            try:
                self.store.save()
            except Exception as error:
                self.app.write_log('erro', str(error))
                self.cancel()

    def draw(self, message: str = '') -> None:
        if not self.selected() or not self.app.query(ReversePanel):
            return
        app, state = self.app, self.store.state
        panel = app.query_one(ReversePanel)
        text = Text()
        def line(status: str, label: str) -> None:
            color = {'pending': '#688398', 'done': '#93d6b4', 'failed': '#ff8e8e',
                     'running': '#ffbf3f' if panel.bright else '#96701f'}[status]
            text.append('● ', style=color)
            text.append(label + '\n')
        stages = [('inventory', 'Inventário'), ('discovery', 'Descoberta'), ('approval', 'Aprovação dos domínios'),
                  ('investigation', 'Investigação por domínio'), ('consolidation', 'Prompt consolidado')]
        current = (state or {}).get('stage', 'inventory')
        stage_index = next((i for i, (name, _) in enumerate(stages) if name == current), len(stages) if current == 'done' else 0)
        status = (state or {}).get('status', 'pending')
        for index, (name, label) in enumerate(stages):
            item_status = 'done' if state and (status == 'done' or index < stage_index) else 'pending'
            if state and index == stage_index:
                item_status = 'running' if self.running else ('failed' if status in ('failed', 'interrupted') else 'pending')
            line(item_status, label)
        for domain in (state or {}).get('domains', []):
            value = domain.get('status', 'pending')
            if value == 'running' and not self.running:
                value = 'failed'
            line(value, '   ' + domain['name'])
        if state and state.get('error'):
            text.append(state['error'], style='#ff8e8e')
        app.query_one('#reverse-content', Static).update(text)
        expected = set((state or {}).get('planned', []))
        done = len(expected & (state or {}).get('units', {}).keys())
        app.query_one('#reverse-progress', ProgressBar).update(total=max(1, len(expected)), progress=done)
        app.query_one('#reverse-count', Static).update(f'Unidades investigadas: {done}/{len(expected)} · Domínios: ' +
            f"{sum(d.get('status') == 'done' for d in (state or {}).get('domains', []))}/{len((state or {}).get('domains', []))}" if expected else 'Aguardando definição das unidades após inventário e aprovação.')
        label = 'Executando' if self.running else ('Concluído' if status == 'done' else 'Falha' if status == 'failed' else 'Interrompido' if state else 'Ociosa')
        app.query_one('#status', Static).update('● ' + label)
        app.query_one('#progress-state', Static).update('Estado: ' + label)
        app.query_one('#stage', Static).update(message or self.error or (state or {}).get('error') or 'Pressione Iniciar/Retomar para investigar o legado selecionado.')
        button = app.query_one('#start', Button)
        button.label = 'Iniciar reexecução' if self.restart else ('Verificar alterações' if status == 'done' else 'Retomar' if state else 'Iniciar')
        button.disabled = bool(self.error) or self.running
        app.query_one('#reverse-stop', Button).disabled = not self.running
        app.query_one('#reverse-open', Button).disabled = not bool(state)
        app.query_one('#reverse-init', Button).disabled = status != 'done' or self.running

    def choose_directory(self) -> None:
        value = self.app.query_one('#reverse-source', Input).value.strip()
        directory = Path(value).expanduser() if value else self.app.working_directory.parent
        def selected(path):
            if path is None:
                return
            try:
                _, source = validate_roots(self.app.working_directory, path)
            except Exception as error:
                self.app.notify(str(error), severity='error')
                return
            self.app.query_one('#reverse-source', Input).value = str(source)
        self.app.push_screen(ReverseDirectoryDialog(directory), selected)

    def prepare_restart(self) -> None:
        if not self.store.state:
            self.app.notify('Nenhuma investigação salva para refazer.')
            return
        def decided(accepted):
            self.restart = accepted
            self.draw('Reexecução preparada; o histórico só será arquivado quando Iniciar for pressionado.' if accepted else '')
        self.app.push_screen(ReverseConfirmDialog('Preparar reexecução do REVERSE',
            'Ao pressionar Iniciar, inventário, domínios, evidências, documentação, estado, tempo e prompt-final do REVERSE serão substituídos por uma nova investigação. '
            'Os resultados anteriores serão preservados em .harness/reverse/historico/. '
            'O legado e os artefatos de INIT, PLAN e BUILD serão preservados. Se INIT já utilizou o prompt anterior, o novo resultado não atualizará automaticamente PRD, PLAN ou BUILD.',
            'Preparar reexecução'), decided)

    def start(self) -> None:
        app = self.app
        if self.running or app._init_running or app._plan_running or app._build_running:
            return
        if self.error:
            app.notify(self.error, severity='error')
            return
        if app.model_id is None:
            app.notify('Selecione um modelo antes de iniciar REVERSE.', severity='error')
            return
        value = app.query_one('#reverse-source', Input).value.strip()
        if not value:
            app.notify('Selecione o projeto legado.', severity='error')
            return
        try:
            source = Path(value).expanduser()
            if not source.is_absolute():
                source = app.working_directory / source
            _, source = validate_roots(app.working_directory, source)
            selection = Selection(str(app.query_one('#provider', Select).value), app.model_id,
                                  str(app.query_one('#effort', Select).value))
            auditor = read_auditor(app.working_directory).resolve(selection)
        except Exception as error:
            app.notify(str(error), severity='error')
            return
        self.running = True
        app.query_one('#configuration').disabled = True
        self.worker = app.run_worker(self.execute(source, selection, auditor), group='reverse', exclusive=True, exit_on_error=False)
        self.draw()

    async def execute(self, source: Path, selection: Selection, auditor_selection: Selection) -> None:
        app = self.app
        secrets = [server.token for server in app.query_one(PromptSource).servers]
        runner = CliRunner(selection, app.working_directory, lambda text: app.write_log('executor', redact(text)), secrets)
        auditor = runner if auditor_selection == selection else CliRunner(auditor_selection, app.working_directory,
                    lambda text: app.write_log('executor', redact('Auditor · ' + text)), secrets)
        try:
            # Arquivar somente após validar caminhos e escolhas, no início efetivo.
            if self.restart:
                self.store.archive()
                self.restart = False
            async def approve(domains):
                return await app.push_screen_wait(ReverseDomainsDialog(domains))
            async def changes(paths, names):
                return await app.push_screen_wait(ReverseConfirmDialog('Alterações no legado',
                    'Arquivos alterados, adicionados ou removidos:\n' + '\n'.join(paths) + '\n\nDomínios afetados:\n' +
                    ('\n'.join(names) or 'Novos arquivos exigem classificação.') +
                    '\n\nAtualizar somente as análises afetadas? Resultados válidos serão preservados. O prompt consolidado será gerado novamente; INIT, PLAN e BUILD não serão atualizados.', 'Atualizar investigação'))
            def update(state, message):
                self.draw(message)
                app.write_log('sistema', message)
            path = await run_reverse(self.store, source, runner, approve, changes, update, auditor=auditor,
                                     verification_command=app.query_one('#reverse-command', Input).value.strip())
            self.store.pause_clock()
            if path:
                app.write_log('sucesso', f'Prompt REVERSE concluído: {path}')
                if await app.push_screen_wait(ReverseConfirmDialog('REVERSE → INIT',
                    'Selecionar INIT com origem Arquivo e o prompt consolidado? A entrevista só começará quando você pressionar Iniciar. Provedor, modelo e effort serão preservados.', 'Continuar para INIT')):
                    self.continue_init()
        except asyncio.CancelledError:
            try:
                self.store.fail('REVERSE interrompido. Resultados confirmados preservados; pressione Retomar.', interrupted=True)
            except Exception:
                if app.is_running:
                    app.write_log('erro', 'Não foi possível salvar a interrupção; resultados intermediários anteriores permanecem em .harness/reverse/.')
            raise
        except Exception as error:
            message = redact(runner.safe(str(error)))
            try:
                self.store.fail(message)
            except Exception:
                message += '\nNão foi possível salvar o estado; verifique espaço e permissões em .harness/reverse/.'
            if app.is_running:
                app.write_log('erro', message)
                self.draw(message)
                app.notify(message, severity='error', timeout=10)
        finally:
            try:
                self.store.pause_clock()
            except Exception:
                if app.is_running:
                    app.write_log('erro', 'Não foi possível salvar o cronômetro do REVERSE. Verifique espaço e permissões.')
            self.running, self.worker = False, None
            if app.is_running:
                app.query_one('#configuration').disabled = False
                self.draw()
                app.update_elapsed(checkpoint=False)

    def cancel(self) -> None:
        if self.worker:
            self.worker.cancel()

    def consult(self) -> None:
        try:
            final = self.store.target('prompt-final.md')
            if final.exists():
                text = final.read_text(encoding='utf-8')
            else:
                text = '\n\n'.join(self.store.target(f"dominios/{d['id']}.md").read_text(encoding='utf-8')
                                   for d in self.store.state['domains'] if d['status'] == 'done' and self.store.target(f"dominios/{d['id']}.md").exists())
                if not text:
                    text = json.dumps(self.store.state, ensure_ascii=False, indent=2)
                text = 'Investigação em andamento. Somente domínios concluídos nesta versão são apresentados; estado e evidências intermediárias estão preservados.\n\n' + text
            self.app.push_screen(ReverseDocumentDialog(text))
        except Exception as error:
            self.app.notify(str(error), severity='error')

    def continue_init(self) -> None:
        try:
            state = self.store.state
            path = self.store.target('prompt-final.md')
            if not state or state['status'] != 'done' or digest(path.read_bytes()) != state.get('prompt_sha256'):
                raise ReverseError('Prompt consolidado ausente, alterado ou investigação incompleta.')
            self.app.query_one('#prompt-source', Select).value = 'file'
            self.app.query_one('#prompt-path', Input).value = str(path)
            self.app.query_one('#module-init', RadioButton).value = True
            self.app.write_log('sistema', 'INIT selecionado com o prompt REVERSE. Pressione Iniciar para a entrevista; seleções preservadas.')
        except Exception as error:
            self.app.notify(str(error), severity='error')
