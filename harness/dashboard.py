"""Dashboard operacional do init, com composição adaptável ao terminal."""

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, RichLog, Static

from .dashboard_art import wordmark
from .domain import AgentSelection, PROVIDERS
from .run_events import InitEvent
from .mascot import Mascot
from .plan_progress import PlanProgress
from .build_progress import BuildProgress


CYAN = "#05bfd8"
MUTED = "#7e939e"
BODY = "#b2c1c9"
GREEN = "#a4d744"
YELLOW = "#ffbd38"
RED = "#f27f89"
STAGES = {
    "starting": (0, "Preparação"),
    "reviewing_initial": (1, "Análise inicial"),
    "preparing_interview": (2, "Preparando entrevista"),
    "interviewing": (2, "Entrevista"),
    "inferring_internal_tools": (2, "Inferências internas"),
    "writing": (3, "Redação do prompt"),
    "reviewing_final": (4, "Revisão documental"),
    "revision_requested": (4, "Ajustes solicitados"),
    "completed": (5, "Finalizando"),
}
PIPELINE = ("Pedido", "Análise", "Entrevista", "Redação", "Revisão", "Concluído")
PLAN_STAGES = {
    "classifying": (0, "Classificando responsabilidades"),
    "reviewing_classification": (1, "Validando responsabilidades"),
    "correcting_task": (2, "Aplicando contrato de correção"),
    "contract_conflict": (4, "Conflito de contrato"),
    "starting": (0, "Prompt do init"), "decomposing": (1, "Divisão da fase"),
    "writing_task": (2, "Redação da task"), "reviewing_task": (3, "Revisão da task"),
    "reviewing_phase": (4, "Revisão da fase"), "reviewing_plan": (4, "Revisão do plano"),
    "revision_requested": (3, "Correção documental"), "reassessing_phase": (4, "Reavaliação de escopo"),
    "completed": (5, "Finalizando"),
}
BUILD_STAGES = {
    "delivery_analysis":(4,"Levantando dependências da entrega"),
    "delivery_install":(4,"Instalação limpa"), "delivery_compile":(4,"Compilação e tipagem"),
    "delivery_startup":(4,"Inicialização operacional"), "delivery_diagnosis":(4,"Diagnosticando instalação"),
    "delivery_repair":(4,"Corrigindo instalação"), "delivery_approved":(5,"Instalação aprovada"),
    "delivery_pending":(5,"Concluído · instalação pendente"),
    "diagnosing":(4,"Diagnosticando repetição"), "verification_repair":(4,"Reparando verificação"),
    "verification_repaired":(4,"Verificação reparada"), "scope_recovery":(1,"Ajustando reserva"),
    "scope_recovered":(1,"Reserva ajustada"), "task_recovery":(2,"Recuperando task"),
    "writer_recovery":(2,"Corrigindo entrega"), "auditor_recovery":(4,"Recuperando auditor"),
    "dependencies": (0,"Verificando dependências"), "installing_dependencies":(0,"Instalando dependências"),
    "dependencies_ready":(0,"Ambiente preparado"), "dependencies_blocked":(0,"Instalação manual necessária"),
    "scheduling":(1,"Organizando tasks"), "g0":(2,"G0 · Escrita"), "g1":(3,"G1 · Existência"),
    "g2":(4,"G2 · Fidelidade"), "g3":(4,"G3 · Funcionalidade"), "completed":(5,"Concluído")}
PLAN_PIPELINE = ("Origem", "Divisão", "Tasks", "Revisão", "Fases", "Concluído")
STATUS = {"ready": ("pronto", MUTED), "running": ("em andamento", YELLOW),
          "completed": ("concluído", GREEN), "blocked": ("instalação manual", YELLOW), "cancelled": ("interrompido", YELLOW), "failed": ("falhou", RED)}


class DashboardField(Vertical):
    def __init__(self, label: str, value: str, *, id: str, tooltip: str | None = None) -> None:
        super().__init__(id=id, classes="dash-field")
        self.label = label
        self.value = value
        if tooltip:
            self.tooltip = Text(tooltip)

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="dash-field-label", markup=False)
        yield Static(self.value, classes="dash-field-value", markup=False)

    def set_value(self, value: str | Text) -> None:
        self.query_one(".dash-field-value", Static).update(value)


