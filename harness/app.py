"""Wizard em tela cheia; configuração do init mantida em memória."""

from pathlib import Path

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .catalog import Catalog
from .agents import CLIAgents
from .artifacts import InitError, suggested_module
from .domain import AgentSelection, Model, PROVIDERS, WizardState, read_prompt_file, resolve_project
from .mcp_client import MCPService
from .mcp_registry import MCPRegistry
from .ui import Step


class ProjectStep(Step):
    def __init__(self, launch_dir: Path) -> None:
        super().__init__("Pasta do projeto", "Confira ou edite o caminho completo. Enter para continuar.", 0)
        self.launch_dir = launch_dir

    def fields(self) -> ComposeResult:
        yield Input(str(self.launch_dir), id="project", select_on_focus=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            path = resolve_project(event.value, self.launch_dir)
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return
        self.wizard.state.project_dir = path
        event.input.value = str(path)
        self.wizard.show_functions()


class ChoiceStep(Step):
    def __init__(self, title: str, hint: str, section: int, kind: str,
                 options: list[tuple[str, str, bool]], *, selected: str | None = None,
                 provider: str = "", model: Model | None = None, role: str = "writer") -> None:
        super().__init__(title, hint, section)
        self.kind = kind
        self.options = options
        self.selected = selected
        self.provider = provider
        self.model = model
        self.role = role

    def fields(self) -> ComposeResult:
        yield OptionList(*(Option(Text(label), id=key, disabled=disabled)
                           for key, label, disabled in self.options), id="choices")

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.highlighted = next((i for i, (key, _, disabled) in enumerate(self.options)
                                 if key == self.selected and not disabled), 0)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.wizard.choose(self, str(event.option.id))


class ManualPromptStep(Step):
    BINDINGS = [Binding("ctrl+d", "submit_prompt", "Confirmar prompt", priority=True)]

    def __init__(self, text: str = "") -> None:
        super().__init__("Prompt do projeto", "Enter cria uma nova linha. Ctrl+D confirma o prompt e avança.", 2)
        self.text = text

    def fields(self) -> ComposeResult:
        yield TextArea(self.text, id="prompt", show_line_numbers=True,
                       placeholder="Descreva o projeto que deseja criar…")

    def on_mount(self) -> None:
        self.resize_editor(self.app.size.height)

    def on_resize(self, event: events.Resize) -> None:
        self.resize_editor(event.size.height)

    def resize_editor(self, height: int) -> None:
        editors = self.query("#prompt")
        if editors:
            editors.first().styles.height = max(4, min(14, height - 18))

    def action_submit_prompt(self) -> None:
        text = self.query_one("#prompt", TextArea).text
        if not text.strip():
            self.error("Escreva o prompt do projeto antes de continuar.")
            return
        self.wizard.state.prompt = text
        self.wizard.state.prompt_file = None
        self.wizard.state.prompt_source = "manual"
        self.wizard.state.mcp_context = None
        self.wizard.show_providers("writer")


class FilePromptStep(Step):
    def __init__(self, path: Path | None) -> None:
        super().__init__("Arquivo de prompt", "Informe um arquivo UTF-8. Caminhos relativos partem da pasta do projeto. Enter confirma.", 2)
        self.path = path

    def fields(self) -> ComposeResult:
        yield Static(str(self.wizard.state.project_dir), classes="context", markup=False)
        yield Input(str(self.path) if self.path else "", placeholder="Ex.: docs/prompt.txt", id="prompt-file")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            path, text = read_prompt_file(event.value, self.wizard.state.project_dir)
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return
        self.wizard.state.prompt_file = path
        self.wizard.state.prompt = text
        self.wizard.state.prompt_source = "file"
        self.wizard.state.mcp_context = None
        self.wizard.show_providers("writer")


class BuildAgentsStep(Step):
    def __init__(self):
        super().__init__("Máximo de agentes em paralelo", "Informe um número de 1 a 32. Reservas de arquivos podem reduzir a concorrência para evitar conflitos.", 3)

    def fields(self):
        yield Input(str(max(2,self.wizard.state.max_agents)),id="parallel-count",select_on_focus=True)

    def on_input_submitted(self,event):
        try:
            value=int(event.value)
            if not 1<=value<=32:raise ValueError()
        except ValueError:
            self.error("Informe um número inteiro de 1 a 32.");return
        self.wizard.state.max_agents=value
        self.wizard.show_reuse()


class ConfirmStep(Step):
    def __init__(self) -> None:
        super().__init__("Executar agora?", "Confira a configuração para iniciar o módulo documental.", 5)

    def on_mount(self) -> None:
        if self.wizard.state.module == "build":
            self.query_one("#hint",Static).update("Build: prepara dependências e executa G0–G3 por task. A aplicação fica na raiz; execuções anteriores podem ser retomadas.")
        elif self.wizard.state.module == "plan":
            self.query_one("#hint", Static).update("Plan documental: divide o prompt aprovado em tasks pequenas e revisa a fidelidade à origem. Reexecutar substitui somente o plan e invalida o build.")
        elif suggested_module(self.wizard.state.project_dir) != "init":
            self.query_one("#hint", Static).update("Executar o init novamente substitui os artefatos anteriores em .harness e reinicia o fluxo do projeto.")

    def fields(self) -> ComposeResult:
        state = self.wizard.state
        yield Static(f"Projeto: {state.project_dir}", classes="summary", markup=False)
        source = f"Arquivo · {state.prompt_file}" if state.prompt_source == "file" else "Escrito manualmente"
        yield Static(f"Módulo: {state.module}", classes="summary", markup=False)
        if state.module == "build":
            source = "Plano aprovado · .harness/plan/final.md"
            yield Static(f"Execução: {state.build_mode} · máximo {state.max_agents} agente(s)",classes="summary",markup=False)
        elif state.module == "plan":
            source = "Prompt aprovado do init · .harness/prompts/final.md"
        if state.prompt_source == "mcp" and state.mcp_context:
            context = state.mcp_context
            source = f"MCP · {context.instance_name} · {context.project.name} ({context.project.slug})"
        yield Static(f"Prompt: {source}", classes="summary", markup=False)
        if state.mcp_context:
            context = state.mcp_context
            yield Static(f"Material: {context.count('skill')} skills · {context.count('hook')} hooks · "
                         f"{context.count('memoria')} memórias · {context.attachments} arquivos de apoio",
                         classes="summary", markup=False)
        for role, agent in (("Escritor", state.writer), ("Revisor", state.reviewer)):
            if agent:
                yield Static(f"{role}: {PROVIDERS[agent.provider]}  /  {agent.model}  /  {agent.effort}",
                             classes="summary", markup=False)
        yield TextArea(state.prompt, read_only=True, id="preview", show_cursor=False)
        yield OptionList(Option("Sim", id="yes"), Option("Não", id="no"), id="execute")

    def focus_field(self) -> None:
        self.query_one("#execute", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.wizard.state.execute_requested = event.option.id == "yes"
        if self.wizard.state.execute_requested:
            from .init_ui import InitExecutionStep
            self.wizard.push_screen(InitExecutionStep(module=self.wizard.state.module))
        else:
            self.wizard.push_screen(DoneStep(False))


class DoneStep(Step):
    def __init__(self, requested: bool) -> None:
        title = "Wizard concluído" if requested else "Configuração concluída sem executar"
        hint = ("Solicitação de execução recebida."
                if requested else "Você escolheu não executar agora.")
        super().__init__(title, hint, 5)

    def fields(self) -> ComposeResult:
        yield Static("Esta configuração ficou somente nesta sessão. Nenhum agente foi iniciado e nenhum arquivo de projeto foi gerado.",
                     classes="context")
        yield Button("Fechar", variant="primary", id="close")

    def focus_field(self) -> None:
        self.query_one("#close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            event.prevent_default()
            self.wizard.exit(self.wizard.state)

    def action_back(self) -> None:
        self.wizard.state.execute_requested = False
        super().action_back()


class HarnessApp(App[WizardState]):
    TITLE = "CAPIVARA"
    SUB_TITLE = "Wizard de inicialização"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+q", "quit", "Sair", priority=True),
                Binding("ctrl+c", "quit", "Sair", show=False, priority=True)]
    CSS = """
    Screen { background: #101820; }
    Header { background: #172832; color: #a6e3cc; }
    Footer { background: #172832; }
    #viewport { height: 1fr; padding: 1 2; align-horizontal: center; }
    #panel { width: 100%; max-width: 104; height: auto;
             padding: 1 2; border: round #345867; background: #14212b; }
    #steps { height: auto; margin-bottom: 1; }
    #heading { text-style: bold; color: #a6e3cc; height: auto; margin-bottom: 1; }
    #hint { color: #b4c4cf; height: auto; margin-bottom: 1; }
    Input { background: #101820; border: tall #345867; }
    Input:focus { border: tall #a6e3cc; }
    OptionList { height: auto; max-height: 14; background: #101820;
                 border: round #345867; padding: 0 1; }
    OptionList:focus { border: round #a6e3cc; }
    OptionList > .option-list--option-highlighted { background: #244d50; color: #c7f7df; }
    #prompt { height: 14; min-height: 6; border: round #a6e3cc; }
    #preview { height: 5; border: round #345867; margin: 1 0; }
    .context, .summary { height: auto; color: #b4c4cf; }
    .context { margin-bottom: 1; }
    #error { color: #ff9b9b; height: auto; margin-top: 1; display: none; }
    #actions { height: 1; margin-top: 1; }
    #mcp-status { height: auto; color: #a6e3cc; margin: 1 0; }
    .mcp-field { border: none; height: 1; padding: 0 1; }
    .mcp-field:focus { border: none; background: #244d50; }
    .field-label { height: auto; color: #b4c4cf; }
    #mcp-save { margin-top: 1; }
    .execution-screen { background: #0b1720; }
    .execution-screen Footer { background: #0b1720; color: #7e939e; }
    #interview-free { height: 8; }
    #final-prompt { height: 18; }
    Button { min-width: 16; }
    """

    def __init__(self, *, launch_dir: Path | None = None, catalog: Catalog | None = None,
                 mcp_registry: MCPRegistry | None = None, mcp_service: MCPService | None = None,
                 agents: CLIAgents | None = None, build_agents: CLIAgents | None = None) -> None:
        super().__init__()
        self.launch_dir = (launch_dir or Path.cwd()).resolve()
        self.state = WizardState(project_dir=self.launch_dir)
        self.catalog = catalog or Catalog.load()
        self.agents = agents or CLIAgents(self.catalog.commands)
        self.build_agents = build_agents or CLIAgents(self.catalog.build_commands)
        self.mcp_registry = mcp_registry or MCPRegistry()
        self.mcp_service = mcp_service or MCPService()
        self.last_mcp_id: str | None = None

    def on_mount(self) -> None:
        self.push_screen(ProjectStep(self.launch_dir))

    def show_functions(self) -> None:
        try:
            selected = suggested_module(self.state.project_dir)
        except InitError as exc:
            self.screen.error(str(exc))
            return
        self.push_screen(ChoiceStep("Qual função deseja executar?", "Use ↑ e ↓ para escolher; Enter para confirmar.", 1, "function", [
            ("init", "init", False), ("plan", "plan", selected == "init"),
            ("build", "build", selected != "build")], selected=selected))

    def show_sources(self) -> None:
        self.push_screen(ChoiceStep("De onde vem o prompt do projeto?", "Escolha a origem do prompt.", 2, "source", [
            ("manual", "1. Escrever manualmente", False),
            ("file", "2. Ler a partir de um arquivo", False),
            ("mcp", "3. Base MCP", False)], selected=self.state.prompt_source))

    def show_providers(self, role: str) -> None:
        label = "escritor" if role == "writer" else "revisor"
        self.push_screen(ChoiceStep(f"Inteligência artificial · {label}", "Selecione o CLI que deseja utilizar.",
                                   3 if role == "writer" else 4, "provider",
                                   [(key, name, False) for key, name in PROVIDERS.items()], role=role))

    def choose(self, screen: ChoiceStep, key: str) -> None:
        if screen.kind == "function":
            if key == "init":
                self.state.module = "init"
                if self.state.prompt_source in ("init", "plan"):
                    self.state.prompt_source = "manual"
                    self.state.prompt = ""
                self.show_sources()
            elif key in ("plan","build"):
                from .plan_artifacts import load_init
                from .build_artifacts import load_plan
                try:
                    source = load_init(self.state.project_dir) if key=="plan" else load_plan(self.state.project_dir)
                except InitError as exc:
                    screen.error(str(exc))
                    return
                self.state.module = key
                self.state.prompt_source = "init" if key=="plan" else "plan"
                self.state.prompt_file = self.state.project_dir / (".harness/prompts/final.md" if key=="plan" else ".harness/plan/final.md")
                self.state.prompt = self.state.prompt_file.read_text(encoding="utf-8")
                self.state.mcp_context = None
                self.show_providers("writer")
            else:
                from .init_ui import PendingModuleStep
                self.push_screen(PendingModuleStep(key))
        elif screen.kind == "source":
            if key == "mcp":
                from .mcp_ui import MCPInstancesStep
                self.push_screen(MCPInstancesStep())
                return
            self.state.prompt_source = key
            self.push_screen(ManualPromptStep(self.state.prompt if self.state.prompt_file is None else "")
                             if key == "manual" else FilePromptStep(self.state.prompt_file))
        elif screen.kind == "provider":
            models = self.catalog.models.get(key, ())
            if not models and not self.catalog.discovery.get(key):
                screen.error(f"Nenhum modelo configurado para {PROVIDERS[key]}. Adicione modelos no arquivo {self.catalog.path}.")
                return
            if self.catalog.discovery.get(key):
                self.load_models(screen,key)
            else:
                self.show_models(screen,key)
        elif screen.kind == "model":
            model = next(m for m in self.catalog.models[screen.provider] if m.id == key)
            self.push_screen(ChoiceStep("Effort do escritor" if screen.role == "writer" else "Effort do revisor",
                                       f"{PROVIDERS[screen.provider]} · {model.name}", screen.section, "effort",
                                       [(e, "Padrão do CLI" if e == "default" else e, False)
                                        for e in model.efforts], provider=screen.provider,
                                       model=model, role=screen.role))
        elif screen.kind == "effort":
            assert screen.model is not None
            agent = AgentSelection(screen.provider, screen.model.id, key)
            if screen.role == "writer":
                self.state.writer = agent
                self.state.reviewer = None
                if self.state.module=="build":
                    self.push_screen(ChoiceStep("Modo de execução do build", "Escolha execução serial ou paralela; o harness reserva os arquivos de cada task.",3,"build-mode",
                        [("serial","Serial · um agente por vez",False),("parallel","Paralelo · múltiplos agentes",False)],selected=self.state.build_mode))
                else:
                    self.show_reuse()
            else:
                self.state.reviewer = agent
                self.push_screen(ConfirmStep())
        elif screen.kind == "build-mode":
            self.state.build_mode=key
            if key=="parallel":self.push_screen(BuildAgentsStep())
            else:
                self.state.max_agents=1
                self.show_reuse()
        elif screen.kind == "reuse":
            self.state.reuse_reviewer = key == "yes"
            if key == "yes":
                self.state.reviewer = self.state.writer
                self.push_screen(ConfirmStep())
            else:
                self.state.reviewer = None
                self.show_providers("reviewer")

    @work(exclusive=True,group="models",exit_on_error=False)
    async def load_models(self,screen,provider):
        menu=screen.query_one(OptionList);menu.disabled=True
        screen.error("")
        screen.query_one("#hint",Static).update(f"Consultando modelos e efforts do {PROVIDERS[provider]}…")
        try:
            await self.catalog.discover_models(provider,self.state.project_dir)
            if screen.is_current:self.show_models(screen,provider)
        except Exception as exc:
            if screen.is_mounted:screen.error(str(exc))
        finally:
            if screen.is_mounted:
                menu.disabled=False
                if screen.is_current:menu.focus()

    def show_models(self,screen,provider):
        models=self.catalog.models.get(provider,())
        if not models:
            screen.error("Nenhum modelo habilitado retornado pelo CLI.");return
        self.push_screen(ChoiceStep("Modelo do escritor" if screen.role=="writer" else "Modelo do revisor",
            f"{PROVIDERS[provider]} · {self.catalog.sources[provider]}",screen.section,"model",
            [(m.id,m.name if m.id=="default" else f"{m.name}  ·  {m.id}",False) for m in models],
            provider=provider,role=screen.role))

    def show_reuse(self):
        self.push_screen(ChoiceStep("Usar o mesmo modelo do escritor como revisor?",
            "O revisor verificará se a entrega atende ao pedido. Sim mantém também a IA e o effort.",
            4,"reuse",[("yes","Sim",False),("no","Não",False)]))

    async def action_quit(self) -> None:
        self.workers.cancel_all()
        await self.workers.wait_for_complete()
        self.exit(self.state)
