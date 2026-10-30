"""Árvore de fases (um status) e tasks (G0–G3) com atualização real."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import ProgressBar, Static

from .plan_progress import COLORS, PlanItemScroll, indicator


class BuildProgress(Vertical):
    DEFAULT_CSS = """
    BuildProgress { height: 1fr; }
    BuildProgress #build-summary { height: 1; color: #b2c1c9; }
    BuildProgress ProgressBar { height: 1; width: 100%; margin-bottom: 1; }
    BuildProgress ProgressBar Bar { width: 1fr; }
    BuildProgress #build-heading { height: 1; color: #05bfd8; }
    BuildProgress #build-scroll { height: 1fr; min-height: 4; scrollbar-size: 1 1; }
    BuildProgress #build-rows { height: auto; width: 1fr; }
    BuildProgress #build-legend { height: 1; color: #7e939e; text-wrap: nowrap; text-overflow: ellipsis; }
    """

    def __init__(self):
        super().__init__(id='build-progress')
        self.event=None;self.running=False;self.pulse=True;self.auto_scroll_y=0

    def compose(self)->ComposeResult:
        yield Static('Aguardando plano aprovado…',id='build-summary',markup=False)
        yield ProgressBar(total=1,show_eta=False,id='build-bar')
        yield Static('',id='build-heading')
        with PlanItemScroll(id='build-scroll'):
            yield Static('',id='build-rows')
        yield Static('Ambiente: status · Tasks: G0 escrita · G1 existência · G2 fidelidade · G3 funcionalidade',id='build-legend')

    def on_mount(self):
        self.set_interval(.5,self.blink)
        self.query_one('#build-heading').tooltip='Fases têm um status. Tasks têm quatro gates. Role a lista para consultar os itens.'
        self.call_after_refresh(self.render_rows)

    def on_resize(self):
        if self.is_mounted:self.call_after_refresh(self.render_rows)

    def blink(self):
        if self.running:
            self.pulse=not self.pulse;self.render_rows()

    def update_event(self,event,running):
        scroll=self.query_one(PlanItemScroll)
        follow=abs(scroll.scroll_y-self.auto_scroll_y)<1 or scroll.scroll_y>=scroll.max_scroll_y-1
        self.event=event;self.running=running
        if event.environment_status!='completed':
            if event.environment_status=='rejected':
                summary=f'Ambiente: instalação pendente · {event.environment_ready}/{event.environment_total} disponíveis'
            elif not running:
                summary='Ambiente: aguardando retomada'
            else:
                summary=(f'Ambiente: {event.environment_ready}/{event.environment_total} dependências disponíveis'
                         if event.environment_total else 'Ambiente: levantando dependências…')
            total,progress=max(1,event.environment_total),event.environment_ready
        elif event.scopes_total and event.scopes_ready<event.scopes_total:
            summary=f'Escopos preparados: {event.scopes_ready}/{event.scopes_total}'
            total,progress=event.scopes_total,event.scopes_ready
        else:
            summary=f'Tasks concluídas: {event.tasks_approved}/{event.tasks_total}'
            total,progress=max(1,event.tasks_total),event.tasks_approved
        self.query_one('#build-summary',Static).update(summary)
        self.query_one(ProgressBar).update(total=total,progress=progress)
        self.render_rows()
        if follow:self.call_after_refresh(self.follow_active)

    def follow_active(self):
        if not self.event:return
        scroll=self.query_one(PlanItemScroll)
        active=[i for i,item in enumerate(self.event.build_items) if
                (item.kind=='task' and 'running' in item.gates) or (item.kind=='preparation' and item.status=='running')]
        if not active:return
        scroll.scroll_to(y=max(0,active[-1]-scroll.scrollable_content_region.height+2),animate=False,immediate=True)
        self.auto_scroll_y=scroll.scroll_y

    def render_rows(self):
        if not self.is_mounted or not self.query('#build-rows'):return
        width=max(28,self.query_one(PlanItemScroll).scrollable_content_region.width-1)
        label_width=width-17
        heading=Text('FASES E TASKS'[:label_width].ljust(label_width),style='#05bfd8')
        heading.append(' G0  G1  G2  G3',style='bold #05bfd8')
        self.query_one('#build-heading',Static).update(heading)
        text=Text(no_wrap=True,overflow='crop')
        for index,item in enumerate(self.event.build_items if self.event else ()):
            if index:text.append('\n')
            title=' '.join(item.title.split())
            name=f'{item.id} {title}' if item.kind=='phase' else f'  {item.id.split("-",1)[-1]} {title}'
            label=Text(name,style='bold #05bfd8' if item.kind=='phase' else '#b2c1c9')
            label.truncate(label_width,overflow='ellipsis',pad=True);text.append_text(label);text.append(' ')
            if item.kind in ('phase','preparation'):
                if item.status=='running':text.append('●' if self.pulse or not self.running else ' ',style=COLORS['running'])
                else:text.append_text(indicator(item.status))
            else:
                for gate,state in enumerate(item.gates):
                    if gate:text.append('   ')
                    text.append_text(indicator(state))
        self.query_one('#build-rows',Static).update(text)