class InitDashboard(VerticalScroll):
    DEFAULT_CSS = """
    InitDashboard {
        height: 1fr; background: #0b1720; color: #b2c1c9;
        padding: 0 2; scrollbar-size: 1 1;
    }
    InitDashboard #dash-masthead {
        height: 11; border-bottom: solid #526672; margin-bottom: 1;
    }
    InitDashboard #dash-brand { width: 54; height: 10; padding-top: 1; }
    InitDashboard #dash-logo { height: 4; width: 48; }
    InitDashboard #dash-small-logo { display: none; color: #0ac6df; text-style: bold; height: 1; }
    InitDashboard #dash-subtitle { height: 2; color: #7e939e; padding-top: 1; }
    InitDashboard #dash-version { height: 1; color: #7e939e; text-align: center; width: 47; }
    InitDashboard #dash-mascot { width: 36; height: 10; content-align: center middle; }
    InitDashboard #dash-tagline { width: 1fr; height: 10; content-align: right bottom; color: #657d89; padding-bottom: 1; }
    InitDashboard .dash-card { border: round #05bfd8; padding: 0 2; background: #0d1c26; }
    InitDashboard .dash-title { color: #05bfd8; height: 1; margin-bottom: 1; }
    InitDashboard #dash-summary {
        grid-size: 5; grid-columns: 3fr 1fr 2fr 2fr 1fr;
        grid-rows: 3; height: 5; border: round #05bfd8; padding: 0 2; margin-bottom: 1;
    }
    InitDashboard .dash-field { height: 3; padding: 0 1; border-right: solid #30454f; }
    InitDashboard .dash-field:last-child { border-right: none; }
    InitDashboard .dash-field-label { height: 1; color: #05bfd8; text-wrap: nowrap; text-overflow: ellipsis; }
    InitDashboard .dash-field-value { height: 1; margin-top: 1; color: #b2c1c9; text-wrap: nowrap; text-overflow: ellipsis; }
    InitDashboard #dash-work-row { height: 10; margin-bottom: 1; }
    InitDashboard #dash-pipeline { width: 2fr; height: 10; margin-right: 2; }
    InitDashboard #dash-pipeline-body { width: 100%; height: 4; padding-top: 1; }
    InitDashboard #dash-pipeline-note { height: 1; margin-top: 1; color: #657d89; text-wrap: nowrap; text-overflow: ellipsis; }
    InitDashboard #dash-provider { width: 1fr; height: 10; }
    InitDashboard #dash-provider-body { height: auto; }
    InitDashboard #dash-telemetry { height: 7; margin-bottom: 1; }
    InitDashboard #dash-metrics { grid-size: 7; grid-columns: 1fr; grid-rows: 3; height: 3; }
    InitDashboard #dash-events { height: 1fr; min-height: 8; }
    InitDashboard #init-log { height: 1fr; min-height: 4; border: none; padding: 0; margin: 0; background: transparent; }
    InitDashboard #error { height: auto; color: #f27f89; display: none; margin-bottom: 1; }
    InitDashboard #init-progress { height: auto; color: #91a5ae; margin: 1 0 0 0; }
    InitDashboard #dash-actions { height: 1; margin-top: 1; }
    InitDashboard #dash-actions Button { margin-right: 2; min-width: 13; background: #122832; color: #c0d3da; }
    InitDashboard #dash-actions Button:focus { background: #164554; color: #e4faff; text-style: bold; }
    InitDashboard #dash-footnote { height: 1; color: #657d89; margin-top: 1; }
    InitDashboard.compact #dash-tagline { display: none; }
    InitDashboard.compact #dash-brand { width: 1fr; }
    InitDashboard.compact #dash-summary { grid-size: 3; grid-columns: 2fr 1fr 2fr; height: 8; }
    InitDashboard.compact #dash-work-row { layout: vertical; height: 20; }
    InitDashboard.compact #dash-pipeline { width: 100%; margin-right: 0; margin-bottom: 1; }
    InitDashboard.compact #dash-provider { width: 100%; height: 9; }
    InitDashboard.compact #dash-telemetry { height: 10; }
    InitDashboard.compact #dash-metrics { grid-size: 4; height: 6; }
    InitDashboard.narrow { padding: 0 1; }
    InitDashboard.narrow #dash-masthead { height: 5; }
    InitDashboard.narrow #dash-brand { height: 4; padding-top: 0; }
    InitDashboard.narrow #dash-logo, InitDashboard.narrow #dash-mascot { display: none; }
    InitDashboard.narrow #dash-small-logo { display: block; }
    InitDashboard.narrow #dash-subtitle { height: 1; padding-top: 0; }
    InitDashboard.narrow #dash-version { width: auto; text-align: left; }
    InitDashboard.narrow #dash-summary { grid-size: 2; grid-columns: 1fr 1fr; height: 11; }
    InitDashboard.narrow #dash-project { column-span: 2; }
    InitDashboard.narrow #dash-metrics { grid-size: 2; height: 12; }
    InitDashboard.narrow #dash-telemetry { height: 16; }
    InitDashboard.narrow #dash-work-row { height: 24; }
    InitDashboard.narrow #dash-pipeline { height: 14; }
    InitDashboard.narrow #dash-pipeline-body { height: 8; }
    InitDashboard.narrow #dash-actions { height: auto; layout: vertical; }
    InitDashboard.narrow #dash-actions Button { width: 100%; margin-bottom: 1; }
    InitDashboard.short #dash-masthead { height: 8; }
    InitDashboard.short #dash-brand, InitDashboard.short #dash-tagline { height: 7; }
    InitDashboard.short #dash-mascot { display: none; }
    InitDashboard.planning #dash-work-row { height: 16; }
    InitDashboard.planning #dash-metrics { grid-size: 8; }
    InitDashboard.planning #dash-pipeline { height: 16; }
    InitDashboard.planning.compact #dash-work-row { height: 26; }
    InitDashboard.planning.narrow #dash-work-row { height: 30; }
    InitDashboard.planning.narrow #dash-pipeline { height: 20; }
    """

    def __init__(self, project_dir: Path, agents: dict[str, AgentSelection], *, module: str = "init") -> None:
        super().__init__(id="init-dashboard")
        self.project_dir = project_dir
        self.agents = agents
        self.module = module
        self.set_class(module in ("plan","build"), "planning")
        self.stages = BUILD_STAGES if module == "build" else PLAN_STAGES if module == "plan" else STAGES
        self.pipeline_labels = PLAN_PIPELINE if module == "plan" else PIPELINE
        self.event = InitEvent("starting", "starting", 0, None, None, 0, 0, 0, 0, 0, 0)
        self.run_status = "ready"
        self.started_at = monotonic()
        self.duration = 0.0

    def compose(self) -> ComposeResult:
        try:
            release = version("capivara-harness")
        except PackageNotFoundError:
            release = "dev"
        with Horizontal(id="dash-masthead"):
            with Vertical(id="dash-brand"):
                yield Static(wordmark(), id="dash-logo", name="CAPIVARA")
                yield Static("CAPIVARA", id="dash-small-logo")
                yield Static("BUILD · CÓDIGO · GATES DE EXECUÇÃO" if self.module=="build" else f"{self.module.upper()} · DOCUMENTAÇÃO · CONTROLE DE EXECUÇÃO", id="dash-subtitle")
                yield Static(f"v{release}", id="dash-version")
            yield Mascot(id="dash-mascot", name="Capivara construtora" if self.module=="build" else "Capivara documentadora")
            yield Static("CAPIVARA · capivara construtora" if self.module=="build" else "CAPIVARA · capivara documentadora", id="dash-tagline")
        with Grid(id="dash-summary"):
            yield DashboardField("PROJETO", f"▱ {self.project_dir}", id="dash-project", tooltip=str(self.project_dir))
            yield DashboardField("MÓDULO", self.module, id="dash-workflow")
            yield DashboardField("ETAPA ATUAL", "Preparação", id="dash-stage")
            yield DashboardField("STATUS", "pronto", id="dash-status")
            yield DashboardField("DURAÇÃO", "0m 00s", id="dash-duration")
        with Horizontal(id="dash-work-row"):
            with Vertical(id="dash-pipeline", classes="dash-card"):
                yield Static("BUILD · GATES POR TASK" if self.module == "build" else "PLAN · CRIAÇÃO (C) E VALIDAÇÃO (V)" if self.module == "plan" else "PIPELINE · FLUXO DO INIT", classes="dash-title")
                if self.module == "build":
                    yield BuildProgress()
                elif self.module == "plan":
                    yield PlanProgress()
                else:
                    yield Static("", id="dash-pipeline-body")
                    yield Static("Pedido → entrevista → prompt → aprovação do revisor", id="dash-pipeline-note")
            with Vertical(id="dash-provider", classes="dash-card"):
                yield Static("PROVEDOR ATUAL", classes="dash-title")
                yield Static("", id="dash-provider-body")
        with Vertical(id="dash-telemetry", classes="dash-card"):
            yield Static(f"TELEMETRIA · {self.module.upper()}", classes="dash-title")
            with Grid(id="dash-metrics"):
                metrics = (("FASES","phases"),("TASKS","tasks"),("DEPENDÊNCIAS","packages"),("AGENTES CLI","agents"),
                           ("OPERAÇÕES","operations"),("INVOCAÇÕES","invocations"),("DEVOLUÇÕES","corrections"),("TOKENS","tokens")) if self.module == "build" else (("FASES", "phases"), ("TASKS", "tasks"), ("RODADA", "round"),
                           ("OPERAÇÕES", "operations"), ("INVOCAÇÕES", "invocations"),
                           ("CORREÇÕES", "corrections"), ("CONTRATOS", "contract_errors"), ("TOKENS", "tokens")) if self.module == "plan" else (
                                   ("ENTREVISTA", "questions"), ("RODADA", "round"), ("OPERAÇÕES", "operations"),
                                   ("INVOCAÇÕES", "invocations"), ("CORREÇÕES", "corrections"),
                                   ("INFERÊNCIAS", "inferences"), ("TOKENS", "tokens"))
                for label, key in metrics:
                    hint = {
                        "packages": "Dependências verificadas / dependências necessárias.",
                        "agents": "Processos CLI ativos / máximo configurado; o scheduler limita também os workers de tasks.",
                        "phases": "Fases aprovadas / fases da origem, incluindo a fase 0.",
                        "tasks": "Tasks aprovadas / tasks identificadas até o momento; o total aumenta durante a divisão.",
                        "questions": "Respostas registradas / perguntas preparadas, somando as rodadas.",
                        "round": "Rodada atual de elaboração e revisão documental.",
                        "operations": "Pedidos lógicos aos agentes durante este módulo.",
                        "invocations": "Tentativas de chamada ao CLI, incluindo novas chamadas para corrigir formato.",
                        "corrections": ("Devoluções documentais; erros de formato/contrato têm contador separado." if self.module == "plan"
                                        else "Devoluções para ajustar o documento ou o contrato da resposta."),
                        "contract_errors": "Respostas que violaram o contrato; uma oportunidade de corrigir formato, sem repetição indefinida.",
                        "inferences": "Decisões internas registradas pelo escritor, sem entrevista.",
                        "tokens": "O consumo de tokens dos CLIs ainda não é medido pelo harness.",
                    }[key]
                    yield DashboardField(label, "não medido" if key == "tokens" else "0", id=f"metric-{key}", tooltip=hint)
        with Vertical(id="dash-events", classes="dash-card"):
            yield Static("EVENTOS RECENTES", classes="dash-title")
            yield RichLog(wrap=True, highlight=False, markup=False, max_lines=200, id="init-log")
        yield Static("", id="error", markup=False)
        yield Static("A aplicação será entregue na raiz; estado e evidências ficam em .harness." if self.module=="build" else "Os agentes geram documentação; os artefatos ficam em .harness.", id="init-progress", markup=False)
        with Horizontal(id="dash-actions"):
            yield Button("Interromper · Esc", id="back", compact=True)
            for label, key in (("Ver relatório" if self.module == "build" else "Ver plano final" if self.module == "plan" else "Ver prompt final", "init-view"),
                               ("Tentar novamente", "init-retry"), ("Fechar", "init-close")):
                button = Button(label, id=key, compact=True)
                button.display = False
                yield button
        yield Static("Esc interrompe / volta · Ctrl+Q sai · metadados operacionais, sem conteúdo dos agentes", id="dash-footnote")

    def on_mount(self) -> None:
        self.responsive()
        self.call_after_refresh(self.render_data)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget.id in ('back','init-view','init-retry','init-close'):
            self.call_after_refresh(event.widget.scroll_visible, animate=False, immediate=True, force=True)

    def on_resize(self, event: events.Resize) -> None:
        self.responsive()
        if self.is_mounted:
            self.call_after_refresh(self.render_data)

    def responsive(self) -> None:
        self.set_class(self.size.width < 120, "compact")
        self.set_class(self.size.width < 76, "narrow")
        self.set_class(self.size.width >= 76 and self.app.size.height < 40, "short")

    def start(self) -> None:
        self.event = InitEvent("starting", "starting", 0, None, None, 0, 0, 0, 0, 0, 0)
        self.run_status = "running"
        self.started_at = monotonic()
        self.duration = 0
        self.query_one(RichLog).clear()
        self.render_data()
        self.log(f"workflow · {self.module} · iniciado")

    def tick(self) -> None:
        if self.run_status == "running":
            self.duration = monotonic() - self.started_at
        seconds = int(self.duration)
        self.query_one("#dash-duration", DashboardField).set_value(f"◷ {seconds // 60}m {seconds % 60:02d}s")

    def log(self, message: str, color: str = MUTED) -> None:
        entry = Text("› ", style=MUTED)
        entry.append(f"[{datetime.now().strftime('%H:%M:%S')}]  ", style=CYAN)
        entry.append(message, style=color)
        self.query_one(RichLog).write(entry)

    def update_event(self, event: InitEvent) -> None:
        self.event = event
        self.render_data()
        role = "revisor" if event.last_role == "reviewer" else "escritor"
        if event.kind == "stage_changed":
            label = self.stages.get(event.stage, (0, event.stage))[1]
            if event.stage == "completed":
                label = "Revisão aprovada · salvando plano final" if self.module == "plan" else "Revisão aprovada · salvando prompt final"
            if self.module == "plan" and event.phase_number is not None:
                label += f" · fase {event.phase_number}"
            suffix = (f" · {event.task_id}" if event.task_id else "") if self.module=="build" else (f" · rodada {event.round}" if event.round else "")
            self.log(f"etapa · {label.lower()}" + suffix, CYAN)
        elif event.kind == "agent_started":
            selection = self.agents.get(event.active_role)
            provider = PROVIDERS.get(selection.provider, selection.provider) if selection else "CLI"
            self.log(f"{role} · {provider} · chamada {event.invocations} iniciada", CYAN)
        elif event.kind == "agent_finished":
            self.log(f"{role} · chamada {event.invocations} concluída")
        elif event.kind == "format_correction":
            self.log(f"{role} · resposta devolvida para correção do contrato", YELLOW)
        elif event.kind == "writer_recovered":
            self.log("escritor · patch autorizado aplicado exatamente após desvio da resposta", YELLOW)
        elif event.kind == "interview_prepared":
            self.log(f"entrevista · {event.questions} pergunta(s) no total")
        elif event.kind == "answer_saved":
            self.log(f"entrevista · resposta {event.answers}/{event.questions} registrada", GREEN)
        elif event.kind in ("agent_failed", "agent_cancelled"):
            self.log(f"{role} · chamada {'interrompida' if event.kind == 'agent_cancelled' else 'falhou'}", YELLOW)

    def finish(self, status: str) -> None:
        self.tick()
        self.run_status = status
        self.render_data()
        label, color = STATUS[status]
        self.log(f"workflow · {self.module} · {label}", color)

    def render_data(self) -> None:
        if not self.query("#dash-stage"):
            return
        label, color = STATUS[self.run_status]
        if self.module=='build' and self.run_status=='completed' and self.event.stage=='delivery_pending':label='concluído · instalação pendente'
        self.query_one("#dash-status", DashboardField).set_value(Text(f"● {label}", style=color))
        stage_label = "Instalação pendente" if self.module=='build' and self.event.stage=='delivery_pending' else "Concluído" if self.run_status == "completed" else self.stages.get(self.event.stage, (0, self.event.stage))[1]
        if self.module == "plan" and self.event.phase_number is not None and self.run_status != "completed":
            stage_label += f" · fase {self.event.phase_number}"
        self.query_one("#dash-stage", DashboardField).set_value(Text(f"▶ {stage_label}", style=CYAN))
        values = (("phases",f"{self.event.phases_approved}/{self.event.phases_total} concluídas"),
                  ("tasks",f"{self.event.tasks_approved}/{self.event.tasks_total} concluídas"),
                  ("packages",f"{self.event.packages_ready}/{self.event.packages_total} prontas"),
                  ("agents",f"{self.event.active_agents}/{self.event.max_agents}")) if self.module == "build" else (("phases", f"{self.event.phases_approved}/{self.event.phases_total} aprovadas"),
                  ("tasks", f"{self.event.tasks_approved}/{self.event.tasks_total} aprovadas")) if self.module == "plan" else (
                  ("questions", f"{self.event.answers}/{self.event.questions} respostas"),
                  ("inferences", str(self.event.inferences)))
        for key, value in (*values,
                           *(((("round", str(self.event.round)),) if self.module != "build" else ())), ("operations", str(self.event.operations)),
                           ("invocations", str(self.event.invocations)), ("corrections", str(self.event.corrections))):
            self.query_one(f"#metric-{key}", DashboardField).set_value(value)
        if self.module == "plan":
            self.query_one("#metric-contract_errors", DashboardField).set_value(str(self.event.contract_errors))
        self.query_one("#dash-provider-body", Static).update(self.provider())
        if self.module == "build":
            self.query_one(BuildProgress).update_event(self.event,self.run_status=="running")
        elif self.module == "plan":
            self.query_one(PlanProgress).update_items(self.event.items)
        else:
            self.query_one("#dash-pipeline-body", Static).update(self.pipeline())
        self.tick()

    def provider(self) -> Text:
        if self.module=="build" and self.event.active_agents>1:
            result=Text(f"{self.event.active_agents} agentes CLI em atividade\n",style=CYAN)
            for role,label,count in (("writer","escritor",self.event.active_writers),("reviewer","revisor",self.event.active_reviewers)):
                if not count:continue
                selection=self.agents[role]
                result.append(f"\n{label} ×{count} · {PROVIDERS[selection.provider]}\n",style=CYAN)
                result.append(f"{selection.model} / {selection.effort}",style=BODY)
            return result
        role = self.event.active_role or self.event.last_role
        if role is None:
            return Text("Aguardando a primeira chamada.\nAs chamadas aparecerão neste painel.", style=MUTED)
        selection = self.agents[role]
        mode = "em atividade" if self.event.active_role else "aguardando"
        if self.run_status == "completed":
            mode = "concluído"
        elif self.run_status in ("cancelled", "failed"):
            mode = STATUS[self.run_status][0]
        data = (("papel", f"{'revisor' if role == 'reviewer' else 'escritor'} · {mode}"),
                ("provedor", PROVIDERS[selection.provider]), ("modelo", "padrão do CLI" if selection.model == "default" else selection.model),
                ("effort", "padrão" if selection.effort == "default" else selection.effort), ("transporte", "CLI"))
        result = Text()
        for index, (label, value) in enumerate(data):
            if index:
                result.append("\n")
            result.append(f"{label:<11}", style=CYAN)
            result.append(value, style=BODY)
        return result

    def pipeline(self) -> Text:
        width = max(30, self.query_one("#dash-pipeline-body", Static).size.width)
        current = self.stages.get(self.event.stage, (0, ""))[0]
        groups = (range(3), range(3, 6)) if width < 64 else (range(6),)
        result = Text()
        for group_index, group in enumerate(groups):
            if group_index:
                result.append("\n\n")
            cell = max(6, width // len(group))
            nodes = []
            for index in group:
                done = self.run_status == "completed" or index < current
                active = index == current and not done
                caption, symbol, color = ("concluído", "☑", GREEN) if done else ("aguardando", "□", MUTED)
                if active:
                    caption, symbol, color = "em curso", "●", YELLOW
                    if self.run_status in ("failed", "cancelled"):
                        caption, color = STATUS[self.run_status]
                if self.module == "init" and index == 2 and done and not self.event.questions:
                    caption = "inferida" if self.event.inferences else "dispensada"
                nodes.append((index, caption, symbol, color))
            result.append(" " * (cell // 2))
            for offset, (_, _, symbol, color) in enumerate(nodes):
                result.append(symbol, style=color)
                if offset + 1 < len(nodes):
                    result.append("─" * (cell - 1), style="#3a535d")
            for row in ("label", "caption"):
                result.append("\n")
                for index, caption, _, color in nodes:
                    value = self.pipeline_labels[index] if row == "label" else caption
                    value = value[:cell - 1]
                    result.append(value.center(cell), style=color if row == "label" else MUTED)
        return result
