"""TUI do CHANGE, com controles e execução separados dos módulos anteriores."""

import asyncio
import json
import tempfile
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ProgressBar, RadioSet, Select, Static, TextArea

from capivara2ptn.change_flow import ChangeRun
from capivara2ptn.change_source import ChangeError, Secrets
from capivara2ptn.change_state import ChangeStore
from capivara2ptn.dialogs import InterviewDialog
from capivara2ptn.init_progress import InitProgress
from capivara2ptn.module_clock import display_time
from capivara2ptn.plan_panel import PlanPanel
from capivara2ptn.project_config import read_auditor, read_options
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.providers import CliRunner, Selection
from capivara2ptn.doc_center_ui import DocCenterFields


class ChangeInterviewDialog(InterviewDialog):
    def on_mount(self):
        super().on_mount()
        self.query_one('.dialog-title', Static).update('Entrevista CHANGE')


class ChangeRequestDialog(ModalScreen[str | None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Cancelar')]
    DEFAULT_CSS = '''
    ChangeRequestDialog .dialog { width: 90%; max-width: 100; height: 65%; }
    #change-description { height: 1fr; min-height: 6; }
    '''

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Nova solicitação CHANGE', classes='dialog-title')
            yield Static('Descreva o problema ou a alteração desejada. O tipo será identificado pela investigação.', classes='question')
            yield TextArea(id='change-description', tab_behavior='focus')
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar', id='change-new-cancel')
                yield Button('Cadastrar', id='change-new-confirm', variant='primary')

    @on(Button.Pressed, '#change-new-confirm')
    def confirm(self):
        value = self.query_one(TextArea).text.strip()
        if value:
            self.dismiss(value)
        else:
            self.app.notify('Descreva a alteração desejada.', severity='error')

    @on(Button.Pressed, '#change-new-cancel')
    def cancel(self):
        self.dismiss(None)


class ChangePlanDialog(ModalScreen[dict]):
    BINDINGS = [Binding('escape', 'dismiss({"action":"pause"})', 'Voltar')]
    DEFAULT_CSS = '''
    ChangePlanDialog .dialog { width: 95%; max-width: 125; height: 92%; }
    #change-plan-view { height: 1fr; min-height: 6; }
    #change-plan-adjustments { height: 4; }
    #change-plan-recovery { height: auto; max-height: 5; }
    '''

    def __init__(self, markdown, recovery):
        super().__init__()
        self.markdown, self.recovery = markdown, recovery

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Aprovar plano CHANGE', classes='dialog-title')
            yield Static(self.recovery['recovery'] + (' HEAD: ' + self.recovery.get('head', '') if self.recovery.get('available') else ''),
                         id='change-plan-recovery', markup=False)
            yield TextArea(self.markdown, read_only=True, id='change-plan-view', tab_behavior='focus')
            yield TextArea(placeholder='Ajustes desejados no plano', id='change-plan-adjustments', tab_behavior='focus')
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar solicitação', id='change-plan-cancel')
                yield Button('Solicitar ajustes', id='change-plan-adjust')
                yield Button('Aprovar execução', id='change-plan-approve', variant='primary')

    @on(Button.Pressed, '#change-plan-approve')
    def approve(self):
        self.dismiss({'action': 'approve'})

    @on(Button.Pressed, '#change-plan-adjust')
    def adjust(self):
        value = self.query_one('#change-plan-adjustments', TextArea).text.strip()
        if value:
            self.dismiss({'action': 'adjust', 'text': value})
        else:
            self.app.notify('Descreva os ajustes solicitados.', severity='error')

    @on(Button.Pressed, '#change-plan-cancel')
    def cancel(self):
        self.dismiss({'action': 'cancel'})


class ChangeDecisionDialog(ModalScreen[str]):
    BINDINGS = [Binding('escape', 'dismiss("pause")', 'Interromper')]

    def __init__(self, heading, message, accept, decline='Manter escopo'):
        super().__init__()
        self.heading, self.message, self.accept, self.decline = heading, message, accept, decline

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static(self.heading, classes='dialog-title', markup=False)
            yield Static(self.message, classes='question', markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Interromper', id='change-decision-pause')
                yield Button(self.decline, id='change-decision-decline')
                yield Button(self.accept, id='change-decision-approve', variant='primary')

    @on(Button.Pressed, '#change-decision-approve')
    def approve(self):self.dismiss('approve')

    @on(Button.Pressed, '#change-decision-decline')
    def decline_action(self):self.dismiss('decline')

    @on(Button.Pressed, '#change-decision-pause')
    def pause(self):self.dismiss('pause')


class ChangeHistoryDialog(ModalScreen[None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', 'Fechar')]
    DEFAULT_CSS = '''
    ChangeHistoryDialog .dialog { width: 95%; max-width: 140; height: 90%; }
    #change-history-text { height: 1fr; min-height: 8; }
    '''

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Histórico da solicitação CHANGE', classes='dialog-title')
            yield TextArea(self.text, read_only=True, id='change-history-text', tab_behavior='focus')
            yield Button('Fechar', id='change-history-close')

    @on(Button.Pressed, '#change-history-close')
    def close(self):self.dismiss(None)


class ChangeControls(Vertical):
    DEFAULT_CSS = '''
    ChangeControls { display: none; height: auto; }
    #change-request-preview { height: 6; }
    #change-controls-actions { height: 3; }
    #change-controls-actions Button { width: 1fr; min-width: 10; }
    '''

    def __init__(self):
        super().__init__(id='change-controls')

    def compose(self) -> ComposeResult:
        yield Static('Solicitação', classes='field-label')
        yield Select([], prompt='Cadastre uma solicitação', id='change-request')
        yield TextArea(read_only=True, id='change-request-preview', tab_behavior='focus')
        with Horizontal(id='change-controls-actions'):
            yield Button('Nova solicitação', id='change-new')
            yield Button('Histórico', id='change-history', disabled=True)
        yield DocCenterFields('change')

    @on(Button.Pressed, '#change-new')
    def create(self):self.app.change.create_dialog()

    @on(Button.Pressed, '#change-history')
    def history(self):self.app.change.history()

    @on(Select.Changed, '#change-request')
    def selected(self, event):
        if isinstance(event.value, str):
            self.app.change.select(event.value)


class ChangePanel(Vertical):
    DEFAULT_CSS = '''
    ChangePanel { display: none; height: 1fr; min-height: 10; margin-bottom: 1; }
    #change-progress { height: 1; width: 100%; }
    #change-progress Bar { width: 1fr; }
    #change-count { height: auto; }
    #change-task-scroll { height: 1fr; min-height: 5; }
    #change-task-content { height: auto; }
    #change-stop { width: 100%; }
    .changing #progress-card { min-height: 5; }
    .changing #log-card { height: 25%; min-height: 5; max-height: 9; }
    '''

    def __init__(self):
        super().__init__(id='change-panel', classes='card')
        self.bright = True

    def compose(self) -> ComposeResult:
        yield ProgressBar(total=1, show_percentage=False, show_eta=False, id='change-progress')
        yield Static('Nenhuma solicitação selecionada.', id='change-count', markup=False)
        with VerticalScroll(id='change-task-scroll'):
            yield Static('', id='change-task-content', markup=False)
        yield Button('Interromper CHANGE', id='change-stop', disabled=True)

    def on_mount(self):
        self.border_title = 'Etapas e tasks CHANGE'
        self.set_interval(.45, self.pulse)

    def pulse(self):
        if self.app.change.running:
            self.bright = not self.bright
            self.app.change.draw()

    @on(Button.Pressed, '#change-stop')
    def stop(self):self.app.change.cancel()


class ChangeController:
    def __init__(self, app):
        self.app = app
        self.store = ChangeStore(app.working_directory)
        self.running = False
        self.worker = None
        self.visible = False
        self.error = ''

    def selected(self):
        if not self.app.query('#module'):
            return self.app.has_class('changing')
        button = self.app.query_one('#module', RadioSet).pressed_button
        return bool(button and button.id == 'module-change')

    def mount(self):
        self.app.query_one('#module', RadioSet).set_styles(
            'layout: grid; grid-size: 3; grid-columns: 1fr 1fr 1fr; grid-rows: 3 3; height: 6;')
        self.reload()

    def reload(self, selected=None):
        try:
            requests = self.store.requests()
            selector = self.app.query_one('#change-request', Select)
            with self.app.prevent(Select.Changed):
                selector.set_options([(f'{i} · {status} · {title}', i) for i, title, status in requests])
                identifiers = {i for i, _, _ in requests}
                saved = self.store.base_path('selecao.json')
                if selected is None and saved.exists():
                    try:
                        selected = json.loads(saved.read_text()).get('id')
                    except (ValueError, OSError):
                        pass
                if selected not in identifiers:
                    selected = requests[-1][0] if requests else None
                selector.value = selected if selected else Select.BLANK
            if selected:
                self.select(selected, persist=False)
            else:
                self.draw()
        except Exception as error:
            self.error = str(error)
            self.app.write_log('erro', self.error)

    def select(self, identifier, persist=True):
        if self.running:
            return
        try:
            state = self.store.select(identifier)
            self.app.query_one('#change-request-preview', TextArea).load_text(state['original'])
            self.app.query_one('#change-history', Button).disabled = False
            if persist:
                self.store.atomic(self.store.base_path('selecao.json'), json.dumps({'id': identifier}) + '\n')
            self.error = ''
            self.draw()
            if self.selected():self.tick()
        except Exception as error:
            self.error = str(error)
            self.app.notify(self.error, severity='error')

    def show(self, selected):
        self.app.set_class(selected, 'changing')
        self.app.query_one(ChangeControls).display = selected
        self.app.query_one(ChangePanel).display = selected
        if not selected:
            if self.visible:
                self.app.query_one(PlanPanel).styles.display = None
                self.app.query_one('#plan-progress-area').styles.display = None
            self.visible = False
            return
        self.visible = True
        self.app.reverse.show(False)
        self.app.set_class(True, 'planning')
        self.app.query_one(PlanPanel).styles.display = 'none'
        self.app.query_one('#plan-progress-area').styles.display = 'none'
        self.app.query_one(PromptSource).display = False
        self.app.query_one(InitProgress).display = False
        self.app.query_one('#progress-module', Static).update('Módulo: CHANGE')
        self.app.query_one('#scope', Static).update('CHANGE · investigação e alteração localizada')
        self.draw()
        self.tick()

    def tick(self):
        if not self.app.query('#elapsed'):
            return
        self.app.query_one('#elapsed', Static).update('Tempo: ' + display_time(self.store.elapsed()))
        self.app.query_one('#elapsed', Static).tooltip = 'Tempo efetivo da solicitação CHANGE, sem pausas e espera por aprovação.'
        if self.running and self.store.started is not None:
            try:self.store.save()
            except Exception as error:
                self.app.write_log('erro', str(error))
                self.cancel()

    def draw(self, message=''):
        if not self.selected() or any(not self.app.query(selector) for selector in
                (ChangePanel, '#change-task-content', '#change-progress', '#change-count', '#status', '#progress-state', '#stage', '#start', '#change-stop')):
            return
        app, state = self.app, self.store.state
        panel = app.query_one(ChangePanel)
        current = (state or {}).get('phase', 'investigation')
        status = (state or {}).get('status', 'draft')
        stages = [('investigation', 'Investigar'), ('interview', 'Entrevistar'), ('planning', 'Planejar'),
                  ('approval', 'Aprovar plano'), ('execution', 'Executar tasks'), ('verification', 'Verificar alteração')]
        index = next((i for i, (name, _) in enumerate(stages) if name == current), 6 if current == 'done' else 4)
        content = Text()
        def line(label, value, indent=''):
            color = {'pending': '#688398', 'done': '#93d6b4', 'skipped': '#688398', 'failed': '#ff8e8e',
                     'running': '#ffbf3f' if panel.bright else '#96701f'}.get(value, '#688398')
            content.append(indent + '● ', color)
            content.append(label + '\n')
        for i, (_, label) in enumerate(stages):
            value = 'done' if state and i < index else 'pending'
            if i == index:
                value = 'running' if self.running else 'failed' if status in {'failed', 'conflict', 'interrupted'} else 'pending'
            if i == 4 and state and not all(t['status'] == 'done' for t in state['tasks']):
                value = 'running' if self.running and current == 'execution' else 'pending'
            if i == 5 and status == 'done_with_limits':value = 'failed'
            line(label, value)
        for task in (state or {}).get('tasks', []):
            line(f"Task {task['id']} — {task['title']}", 'done' if task['status'] == 'done' else 'running' if self.running and task['status'] == 'running' else 'pending', '   ')
            for role in ('g0', 'writer', 'g1', 'g2'):
                value = task[role]
                if value == 'running' and not self.running:value = 'failed'
                line('Escritor' if role == 'writer' else role.upper(), value, '      ')
            if task.get('error'):content.append('      ' + task['error'] + '\n', style='#ff8e8e')
        if state and state.get('error'):content.append(state['error'], style='#ff8e8e')
        app.query_one('#change-task-content', Static).update(content)
        tasks = (state or {}).get('tasks', [])
        done = sum(t['status'] == 'done' for t in tasks)
        app.query_one('#change-progress', ProgressBar).update(total=max(1, len(tasks)), progress=done)
        app.query_one('#change-count', Static).update(f'{self.store.identifier} · Tasks: {done}/{len(tasks)}' if state else 'Cadastre ou selecione uma solicitação.')
        label = 'Executando' if self.running else {'done': 'Concluído', 'done_with_limits': 'Concluído com limitações',
                 'failed': 'Falha', 'conflict': 'Conflito', 'cancelled': 'Cancelado', 'interrupted': 'Interrompido',
                 'approval': 'Aguardando aprovação', 'scope_review': 'Aguardando decisão', 'execution': 'Interrompido',
                 'investigation': 'Interrompido', 'interview': 'Interrompido', 'planning': 'Interrompido', 'verification': 'Interrompido'}.get(status, 'Aguardando início')
        app.query_one('#status', Static).update('● ' + label)
        app.query_one('#progress-state', Static).update('Estado: ' + label)
        app.query_one('#stage', Static).update(message or self.error or (state or {}).get('error') or 'Selecione Iniciar para investigar ou retomar a solicitação.')
        start = app.query_one('#start', Button)
        start.label = 'Rever verificação' if status == 'done_with_limits' else 'Retomar CHANGE' if state and status != 'draft' else 'Iniciar CHANGE'
        start.disabled = not bool(state) or self.running or bool(self.error) or status in {'done', 'cancelled'}
        app.query_one('#change-stop', Button).disabled = not self.running

    def create_dialog(self):
        if self.running:
            return
        def created(description):
            if description is None:return
            try:
                secrets = Secrets(self.app.working_directory)
                self.store.create(secrets.prose(description))
                self.reload(self.store.identifier)
            except Exception as error:self.app.notify(str(error), severity='error')
        self.app.push_screen(ChangeRequestDialog(), created)

    def history(self):
        if not self.store.state:return
        state = self.store.state
        text = '# ' + state['id'] + '\n\n' + state['original'] + '\n\n'
        for name in ('investigacao.json', 'entrevista.json', 'plano.md', 'conclusao.md', 'execucao.log'):
            path = self.store.path(name)
            if path.exists():text += '\n\n## ' + name + '\n' + path.read_text()
        text += '\n\n## Histórico de execução\n' + json.dumps(state['events'], ensure_ascii=False, indent=2)
        self.app.push_screen(ChangeHistoryDialog(text))

    def start(self):
        app = self.app
        if self.running or app._init_running or app._plan_running or app._build_running or app.reverse.running:return
        if not self.store.state:
            app.notify('Cadastre ou selecione uma solicitação CHANGE.', severity='error');return
        if self.store.state['status'] in {'done', 'cancelled'}:return
        if app.model_id is None:
            app.notify('Selecione o modelo do escritor.', severity='error');return
        try:
            options = read_options(app.working_directory)
            selection = Selection(str(app.query_one('#provider', Select).value), app.model_id, str(app.query_one('#effort', Select).value))
            auditor = read_auditor(app.working_directory).resolve(selection) if options.gates_enabled else None
        except Exception as error:
            app.notify(str(error), severity='error');return
        self.running = True
        app.query_one('#configuration').disabled = True
        self.worker = app.run_worker(self.execute(selection, auditor, options), group='change', exclusive=True, exit_on_error=False)
        self.draw()

    async def execute(self, selection, auditor_selection, options):
        app = self.app
        secrets = Secrets(app.working_directory)
        def log(value):
            value = secrets.prose(value)
            if app.query('#log'):app.write_log('executor', value)
            if self.store.state:
                self.store.log(value)
        try:
            # Cwd neutro: a CLI não recebe AGENTS/configuração operacional do projeto.
            with tempfile.TemporaryDirectory(prefix='capivara-change-model-') as temporary:
                model_cwd = Path(temporary)
                tokens = [server.token for server in app.query_one(PromptSource).servers]
                writer = CliRunner(selection, model_cwd, log, [*tokens, *secrets.values], forbid_automated_tests=options.forbid_automated_tests)
                auditor = (writer if auditor_selection == selection else CliRunner(auditor_selection, model_cwd, log, [*tokens, *secrets.values],
                           forbid_automated_tests=options.forbid_automated_tests)) if auditor_selection else None
                doc_center = await app.query_one('#change-doc-center', DocCenterFields).prepare(self.store.state['original'])
                if doc_center is not None:
                    original_writer = writer
                    writer = doc_center.wrap(writer)
                    auditor = writer if auditor is original_writer else doc_center.wrap(auditor) if auditor else None
                async def ask(question):
                    return await app.push_screen_wait(ChangeInterviewDialog(question['text'], tuple(question['choices']), preview=False))
                async def approve(plan, recovery):
                    return await app.push_screen_wait(ChangePlanDialog(plan, recovery))
                async def scope(request):
                    return await app.push_screen_wait(ChangeDecisionDialog('Ampliação de escopo CHANGE',
                        request['reason'] + '\n\nArquivos adicionais:\n' + '\n'.join(request['files']), 'Autorizar ampliação'))
                async def conflict(paths):
                    answer = await app.push_screen_wait(ChangeDecisionDialog('Código alterado desde o checkpoint',
                        '\n'.join(paths) + '\n\nAtualizar somente a investigação afetada e submeter novo plano? Respostas e tasks ainda válidas serão preservadas.',
                        'Atualizar investigação', 'Manter interrompido'))
                    return answer == 'approve'
                def update(state, message):
                    self.draw(message)
                    self.store.log(message)
                    if app.query('#log'):app.write_log('sistema', message)
                await ChangeRun(self.store, writer, auditor, options, secrets, update, ask, approve, scope, conflict).run()
                if doc_center is not None and self.store.state['status'] in {'done', 'done_with_limits'}:
                    await doc_center.finish(self.store.identifier)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = secrets.safe(str(error))
            if app.is_running:
                app.write_log('erro', message)
                app.notify(message, severity='error', timeout=10)
                self.draw(message)
        finally:
            self.running, self.worker = False, None
            if app.is_running and app.query('#configuration'):
                app.query_one('#configuration').disabled = False
                self.draw()
                app.update_elapsed(checkpoint=False)

    def cancel(self):
        if self.worker:self.worker.cancel()
