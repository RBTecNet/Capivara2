"""TUI persistente de INIT, PLAN e BUILD."""

import asyncio
import copy
import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Literal

from PIL import Image as PillowImage
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
    Switch,
    TextArea,
)
from textual_image.widget import Image

from capivara2ptn.dialogs import (
    ContinueDialog,
    InterviewDialog,
    ModelIdDialog,
    ResultDialog,
    RestartStageDialog,
)
from capivara2ptn.models import PROVIDERS, discover_models
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.init_flow import InitCancelled, InitError, Question, RunFiles, run_init
from capivara2ptn.prompt_files import PromptError, install_defaults, prompt_directory
from capivara2ptn.providers import EFFORTS, CliRunner, ProviderError, Selection
from capivara2ptn.project_config import AuditorConfig, ExecutionOptions, ProjectConfigError, read_auditor, read_options, save_auditor, save_options
from capivara2ptn.module_clock import ModuleClock, display_time
from capivara2ptn.init_progress import InitProgress
from capivara2ptn.plan_flow import PlanStore, run_plan
from capivara2ptn.plan_markdown import PlanFormatError
from capivara2ptn.plan_panel import PlanPanel
from capivara2ptn.plan_state import PlanError, counts, load_plan_state, read_init_prd, record_init_completion
from capivara2ptn.workspace import WorkspaceError, data_directory, reset_stage
from capivara2ptn.build_flow import BuildError, BuildStore, read_build_input, run_build

LogKind = Literal["sistema", "executor", "sucesso", "erro"]
MANUAL_MODEL = object()  # Ação da interface; nunca é um ID de modelo.


