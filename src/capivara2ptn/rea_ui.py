"""Card e controlador REA; os módulos existentes não participam desta execução."""

import asyncio
import json
import tempfile
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal,Vertical,VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button,DirectoryTree,Input,ProgressBar,RadioSet,Select,Static,TextArea

from capivara2ptn.dialogs import InterviewDialog
from capivara2ptn.init_progress import InitProgress
from capivara2ptn.module_clock import display_time
from capivara2ptn.plan_panel import PlanPanel
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.providers import CliRunner,Selection
from capivara2ptn.rea_common import ReaError,Safe
from capivara2ptn.rea_flow import ReaRun
from capivara2ptn.rea_setup import ReaSetup
from capivara2ptn.rea_state import ReaStore

DEFAULT_REQUEST='Compreender a estrutura e os comportamentos identificáveis do alvo, documentando evidências, inferências e limitações.'


class ReaQuestionDialog(InterviewDialog):
    def on_mount(self):
        super().on_mount()
        self.query_one('.dialog-title',Static).update('Esclarecimento REA')
        self.query_one('.muted',Static).update('Informação necessária para a operação escolhida.')


class ReaTargetDialog(ModalScreen[Path|None]):
    BINDINGS=[Binding('escape','dismiss(None)','Cancelar')]
    DEFAULT_CSS='''ReaTargetDialog .dialog { height: 85%; } #rea-tree { height: 1fr; min-height: 5; }'''
    def __init__(self,path):super().__init__();self.directory=path if path.is_dir() else path.parent
    def compose(self)->ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Selecionar arquivo ou diretório REA',classes='dialog-title')
            yield Input(str(self.directory),id='rea-picker-path')
            with Horizontal(classes='dialog-actions'):
                yield Button('Pasta acima',id='rea-picker-parent')
                yield Button('Navegar',id='rea-picker-navigate')
            yield DirectoryTree(self.directory,id='rea-tree')
            yield Static('',id='rea-picker-error',classes='error',markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar',id='rea-picker-cancel')
                yield Button('Selecionar',id='rea-picker-confirm',variant='primary')
    @on(DirectoryTree.FileSelected)
    @on(DirectoryTree.DirectorySelected)
    def selected(self,event):event.stop();self.query_one(Input).value=str(event.path)
    def path(self):
        try:
            path=Path(self.query_one(Input).value).expanduser().resolve(strict=True)
            if not path.is_file() and not path.is_dir():raise ValueError
            return path
        except (OSError,ValueError,RuntimeError):
            self.query_one('#rea-picker-error',Static).update('Escolha um arquivo regular ou diretório existente.');return None
    @on(Button.Pressed,'#rea-picker-parent')
    def go_parent(self):self.query_one(Input).value=str(Path(self.query_one(Input).value).parent);self.navigate()
    @on(Button.Pressed,'#rea-picker-navigate')
    @on(Input.Submitted,'#rea-picker-path')
    def navigate(self):
        path=self.path()
        if path:self.query_one(DirectoryTree).path=path if path.is_dir() else path.parent
    @on(Button.Pressed,'#rea-picker-confirm')
    def confirm(self):
        path=self.path()
        if path:self.dismiss(path)
    @on(Button.Pressed,'#rea-picker-cancel')
    def cancel(self):self.dismiss(None)


class ReaConsentDialog(ModalScreen[bool]):
    BINDINGS=[Binding('escape','dismiss(False)','Agora não')]
    def __init__(self,plan):super().__init__();self.plan=plan
    def compose(self)->ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static(self.plan['title'],classes='dialog-title',markup=False)
            text='\n'.join('• '+str(item) for item in self.plan.get('items',[]))
            if self.plan.get('destination'):text+='\n\nDestino: '+self.plan['destination']
            if self.plan.get('commands'):text+='\n\nComandos:\n'+'\n'.join(self.plan['commands'])
            if self.plan.get('arguments'):text+='\n\n'+json.dumps(self.plan['arguments'],ensure_ascii=False,indent=2)
            text+='\n\n'+self.plan.get('note','')
            yield Static(text,classes='question',markup=False)
            with Horizontal(classes='dialog-actions'):
                yield Button('Agora não',id='rea-consent-no')
                yield Button('Autorizar e continuar',id='rea-consent-yes',variant='primary')
    @on(Button.Pressed,'#rea-consent-yes')
    def confirm(self):self.dismiss(True)
    @on(Button.Pressed,'#rea-consent-no')
    def cancel(self):self.dismiss(False)


class ReaPrivilegeDialog(ModalScreen[str|None]):
    BINDINGS=[Binding('escape','dismiss(None)','Cancelar')]
    def compose(self)->ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static('Autenticação administrativa',classes='dialog-title')
            yield Static('O sistema exige sudo para os pacotes autorizados. A senha será usada somente no stdin do sudo, sem gravação ou envio ao modelo.',classes='question')
            yield Input(password=True,id='rea-sudo-password')
            with Horizontal(classes='dialog-actions'):
                yield Button('Cancelar',id='rea-sudo-cancel')
                yield Button('Continuar',id='rea-sudo-confirm',variant='primary')
    @on(Button.Pressed,'#rea-sudo-confirm')
    @on(Input.Submitted,'#rea-sudo-password')
    def confirm(self):
        widget=self.query_one(Input);value=widget.value;widget.value='';self.dismiss(value or None)
    @on(Button.Pressed,'#rea-sudo-cancel')
    def cancel(self):self.query_one(Input).value='';self.dismiss(None)


class ReaDocumentDialog(ModalScreen[None]):
    BINDINGS=[Binding('escape','dismiss(None)','Fechar')]
    DEFAULT_CSS='''ReaDocumentDialog .dialog { width: 95%; height: 90%; max-width: 140; } ReaDocumentDialog TextArea { height: 1fr; }'''
    def __init__(self,text):super().__init__();self.text=text
    def compose(self)->ComposeResult:
        with Vertical(classes='dialog'):
            yield Static('Documentação REA',classes='dialog-title')
            yield TextArea(self.text,read_only=True,tab_behavior='focus')
            yield Button('Fechar',id='rea-document-close')
    @on(Button.Pressed,'#rea-document-close')
    def close(self):self.dismiss(None)


class ReaControls(Vertical):
    DEFAULT_CSS='''ReaControls { display: none; height: auto; } #rea-request { height: 4; } #rea-actions { height: 3; } #rea-actions Button { width: 1fr; min-width: 8; }'''
    def compose(self)->ComposeResult:
        yield Static('Arquivo ou diretório de análise',classes='field-label')
        yield Input(placeholder='Caminho do alvo',id='rea-target')
        yield Button('Selecionar alvo',id='rea-select-target')
        yield Static('Objetivo da investigação',classes='field-label')
        yield TextArea(DEFAULT_REQUEST,id='rea-request',tab_behavior='focus')
        with Horizontal(id='rea-actions'):
            yield Button('Nova investigação',id='rea-new')
            yield Button('Documentação',id='rea-document',disabled=True)
    @on(Button.Pressed,'#rea-select-target')
    def select(self):
        value=self.query_one(Input).value
        path=Path(value).expanduser() if value else self.app.working_directory
        self.app.push_screen(ReaTargetDialog(path),lambda result:self.query_one(Input).__setattr__('value',str(result)) if result else None)
    @on(Button.Pressed,'#rea-new')
    def new(self):self.app.rea.new()
    @on(Button.Pressed,'#rea-document')
    def document(self):self.app.rea.document()


class ReaPanel(Vertical):
    DEFAULT_CSS='''
    ReaPanel { display: none; height: 1fr; min-height: 10; margin-bottom: 1; }
    #rea-progress { height: 1; width: 100%; } #rea-progress Bar { width: 1fr; }
    #rea-details-scroll { height: 1fr; min-height: 5; } #rea-details { height: auto; }
    #rea-stop { width: 100%; } .reanalyzing #log-card { height: 30%; min-height: 5; max-height: 12; }
    '''
    def __init__(self):super().__init__(classes='card');self.bright=True
    def compose(self)->ComposeResult:
        yield ProgressBar(total=1,show_percentage=False,show_eta=False,id='rea-progress')
        with VerticalScroll(id='rea-details-scroll'):yield Static('Selecione o alvo para iniciar.',id='rea-details',markup=False)
        yield Button('Interromper REA',id='rea-stop',disabled=True)
    def on_mount(self):self.border_title='Investigação REA · MCP';self.set_interval(.45,self.pulse)
    def pulse(self):
        if self.app.rea.running:self.bright=not self.bright;self.app.rea.draw()
    @on(Button.Pressed,'#rea-stop')
    def stop(self):self.app.rea.cancel()


class ReaController:
    def __init__(self,app):
        self.app=app;self.store=ReaStore(app.working_directory);self.running=False;self.worker=None;self.visible=False;self.error='';self.message=''
    def selected(self):
        button=self.app.query_one('#module',RadioSet).pressed_button if self.app.query('#module') else None
        return bool(button and button.id=='module-rea')
    def mount(self):
        try:
            state=self.store.load()
            if state:
                self.app.query_one('#rea-target',Input).value=state['target'];self.app.query_one('#rea-request',TextArea).load_text(state['request'])
        except Exception as error:self.error=str(error);self.app.write_log('erro',self.error)
    def show(self,selected):
        self.app.set_class(selected,'reanalyzing');self.app.query_one(ReaControls).display=selected;self.app.query_one(ReaPanel).display=selected
        if not selected:
            if self.visible:
                self.app.query_one(PlanPanel).styles.display=None;self.app.query_one('#plan-progress-area').styles.display=None
            self.visible=False;return
        self.visible=True;self.app.change.show(False);self.app.reverse.show(False)
        self.app.set_class(True,'planning');self.app.query_one(PlanPanel).styles.display='none';self.app.query_one('#plan-progress-area').styles.display='none'
        self.app.query_one(PromptSource).display=False;self.app.query_one(InitProgress).display=False
        self.app.query_one('#scope',Static).update('REA · investigação pelo servidor MCP oficial')
        self.app.query_one('#progress-module',Static).update('Módulo: REA');self.draw();self.tick()
    def draw(self,message=''):
        if message:self.message=message
        if not self.selected() or not self.app.query('#rea-details'):return
        state=self.store.state or {};status=state.get('status','pending');panel=self.app.query_one(ReaPanel)
        label='Executando' if self.running else {'done':'Concluído','done_with_limits':'Concluído com limitações','failed':'Falha','interrupted':'Interrompido'}.get(status,'Aguardando início')
        color=('#ffbf3f' if panel.bright else '#96701f') if self.running else '#93d6b4' if status=='done' else '#ffbf3f' if status=='done_with_limits' else '#ff8e8e' if status=='failed' else '#688398'
        text=Text('● ',style=color);text.append(label+'\nOperação: '+state.get('operation','seleção do alvo')+'\n')
        text.append('Chamadas registradas: '+str(len(state.get('calls',[])))+'\n')
        if state.get('tool_count'):text.append(f"Ferramentas MCP descobertas: {state['tool_count']}\n")
        for call in state.get('calls',[])[-12:]:text.append(('● ' if not call['error'] else '● Falha · ')+call['tool']+'\n',style='#93d6b4' if not call['error'] else '#ff8e8e')
        if state.get('error'):text.append(state['error'],style='#ff8e8e')
        self.app.query_one('#rea-details',Static).update(text)
        self.app.query_one('#rea-progress',ProgressBar).update(total=None if self.running else 1,progress=1 if status in {'done','done_with_limits'} else 0)
        self.app.query_one('#status',Static).update('● '+label);self.app.query_one('#progress-state',Static).update('Estado: '+label)
        self.app.query_one('#stage',Static).update(self.message or self.error or state.get('error') or 'Selecione Iniciar para preparar o REA e investigar.')
        start=self.app.query_one('#start',Button);start.label='Retomar REA' if state else 'Iniciar REA';start.disabled=self.running or bool(self.error) or status in {'done','done_with_limits'}
        self.app.query_one('#rea-stop',Button).disabled=not self.running
        self.app.query_one('#rea-document',Button).disabled=not bool(state.get('documentation'))
    def tick(self):
        self.app.query_one('#elapsed',Static).update('Tempo: '+display_time(self.store.elapsed()))
        self.app.query_one('#elapsed',Static).tooltip='Execução efetiva do REA, sem pausas ou espera por autorização.'
        if self.running and self.store.started is not None:
            try:self.store.save()
            except Exception as error:self.app.write_log('erro',str(error));self.cancel()
    def new(self):
        if self.running:return
        async def archive():
            if not await self.app.push_screen_wait(ReaConsentDialog({'title':'Nova investigação REA','items':['Arquivar os resultados REA atuais, sem apagar evidências.'],
                'note':'INIT, PLAN, BUILD, REVERSE, CHANGE e o alvo serão preservados.'})):return
            self.store.archive();self.error='';self.message='';self.draw()
        if self.store.state or self.store.base.exists():self.app.run_worker(archive(),exit_on_error=False)
    def document(self):
        try:
            if self.store.state and self.store.state.get('documentation'):
                self.app.push_screen(ReaDocumentDialog(self.store.path(self.store.state['documentation']).read_text()))
        except Exception as error:self.app.notify(str(error),severity='error')
    def start(self):
        app=self.app
        if self.running or app._init_running or app._plan_running or app._build_running or app.reverse.running or app.change.running:return
        if app.model_id is None:app.notify('Selecione provedor, modelo e effort.',severity='error');return
        try:
            raw=app.query_one('#rea-target',Input).value.strip()
            if not raw:raise ReaError('Selecione o alvo para investigar.')
            target=Path(raw).expanduser()
            if not target.is_absolute():target=app.working_directory/target
            target=target.resolve(strict=True)
            request=app.query_one('#rea-request',TextArea).text.strip() or DEFAULT_REQUEST
            selection=Selection(str(app.query_one('#provider',Select).value),app.model_id,str(app.query_one('#effort',Select).value))
        except Exception as error:app.notify(str(error),severity='error');return
        self.running=True;app.query_one('#configuration').disabled=True
        self.worker=app.run_worker(self.execute(target,request,selection),group='rea',exclusive=True,exit_on_error=False);self.draw()
    async def execute(self,target,request,selection):
        app=self.app;safe=Safe(app.working_directory,target,[s.token for s in app.query_one(PromptSource).servers])
        def log(text):
            text=safe.text(text)
            if self.store.state:self.store.log(text)
            if app.query('#log'):app.write_log('executor',text)
            if app.is_running:self.draw(text)
        def update(state,text):
            if app.query('#log'):app.write_log('sistema',text)
            if app.is_running:self.draw(text)
        async def approve(plan):return await app.push_screen_wait(ReaConsentDialog(safe.value(plan)))
        async def privilege():return await app.push_screen_wait(ReaPrivilegeDialog())
        async def ask(question):return await app.push_screen_wait(ReaQuestionDialog(question['text'],tuple(question['choices']),preview=False))
        try:
            setup=ReaSetup(log,safe.text,approve,privilege)
            with tempfile.TemporaryDirectory(prefix='capivara-rea-model-') as directory:
                writer=CliRunner(selection,Path(directory),log,[*safe.tokens])
                safe.tokens.update(writer.secrets)
                await ReaRun(self.store,writer,setup,safe,update,approve,ask).run(target,safe.text(request))
        except asyncio.CancelledError:raise
        except Exception as error:
            message=safe.text(str(error))
            if app.is_running:app.write_log('erro',message);app.notify(message,severity='error',timeout=12);self.draw(message)
        finally:
            self.running=False;self.worker=None
            if app.is_running and app.query('#configuration'):app.query_one('#configuration').disabled=False;self.draw();self.tick()
    def cancel(self):
        if self.worker:self.worker.cancel()