class CapivaraApp(App[None]):
    TITLE = "CAPIVARA"
    SUB_TITLE = "CAPIVARA2PTN • Etapa 1"
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True),
        Binding("ctrl+c", "cancel_build", "Interromper BUILD", priority=True),
        Binding("f2", "preview_interview", "Prévia INIT"),
        Binding("f3", "preview_result(True)", "Sucesso"),
        Binding("f4", "preview_result(False)", "Falha"),
        Binding("f5", "preview_continue", "INIT → PLAN"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.theme = "textual-dark"
        self.working_directory = Path.cwd()
        self.model_id: str | None = None
        self._provider: str | None = None
        self._model_options: list[tuple[str, str]] = []
        self._allow_manual_model = False
        self.init_completed = False
        self._init_running = False
        self._init_header = "● Ociosa"
        self._init_state_text = "Estado: Ociosa"
        self._init_stage_text = "Aguardando início do INIT."
        self._plan_running = False
        self._plan_state: dict | None = None
        self._build_state: dict | None = None
        self._build_running = False
        self._build_worker = None
        self._init_saved = False
        self._restart_requested: str | None = None
        self._workspace_error: str | None = None
        self._options = ExecutionOptions()
        self._options_ready = False
        self._auditor_config = AuditorConfig()
        self._auditor_ready = False
        self._auditor_models: list[tuple[str, str]] = []
        self._auditor_manual = False
        self._clock: ModuleClock | None = None
        self._clock_error_reported = False
        # Recursos vêm da instalação; nunca dependem do cwd do usuário.
        with files("capivara2ptn").joinpath("assets/mascote.png").open("rb") as stream:
            with PillowImage.open(stream) as image:
                self.mascot = image.convert("RGBA")

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Image(self.mascot, id="mascot")
            with Vertical(id="brand"):
                yield Static("CAPIVARA", id="title")
                yield Static("CAPIVARA2PTN · Fundação e interface", classes="muted")
            yield Static("● Ociosa", id="status")

        yield Static(f"Diretório de trabalho: {self.working_directory}", id="workspace", markup=False)
        with VerticalScroll(id="body"):
            with Horizontal(id="dashboard"):
                with Vertical(id="configuration", classes="card"):
                    yield Label("Módulo", classes="field-label")
                    with RadioSet(id="module"):
                        yield RadioButton("INIT", value=True, id="module-init")
                        yield RadioButton("PLAN", id="module-plan")
                        yield RadioButton("BUILD", disabled=True, id="module-build")
                    yield Label("Provedor · escritor", classes="field-label")
                    yield Select(
                        PROVIDERS,
                        value="codex", allow_blank=False, id="provider",
                    )
                    yield Label("Modelo · escritor", id="model-label", classes="field-label")
                    yield Select(
                        [], prompt="Consultando modelos…", disabled=True, id="model",
                    )
                    yield Label("Effort · escritor", classes="field-label")
                    yield Select(
                        [(label, label) for label in EFFORTS],
                        value="Default", allow_blank=False, id="effort",
                    )
                    yield Label("Provedor · auditor", classes="field-label")
                    yield Select([("Mesmo do escritor", "writer"), *PROVIDERS], value="writer", allow_blank=False, id="auditor-provider")
                    with Horizontal(id="auditor-details"):
                        with Vertical(id="auditor-model-field"):
                            yield Label("Modelo · auditor", id="auditor-model-label", classes="field-label")
                            yield Select([], prompt="Selecione um modelo", id="auditor-model")
                        with Vertical(id="auditor-effort-field"):
                            yield Label("Effort", classes="field-label")
                            yield Select([(level, level) for level in EFFORTS], value="Default", allow_blank=False, id="auditor-effort")
                    with Horizontal(classes="option-row"):
                        yield Switch(value=True, id="gates-enabled", classes="execution-option")
                        yield Label("Habilitar gates", classes="option-label")
                    with Horizontal(classes="option-row"):
                        yield Switch(value=False, id="forbid-tests", classes="execution-option")
                        yield Label("Proibir testes automatizados", classes="option-label")
                    yield PromptSource(self.working_directory)
                    yield Static("INIT · entrevista, PRD e G0", classes="muted", id="scope")
                    yield Button("Iniciar", variant="primary", id="start")

                with Vertical(id="monitor"):
                    with Vertical(id="progress-card", classes="card"):
                        with Horizontal(id="progress-heading"):
                            yield Static("Módulo: INIT", id="progress-module")
                            yield Static("Tempo: 00:00:00", id="elapsed")
                        yield Static("Estado: Ociosa", id="progress-state")
                        yield InitProgress(id="progress")
                        with Vertical(id="plan-progress-area"):
                            yield ProgressBar(total=1, show_percentage=False, show_eta=False, id="plan-progress")
                            yield Static("Nenhum planejamento iniciado.", id="plan-count")
                        yield Static("Aguardando início do INIT.", id="stage", classes="muted", markup=False)
                    yield PlanPanel(id="plan-panel", classes="card")
                    with Vertical(id="log-card", classes="card"):
                        yield Static(
                            "[cyan]Sistema[/]  [magenta]Executor[/]  [green]Sucesso[/]  [red]Erro[/]",
                            id="log-legend",
                        )
                        yield RichLog(id="log", min_width=1, wrap=True, highlight=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#configuration").border_title = "Configuração"
        self.query_one("#progress-card").border_title = "Progresso"
        self.query_one("#log-card").border_title = "Log"
        self.query_one("#log-card").border_subtitle = "Saída da CLI"
        self.query_one("#prompt-path").tooltip = "Caminhos relativos são interpretados a partir do diretório de trabalho."
        self.query_one("#start").tooltip = "Executar o módulo selecionado no diretório de trabalho. Ctrl+C interrompe o BUILD."
        self.set_class(self.size.width < 100, "narrow")
        self.write_log("sistema", "Aplicação pronta. Interface disponível; nenhuma execução iniciada.")
        try:
            self.show_options(read_options(self.working_directory))
        except (ProjectConfigError, WorkspaceError) as error:
            self.write_log("erro", str(error))
            if isinstance(error, WorkspaceError):
                self._workspace_error = str(error)
        self._options_ready = True
        try:
            self.show_auditor(read_auditor(self.working_directory))
        except (ProjectConfigError, WorkspaceError) as error:
            self.write_log("erro", str(error))
            self.show_auditor(AuditorConfig())
        self._auditor_ready = True
        try:
            self._clock = ModuleClock(self.working_directory)
        except (ProjectConfigError, WorkspaceError) as error:
            self.write_log("erro", str(error))
        try:
            self._plan_state = load_plan_state(self.working_directory)
        except (PlanError, WorkspaceError) as error:
            self.write_log("erro", str(error))
        self.update_plan_panel(self._plan_state, active=False)
        try:
            self._build_state = load_plan_state(self.working_directory, module="BUILD")
        except (PlanError, WorkspaceError) as error:
            self.write_log("erro", str(error))
        try:
            install_defaults()
        except PromptError as error:
            self.write_log("erro", str(error))
        self.query_one("#module", RadioSet).focus()
        self.call_after_refresh(self.query_one("#configuration").scroll_home, animate=False)
        self.select_provider(str(self.query_one("#provider", Select).value))
        self.restore_project_phase()
        self.set_interval(1, self.update_elapsed)

    def update_elapsed(self, *, checkpoint: bool = True) -> None:
        button = self.query_one("#module", RadioSet).pressed_button
        if button is None:
            return
        module = button.id.removeprefix("module-").upper()
        clock = self._clock
        recorded = clock and (module in clock.totals or clock.active == module)
        value = display_time(clock.elapsed(module)) if clock and (recorded or not self.module_completed(module.lower())) else "--:--:--"
        label = self.query_one("#elapsed", Static)
        label.update("Tempo: " + value)
        label.tooltip = "Duração acumulada do módulo, sem o período interrompido." if value != "--:--:--" else "Duração não registrada para esta execução anterior."
        if checkpoint and clock and clock.active:
            try:
                clock.checkpoint()
                self._clock_error_reported = False
            except (ProjectConfigError, WorkspaceError) as error:
                if not self._clock_error_reported:
                    self.write_log("erro", str(error))
                self._clock_error_reported = True

    def start_clock(self, module: str) -> None:
        if self._clock is None:
            self._clock = ModuleClock(self.working_directory)
        self._clock.start(module)
        self.update_elapsed()

    def stop_clock(self, module: str) -> None:
        if self._clock:
            try:
                self._clock.stop(module)
            except (ProjectConfigError, WorkspaceError) as error:
                if self.is_running:
                    self.write_log("erro", str(error))
        if self.is_running:
            self.update_elapsed(checkpoint=False)

    def reset_clock(self, module: str) -> None:
        if self._clock is None:
            self._clock = ModuleClock(self.working_directory)
        self._clock.reset_from(module)
        self.update_elapsed(checkpoint=False)

    def execution_runners(self, selection: Selection, options: ExecutionOptions) -> tuple[CliRunner, CliRunner | None]:
        secrets = [server.token for server in self.query_one(PromptSource).servers]
        writer = CliRunner(selection, self.working_directory, lambda text: self.write_log("executor", text), secrets,
                           forbid_automated_tests=options.forbid_automated_tests)
        if not options.gates_enabled:
            return writer, None
        config = read_auditor(self.working_directory)
        effective = config.resolve(selection)
        self.write_log("sistema", f"Auditor: {effective.provider}, modelo {effective.model}, effort {effective.effort}." + (" Mesmo do escritor." if config.provider == "writer" else ""))
        if config.provider == "writer":
            return writer, writer
        return writer, CliRunner(effective, self.working_directory, lambda text: self.write_log("executor", "Auditor · " + text), secrets,
                                 forbid_automated_tests=options.forbid_automated_tests)

    def plan_completed(self) -> bool:
        if not self._plan_state or self._plan_state["status"] != "done":
            return False
        done, total, _, _ = counts(self._plan_state)
        return total > 0 and done == total

    def module_completed(self, module: str) -> bool:
        if module == "build":
            return bool(self._build_state and self._build_state["status"] == "done")
        return self._init_saved if module == "init" else (self.plan_completed() if module == "plan" else False)

    def restore_project_phase(self) -> None:
        try:
            read_init_prd(self.working_directory)
            self._init_saved = self.init_completed = True
            receipt = data_directory(self.working_directory) / "init-concluido.json"
            gate = json.loads(receipt.read_text()).get("gates_enabled", True) if receipt.exists() else (data_directory(self.working_directory) / "g0-init.txt").exists()
            self.query_one(InitProgress).reset(gate)
            self.query_one(InitProgress).set_stage("Concluído")
            self._init_header = "● Concluído"
            self._init_state_text = "Estado: Concluído"
            self._init_stage_text = "PRD de INIT concluído disponível em .harness/dados/prompt-final.md."
            original = data_directory(self.working_directory) / "prompt-original.txt"
            manual = self.query_one("#manual-prompt", TextArea)
            if original.is_file() and not manual.text:
                manual.load_text(original.read_text(encoding="utf-8"))
        except (PlanError, WorkspaceError, OSError, ValueError):
            pass
        build = self.query_one("#module-build", RadioButton)
        build.disabled = not self.plan_completed()
        target = "build" if self.plan_completed() else ("plan" if self._init_saved else "init")
        button = self.query_one(f"#module-{target}", RadioButton)
        button.value = True
        self.apply_module_view(button)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 100, "narrow")
        if self.query("#monitor"):
            self.call_after_refresh(self.fit_plan_monitor)

    def fit_plan_monitor(self) -> None:
        monitor = self.query_one("#monitor")
        if self.has_class("planning") and self.size.width < 100:
            monitor.styles.height = max(18, self.query_one("#body").content_size.height)
        else:
            monitor.styles.height = None

    def show_options(self, options: ExecutionOptions) -> None:
        self._options = options
        selected = self.query_one("#module", RadioSet).pressed_button
        if selected and selected.id == "module-build" and self._build_state is None:
            self.update_build_panel(None)
        with self.prevent(Switch.Changed):
            self.query_one("#gates-enabled", Switch).value = options.gates_enabled
            self.query_one("#forbid-tests", Switch).value = options.forbid_automated_tests

    @on(Switch.Changed, ".execution-option")
    def options_changed(self) -> None:
        if not self._options_ready:
            return
        options = ExecutionOptions(
            self.query_one("#gates-enabled", Switch).value,
            self.query_one("#forbid-tests", Switch).value,
        )
        if options == self._options:
            return
        try:
            save_options(self.working_directory, options)
        except (ProjectConfigError, WorkspaceError) as error:
            self.show_options(self._options)
            self.write_log("erro", str(error))
            self.notify(str(error), severity="error")
            return
        self.show_options(options)

    @on(Select.Changed, "#provider")
    def provider_changed(self, event: Select.Changed) -> None:
        self.select_provider(str(event.value))

    def select_provider(self, provider: str) -> None:
        if provider == self._provider:
            return
        self._provider = provider
        self.model_id = None
        self._model_options = []
        self._allow_manual_model = False
        model = self.query_one("#model", Select)
        model.expanded = False
        model.disabled = True
        with self.prevent(Select.Changed):
            model.set_options([])
        model.prompt = "Consultando modelos…"
        model.tooltip = "Consultando os modelos do provedor selecionado."
        label = self.query_one("#model-label", Label)
        label.update("Modelo · escritor · consultando…")
        label.tooltip = None
        self.load_models(provider)

    @work(group="models", exclusive=True, exit_on_error=False)
    async def load_models(self, provider: str) -> None:
        catalog = await discover_models(provider, self.working_directory)
        if provider != self._provider:
            return
        self._model_options = catalog.models
        self._allow_manual_model = catalog.manual
        model = self.query_one("#model", Select)
        with self.prevent(Select.Changed):
            model.set_options(self.model_choices())
        model.prompt = "Selecione um modelo" if catalog.models else "Sem modelos · informar ID…"
        model.tooltip = catalog.message
        model.disabled = False
        label = self.query_one("#model-label", Label)
        label.update("Modelo · escritor" if catalog.models else "Modelo · escritor · nenhum encontrado")
        label.tooltip = catalog.message
        self.write_log("sistema", catalog.message)

    def model_choices(self, *, auditor: bool = False) -> list:
        options = list(self._auditor_models if auditor else self._model_options)
        manual = self._auditor_manual if auditor else self._allow_manual_model
        if manual:
            options.append(("Informar ID manualmente…", MANUAL_MODEL))
        return options

    @on(Select.Changed, "#model")
    def model_changed(self, event: Select.Changed) -> None:
        if event.value is MANUAL_MODEL:
            # Restaurar a seleção antes do diálogo evita deixar a ação como ID.
            with self.prevent(Select.Changed):
                event.select.value = self.model_id if self.model_id is not None else Select.BLANK
            provider = self._provider
            self.push_screen(ModelIdDialog(), lambda value: self.manual_model_selected(provider, value))
        else:
            self.model_id = event.value if isinstance(event.value, str) else None

    def manual_model_selected(self, provider: str | None, identifier: str | None) -> None:
        if provider != self._provider or identifier is None:
            return
        if not any(value == identifier for _, value in self._model_options):
            self._model_options.append((identifier, identifier))
        model = self.query_one("#model", Select)
        with self.prevent(Select.Changed):
            model.set_options(self.model_choices())
            model.value = identifier
        self.model_id = identifier
        self.query_one("#model-label", Label).update("Modelo · escritor · ID informado")

    def show_auditor(self, config: AuditorConfig) -> None:
        self._auditor_config = config
        independent = config.provider != "writer"
        self.query_one("#auditor-details").display = independent
        model = self.query_one("#auditor-model", Select)
        with self.prevent(Select.Changed):
            self.query_one("#auditor-provider", Select).value = config.provider
            self.query_one("#auditor-effort", Select).value = config.effort
            model.expanded = False
            model.set_options([(config.model, config.model)] if config.model else [])
            model.value = config.model or Select.BLANK
        model.disabled = independent
        if independent:
            model.prompt = "Consultando modelos…"
            self.load_auditor_models(config.provider)
        else:
            for worker in list(self.workers):
                if worker.group == "auditor-models":
                    worker.cancel()

    def persist_auditor(self, config: AuditorConfig) -> bool:
        try:
            save_auditor(self.working_directory, config)
        except (ProjectConfigError, WorkspaceError) as error:
            self.show_auditor(self._auditor_config)
            self.write_log("erro", str(error))
            self.notify(str(error), severity="error")
            return False
        self._auditor_config = config
        return True

    @on(Select.Changed, "#auditor-provider")
    def auditor_provider_changed(self, event: Select.Changed) -> None:
        if not self._auditor_ready or event.value != event.select.value or event.value == self._auditor_config.provider:
            return
        config = replace(self._auditor_config, provider=str(event.value), model=None)
        if self.persist_auditor(config):
            self.show_auditor(config)

    @work(group="auditor-models", exclusive=True, exit_on_error=False)
    async def load_auditor_models(self, provider: str) -> None:
        catalog = await discover_models(provider, self.working_directory)
        if provider != self._auditor_config.provider:
            return
        self._auditor_models = list(catalog.models)
        self._auditor_manual = catalog.manual
        saved = self._auditor_config.model
        if saved and not any(value == saved for _, value in self._auditor_models):
            self._auditor_models.append((saved, saved))
        model = self.query_one("#auditor-model", Select)
        with self.prevent(Select.Changed):
            model.set_options(self.model_choices(auditor=True))
            model.value = saved or Select.BLANK
        model.prompt = "Selecione um modelo" if catalog.models else "Informar ID…"
        model.tooltip = catalog.message
        model.disabled = False
        self.write_log("sistema", "Auditor · " + catalog.message)

    @on(Select.Changed, "#auditor-model")
    def auditor_model_changed(self, event: Select.Changed) -> None:
        if not self._auditor_ready or event.value != event.select.value:
            return
        if event.value is MANUAL_MODEL:
            with self.prevent(Select.Changed):
                event.select.value = self._auditor_config.model or Select.BLANK
            provider = self._auditor_config.provider
            self.push_screen(ModelIdDialog(), lambda value: self.auditor_manual_selected(provider, value))
        elif self._auditor_config.provider != "writer":
            identifier = event.value if isinstance(event.value, str) else None
            if identifier != self._auditor_config.model:
                self.persist_auditor(replace(self._auditor_config, model=identifier))

    def auditor_manual_selected(self, provider: str, identifier: str | None) -> None:
        if identifier is None or provider != self._auditor_config.provider:
            return
        if not self.persist_auditor(replace(self._auditor_config, model=identifier)):
            return
        if not any(value == identifier for _, value in self._auditor_models):
            self._auditor_models.append((identifier, identifier))
        with self.prevent(Select.Changed):
            model = self.query_one("#auditor-model", Select)
            model.set_options(self.model_choices(auditor=True))
            model.value = identifier

    @on(Select.Changed, "#auditor-effort")
    def auditor_effort_changed(self, event: Select.Changed) -> None:
        if self._auditor_ready and event.value == event.select.value and event.value != self._auditor_config.effort:
            self.persist_auditor(replace(self._auditor_config, effort=str(event.value)))

    def write_log(self, kind: LogKind, message: str) -> None:
        """Saída textual apresentada sem interpretar markup vindo da CLI."""
        colors = {"sistema": "cyan", "executor": "magenta", "sucesso": "green", "erro": "red"}
        line = Text(f"[{kind.upper()}] ", style=colors[kind])
        line.append(message, style="default")
        self.query_one("#log", RichLog).write(line)

    @on(RadioSet.Changed, "#module")
    def module_changed(self, event: RadioSet.Changed) -> None:
        self.apply_module_view(event.pressed)
        module = event.pressed.id.removeprefix("module-")
        if self._restart_requested != module:
            self._restart_requested = None
        if not self._init_running and not self._plan_running and not self._build_running and module != "build" and self.module_completed(module):
            self.push_screen(RestartStageDialog(module), lambda accepted: self.restart_decision(module, accepted))

    def restart_decision(self, module: str, accepted: bool, start: bool = False) -> None:
        selected = self.query_one("#module", RadioSet).pressed_button
        if selected is None or selected.id != f"module-{module}":
            return
        self._restart_requested = module if accepted else None
        if accepted:
            self.query_one("#stage", Static).update(f"Reexecução de {module.upper()} confirmada. Os resultados serão excluídos ao pressionar Iniciar.")
            if start:
                self.start_pressed()

    def apply_module_view(self, button: RadioButton) -> None:
        planning = button.id in ("module-plan", "module-build")
        self.set_class(planning, "planning")
        self.query_one("#progress-module", Static).update(f"Módulo: {button.label}")
        self.query_one(PromptSource).display = button.id == "module-init"
        self.query_one(InitProgress).display = button.id == "module-init"
        module = button.id.removeprefix("module-")
        self.update_elapsed(checkpoint=False)
        self.query_one("#scope", Static).update({"init": "INIT · entrevista, PRD e G0", "plan": "PLAN · fases e threads", "build": "BUILD · uma task por vez · Ctrl+C interrompe"}[module])
        start = self.query_one("#start", Button)
        start.disabled = (module == "build" and not self.plan_completed()) or bool(self._workspace_error)
        start.label = f"Refazer {module.upper()}" if self.module_completed(module) else "Iniciar"
        self.call_after_refresh(self.fit_plan_monitor)
        if module == "build" and not self._build_running:
            self.update_build_panel(self._build_state)
            self.show_build_status()
        elif module == "plan" and not self._init_running and not self._plan_running:
            self.update_plan_panel(self._plan_state)
            self.show_plan_status()
        elif button.id == "module-init" and not self._init_running and not self._plan_running:
            self.query_one("#status", Static).update(self._init_header)
            self.query_one("#progress-state", Static).update(self._init_state_text)
            self.query_one("#stage", Static).update(self._init_stage_text)

    def update_plan_panel(self, state: dict | None, active: bool = False) -> None:
        self._plan_state = state
        self.query_one("#module-build", RadioButton).disabled = not self.plan_completed()
        self.display_execution_panel(state, active)

    def display_execution_panel(self, state: dict | None, active: bool = False) -> None:
        self.query_one(PlanPanel).show(state, active)
        done, total, phases_done, phases_total = counts(state) if state else (0, 0, 0, 0)
        self.query_one("#plan-progress", ProgressBar).update(total=max(1, total), progress=done)
        text = f"Threads: {done}/{total} · Fases: {phases_done}/{phases_total}" if total else "Aguardando definição das threads."
        self.query_one("#plan-count", Static).update(text)

    def update_build_panel(self, state: dict | None, active: bool = False) -> None:
        self._build_state = state
        if state is None and self.plan_completed():
            state = {"module": "BUILD", "status": "pending", "error": "",
                     "gates_enabled": self._options.gates_enabled, "phases": copy.deepcopy(self._plan_state["phases"])}
            for phase in state["phases"]:
                for task in phase["threads"]:
                    task.update(writer="pending", g0="pending", g1="pending", g2="pending", error="")
                    task.pop("gates_enabled", None)
        self.display_execution_panel(state, active)

    def show_build_status(self) -> None:
        state = self._build_state
        done = bool(state and state["status"] == "done")
        status = "Concluído" if done else "Interrompido" if state else "Ociosa"
        description = ("BUILD concluído. Resultados preservados; nenhuma execução automática." if done else
                       (state.get("error") or "BUILD incompleto. Pressione Iniciar para retomar a task pendente.") if state else
                       "BUILD utilizará o PRD e as tasks do PLAN concluído.")
        self.query_one("#status", Static).update("● " + status)
        self.query_one("#progress-state", Static).update("Estado: " + status)
        self.query_one("#stage", Static).update(description)

    def action_cancel_build(self) -> None:
        if self._build_running and self._build_worker:
            self.write_log("sistema", "Interrupção do BUILD solicitada. Nenhuma nova tentativa será iniciada.")
            self._build_worker.cancel()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "cancel_build":
            return self._build_running and (not self._build_state or self._build_state["status"] != "done")
        return super().check_action(action, parameters)

    def show_plan_status(self) -> None:
        state = self._plan_state
        if state is None:
            status, description = "Ociosa", "PLAN utilizará automaticamente o PRD final de INIT concluído."
        elif state["status"] == "done":
            status, description = "Concluído", "PLAN salvo em .harness/dados/plano.md. BUILD não foi executado."
        else:
            status = "Interrompido" if state["status"] not in ("failed", "interrupted") else "Falha/interrupção"
            description = state.get("error") or "PLAN incompleto salvo. Pressione Iniciar para retomar com as opções selecionadas."
        self.query_one("#progress-state", Static).update(f"Estado: {status}")
        self.query_one("#stage", Static).update(description)
        header = "● Concluído" if state and state["status"] == "done" else ("● Falha" if state else "● Ociosa")
        self.query_one("#status", Static).update(header)

    @on(Button.Pressed, "#start")
    def start_pressed(self) -> None:
        if self._init_running or self._plan_running or self._build_running:
            return
        if self._workspace_error:
            self.notify(self._workspace_error, severity="error")
            return
        module = self.query_one("#module", RadioSet).pressed_button
        if module is None:
            return
        if module.id == "module-build" and not self.plan_completed():
            self.notify("BUILD exige PLAN concluído.", severity="error")
            return
        if self.model_id is None:
            self.notify("Selecione um modelo antes de iniciar o módulo.", severity="error")
            return
        module_name = module.id.removeprefix("module-")
        if self.module_completed(module_name) and self._restart_requested != module_name:
            self.push_screen(RestartStageDialog(module_name), lambda accepted: self.restart_decision(module_name, accepted, start=True))
            return
        selection = Selection(
            str(self.query_one("#provider", Select).value),
            self.model_id,
            str(self.query_one("#effort", Select).value),
        )
        self.query_one("#configuration").disabled = True
        if module.id == "module-build":
            self._build_running = True
            self.refresh_bindings()
            self._build_worker = self.execute_build(selection)
        elif module.id == "module-plan":
            self._plan_running = True
            self.execute_plan(selection)
        else:
            self._init_running = True
            self.init_completed = False
            self.query_one(InitProgress).reset(self._options.gates_enabled)
            self.execute_init(selection)

    def init_stage(self, state: str, description: str) -> None:
        progress = self.query_one(InitProgress)
        progress.set_stage(state)
        self._init_header = "● Concluído" if state == "Concluído" else "● Executando"
        self.query_one("#status", Static).update(self._init_header)
        display_state = "Análise" if state == "Entrevista" and progress.phase == 1 else state
        self._init_state_text = f"Estado: {display_state}"
        self._init_stage_text = description
        self.query_one("#progress-state", Static).update(self._init_state_text)
        self.query_one("#stage", Static).update(self._init_stage_text)
        self.write_log("sistema", description)

    @work(group="init", exclusive=True, exit_on_error=False)
    async def execute_init(self, selection: Selection) -> None:
        configuration = self.query_one("#configuration")
        source = self.query_one(PromptSource)
        runner = CliRunner(
            selection, self.working_directory,
            lambda text: self.write_log("executor", text),
            [server.token for server in source.servers],
        )
        files = RunFiles(self.working_directory)
        try:
            options = read_options(self.working_directory)
            save_options(self.working_directory, options)
            self.show_options(options)
            self.query_one(InitProgress).reset(options.gates_enabled)
            runner, auditor = self.execution_runners(selection, options)
            self.init_stage("Preparando", "Lendo o pedido original da origem selecionada.")
            origin = str(self.query_one("#prompt-source", Select).value)
            if origin == "manual":
                original = self.query_one("#manual-prompt", TextArea).text
            elif origin == "file":
                path = source.file_path

                def read_file() -> str:
                    if path is None or not path.is_file():
                        raise InitError("Selecione um arquivo regular para o pedido original.")
                    return path.read_text(encoding="utf-8-sig")

                original = await asyncio.to_thread(read_file)
            else:
                original = source.mcp_text
                if not original.strip():
                    raise InitError("Selecione um projeto MCP e aguarde o carregamento do pedido antes de iniciar.")
            if not original.strip():
                raise InitError("O pedido original está vazio.")
            if self._restart_requested == "init":
                reset_stage(self.working_directory, "init")
                self.reset_clock("INIT")
                self._restart_requested = None
                self._init_saved = False
                self._build_state = None
                self.update_plan_panel(None)
                self.write_log("sistema", "Resultados anteriores de INIT, PLAN e BUILD excluídos por confirmação. Refazendo INIT.")
            self.write_log("sistema", f"INIT: {selection.provider}, modelo {selection.model}, effort {selection.effort}.")
            self.write_log("sistema", f"Prompts editáveis: {prompt_directory()}")
            self.write_log("sistema", f"Gates: {'habilitados' if options.gates_enabled else 'desabilitados'}. Proibir testes automatizados: {'sim' if options.forbid_automated_tests else 'não'}.")

            async def ask(question: Question) -> str | None:
                return await self.push_screen_wait(InterviewDialog(question.text, question.choices, preview=False))

            self.start_clock("INIT")
            prd = await run_init(original, files, runner, ask, self.init_stage, gates_enabled=options.gates_enabled, auditor=auditor)
            record_init_completion(self.working_directory, prd, options.gates_enabled)
            self.stop_clock("INIT")
            self.init_completed = True
            self._init_saved = True
            gate_status = "G0 APROVADO" if options.gates_enabled else "Gates desabilitados; G0 INIT não executado"
            self.write_log("sucesso", f"INIT concluído — {gate_status}. PRD: {prd}")
            await self.push_screen_wait(ResultDialog(True, f"INIT concluído. {gate_status}.\nPRD final: {prd}"))
            if await self.push_screen_wait(ContinueDialog(preview=False, gates_enabled=options.gates_enabled)):
                self.query_one("#module-plan", RadioButton).value = True
                self.query_one("#status", Static).update("● Ociosa")
                self.query_one("#progress-state", Static).update("Estado: Ociosa")
                self.query_one("#stage", Static).update("INIT concluído. PLAN selecionado; pressione Iniciar quando desejar planejar.")
                self.write_log("sistema", "PLAN selecionado. Provedor, modelo e effort preservados; PLAN não foi executado.")
        except asyncio.CancelledError:
            self.stop_clock("INIT")
            if files.started:
                try:
                    files.write("erro-init.txt", "INIT interrompido ao encerrar a aplicação. Histórico confirmado preservado.\n")
                except InitError:
                    pass
            raise
        except Exception as error:
            self.stop_clock("INIT")
            known = isinstance(error, (InitError, PromptError, ProviderError, ProjectConfigError, PlanError, WorkspaceError, OSError, UnicodeError))
            message = runner.safe(str(error)) if known else f"Falha interna no INIT ({type(error).__name__}). Arquivos existentes preservados."
            if files.started:
                try:
                    files.write("erro-init.txt", message + "\n")
                except InitError:
                    message += "\nNão foi possível salvar o registro da falha."
            cancelled = isinstance(error, InitCancelled)
            self.query_one(InitProgress).fail(cancelled=cancelled, rejected=message.startswith("G0 REPROVADO"))
            self._init_header = "● Cancelado" if cancelled else "● Falha"
            self._init_state_text = "Estado: Cancelado" if cancelled else "Estado: INIT não concluído"
            self._init_stage_text = message
            self.query_one("#status", Static).update(self._init_header)
            self.query_one("#progress-state", Static).update(self._init_state_text)
            self.query_one("#stage", Static).update(message)
            self.write_log("erro", message)
            await self.push_screen_wait(ResultDialog(False, message))
        finally:
            self.stop_clock("INIT")
            self._init_running = False
            if self.is_running:
                configuration.disabled = False

    def action_preview_interview(self) -> None:
        if self._init_running or self._plan_running or self._build_running:
            return
        self.push_screen(InterviewDialog("Pergunta de exemplo da entrevista", ("Alternativa A", "Alternativa B")))

    def action_preview_result(self, success: bool) -> None:
        if self._init_running or self._plan_running or self._build_running:
            return
        self.push_screen(ResultDialog(success, "Prévia visual. Nenhuma execução foi realizada."))

    def action_preview_continue(self) -> None:
        if self._init_running or self._plan_running or self._build_running:
            return
        self.push_screen(ContinueDialog())

    @work(group="plan", exclusive=True, exit_on_error=False)
    async def execute_plan(self, selection: Selection) -> None:
        configuration = self.query_one("#configuration")
        store = PlanStore(self.working_directory)
        source = self.query_one(PromptSource)
        runner = CliRunner(selection, self.working_directory, lambda text: self.write_log("executor", text), [server.token for server in source.servers])
        try:
            options = read_options(self.working_directory)
            save_options(self.working_directory, options)
            self.show_options(options)
            runner, auditor = self.execution_runners(selection, options)
            prd = await asyncio.to_thread(read_init_prd, self.working_directory)
            if self._restart_requested == "plan":
                reset_stage(self.working_directory, "plan")
                self.reset_clock("PLAN")
                self._restart_requested = None
                self._build_state = None
                self.update_plan_panel(None)
                self.write_log("sistema", "Resultados anteriores de PLAN e BUILD excluídos por confirmação. Refazendo PLAN a partir do PRD preservado.")
            self.call_after_refresh(self.query_one("#body").scroll_to_widget, self.query_one("#monitor"), top=True, animate=False)
            self.write_log("sistema", f"PLAN: {selection.provider}, modelo {selection.model}, effort {selection.effort}; gates {'habilitados' if options.gates_enabled else 'desabilitados'}.")

            def update(state: dict, message: str) -> None:
                self.update_plan_panel(state, active=True)
                self.query_one("#status", Static).update("● Executando")
                self.query_one("#progress-state", Static).update("Estado: Planejando" if state["status"] != "done" else "Estado: Concluído")
                self.query_one("#stage", Static).update(message)
                self.write_log("sistema", message)

            self.start_clock("PLAN")
            path = await run_plan(prd, store, runner, options, update, auditor=auditor)
            self.stop_clock("PLAN")
            self.update_plan_panel(store.state, active=False)
            self.query_one("#status", Static).update("● Concluído")
            self.write_log("sucesso", f"PLAN concluído. Plano: {path}. BUILD não foi executado.")
            await self.push_screen_wait(ResultDialog(True, f"PLAN concluído. Todas as threads concluídas.\nPlano: {path}\nSelecione BUILD quando desejar implementar."))
        except asyncio.CancelledError:
            self.stop_clock("PLAN")
            if store.state is not None and store.state["status"] != "done":
                try:
                    store.fail("PLAN interrompido ao encerrar a aplicação. Progresso preservado; pressione Iniciar para retomar.", interrupted=True)
                except PlanError:
                    pass
            raise
        except Exception as error:
            self.stop_clock("PLAN")
            known = isinstance(error, (PlanError, PlanFormatError, ProviderError, PromptError, ProjectConfigError, WorkspaceError, OSError, UnicodeError))
            message = runner.safe(str(error)) if known else f"Falha interna no PLAN ({type(error).__name__}). Arquivos preservados."
            if store.state is not None:
                try:
                    store.fail(message)
                except PlanError:
                    message += "\nNão foi possível atualizar o registro da falha."
                self.update_plan_panel(store.state, active=False)
            self.query_one("#status", Static).update("● Falha")
            self.query_one("#progress-state", Static).update("Estado: PLAN interrompido")
            self.query_one("#stage", Static).update(message)
            self.write_log("erro", message)
            await self.push_screen_wait(ResultDialog(False, message))
        finally:
            self.stop_clock("PLAN")
            self._plan_running = False
            if self.is_running:
                configuration.disabled = False

    @work(group="build", exclusive=True, exit_on_error=False)
    async def execute_build(self, selection: Selection) -> None:
        configuration = self.query_one("#configuration")
        store = BuildStore(self.working_directory)
        source = self.query_one(PromptSource)
        runner = CliRunner(selection, self.working_directory, lambda text: self.write_log("executor", text), [server.token for server in source.servers])
        try:
            options = read_options(self.working_directory)
            save_options(self.working_directory, options)
            self.show_options(options)
            runner, auditor = self.execution_runners(selection, options)
            if self._restart_requested == "build":
                await asyncio.to_thread(read_build_input, self.working_directory)
                reset_stage(self.working_directory, "build")
                self.reset_clock("BUILD")
                self._restart_requested = None
                self.update_build_panel(None)
                self.write_log("sistema", "Reinício do BUILD confirmado. Controles anteriores excluídos; código existente preservado.")
            self.call_after_refresh(self.query_one("#body").scroll_to_widget, self.query_one("#monitor"), top=True, animate=False)
            self.write_log("sistema", f"BUILD: {selection.provider}, modelo {selection.model}, effort {selection.effort}; gates {'habilitados' if options.gates_enabled else 'desabilitados'}. Ctrl+C interrompe, Ctrl+Q encerra.")

            def update(state, message):
                self.update_build_panel(state, active=state["status"] != "done")
                self.refresh_bindings()
                self.query_one("#status", Static).update("● Concluído" if state["status"] == "done" else "● Executando")
                preparing = bool(state["phases"] and state["phases"][0]["threads"][0].get("environment_pending"))
                self.query_one("#progress-state", Static).update("Estado: " + {"done": "Concluído", "retrying": "Nova tentativa", "correcting": "Corrigindo"}.get(state["status"], "Preparando ambiente" if preparing else "Implementando"))
                self.query_one("#stage", Static).update(message)
                self.write_log("sistema", message)

            self.start_clock("BUILD")
            path = await run_build(store, runner, options, update, auditor=auditor)
            self.stop_clock("BUILD")
            self.update_build_panel(store.state)
            self.write_log("sucesso", f"BUILD concluído. Estado: {path}")
            await self.push_screen_wait(ResultDialog(True, "BUILD concluído. Todas as tasks e fases concluídas."))
        except asyncio.CancelledError:
            self.stop_clock("BUILD")
            if store.state and store.state["status"] != "done":
                try:
                    store.fail("BUILD interrompido pelo usuário. Arquivos e progresso preservados; pressione Iniciar para retomar.", interrupted=True)
                except PlanError:
                    if self.is_running:
                        self.write_log("erro", "Não foi possível salvar a interrupção do BUILD. Verifique as permissões e o espaço disponível em .harness.")
                if self.is_running:
                    self.update_build_panel(store.state)
                    self.show_build_status()
                    self.write_log("sistema", store.state["error"])
            raise
        except Exception as error:
            self.stop_clock("BUILD")
            known = isinstance(error, (BuildError, PlanError, PlanFormatError, PromptError, ProjectConfigError, WorkspaceError, OSError, UnicodeError))
            message = runner.safe(str(error)) if known else f"Falha interna no BUILD ({type(error).__name__}). Progresso preservado."
            if store.state:
                try:
                    store.fail(message)
                except PlanError:
                    message += "\nNão foi possível salvar o registro da falha do BUILD."
                self.update_build_panel(store.state)
            self.query_one("#status", Static).update("● Falha")
            self.query_one("#progress-state", Static).update("Estado: BUILD suspenso")
            self.query_one("#stage", Static).update(message)
            self.write_log("erro", message)
            await self.push_screen_wait(ResultDialog(False, message))
        finally:
            self.stop_clock("BUILD")
            self._build_running = False
            self._build_worker = None
            if self.is_running:
                self.refresh_bindings()
                configuration.disabled = False
                self.query_one("#start", Button).label = "Refazer BUILD" if self.module_completed("build") else "Iniciar"
