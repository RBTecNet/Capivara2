"""Cadastro, validação e navegação entre instâncias MCP na TUI."""

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from .mcp_registry import MCPError, MCPInstance, make_instance
from .ui import Step


class MCPInstancesStep(Step):
    def __init__(self) -> None:
        super().__init__("Instâncias MCP", "Escolha um recurso registrado ou adicione um MCP.", 2)
        self.instances: dict[str, MCPInstance] = {}

    def fields(self) -> ComposeResult:
        yield OptionList(Option("Adicionar MCP", id="add"), id="mcp-instances")

    def on_mount(self) -> None:
        self.refresh_instances()

    def on_screen_resume(self) -> None:
        self.refresh_instances()

    def refresh_instances(self) -> None:
        menu = self.query_one(OptionList)
        menu.clear_options()
        try:
            self.instances = {instance.id: instance for instance in self.wizard.mcp_registry.instances()}
            menu.add_options(Option(Text(instance.name), id=f"instance:{instance.id}")
                             for instance in self.instances.values())
            self.error("")
        except MCPError as exc:
            self.instances = {}
            self.error(str(exc))
        menu.add_option(Option("Adicionar MCP", id="add"))
        menu.highlighted = next((index for index, instance_id in enumerate(self.instances)
                                 if instance_id == self.wizard.last_mcp_id), 0)
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        key = str(event.option.id)
        if key == "add":
            self.wizard.push_screen(MCPFormStep())
        elif key.startswith("instance:"):
            instance = self.instances[key.removeprefix("instance:")]
            self.wizard.last_mcp_id = instance.id
            self.wizard.push_screen(MCPProjectsStep(instance))


class MCPFormStep(Step):
    BINDINGS = [Binding("ctrl+s", "save", "Validar e salvar", priority=True)]

    def __init__(self, instance: MCPInstance | None = None) -> None:
        super().__init__("Alterar MCP" if instance else "Adicionar MCP",
                         "Informe o recurso e valide o acesso para salvar. Enter avança entre os campos.", 2)
        self.instance = instance
        self.busy = False

    def fields(self) -> ComposeResult:
        instance = self.instance
        yield Static("Nome do recurso", classes="field-label")
        yield Input(instance.name if instance else "", placeholder="Ex.: Documentos da equipe", id="mcp-name", compact=True, classes="mcp-field")
        yield Static("Endereço do MCP", classes="field-label")
        yield Input(instance.url if instance else "", placeholder="http://servidor:7777/mcp", id="mcp-url", compact=True, classes="mcp-field")
        yield Static("Chave API de autenticação", classes="field-label")
        yield Input(instance.api_key if instance else "", password=True, id="mcp-key", compact=True, classes="mcp-field",
                    placeholder="Token, env:NOME ou @/caminho/da/chave")
        yield Button("Validar e salvar", id="mcp-save", variant="primary", compact=True)
        status = Static("", id="mcp-status", markup=False)
        status.display = False
        yield status

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mcp-name":
            self.query_one("#mcp-url", Input).focus()
        elif event.input.id == "mcp-url":
            self.query_one("#mcp-key", Input).focus()
        else:
            self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mcp-save":
            event.prevent_default()
            self.action_save()

    def action_save(self) -> None:
        if not self.busy:
            self.save_instance()

    @work(exclusive=True)
    async def save_instance(self) -> None:
        self.busy = True
        self.error("")
        try:
            instance = make_instance(self.query_one("#mcp-name", Input).value,
                                     self.query_one("#mcp-url", Input).value,
                                     self.query_one("#mcp-key", Input).value,
                                     instance_id=self.instance.id if self.instance else None,
                                     base_dir=self.wizard.launch_dir)
            self.wizard.mcp_registry.check_name(instance)
            self.query_one("#mcp-save", Button).disabled = True
            for field in self.query(Input):
                field.disabled = True
            self.status("Validando acesso ao MCP…")
            await self.wizard.mcp_service.validate(instance)
            if not self.is_current:
                return
            self.wizard.mcp_registry.save(instance)
            self.wizard.last_mcp_id = instance.id
            self.wizard.notify(f"MCP “{instance.name}” validado e salvo.")
            self.wizard.pop_screen()
        except MCPError as exc:
            self.error(str(exc) + " Altere os dados e tente novamente.")
            self.status("")
        finally:
            self.busy = False
            if self.is_mounted and self.is_current:
                for button in self.query("#mcp-save"):
                    button.disabled = False
                for field in self.query(Input):
                    field.disabled = False
                self.focus_field()


class MCPProjectsStep(Step):
    def __init__(self, instance: MCPInstance) -> None:
        super().__init__("Projetos do MCP", f"{instance.name} · selecione o projeto que deseja utilizar.", 2)
        self.instance = instance
        self.projects = {}
        self.busy = False

    def fields(self) -> ComposeResult:
        yield Static(self.instance.url, classes="context", markup=False)
        yield Static("Consultando projetos…", id="mcp-status", markup=False)
        yield OptionList(id="mcp-projects", disabled=True)

    def on_mount(self) -> None:
        self.load_projects()

    def on_screen_resume(self) -> None:
        # Alterações de credenciais feitas no formulário passam a valer ao voltar.
        self.load_projects()

    @work(exclusive=True)
    async def load_projects(self) -> None:
        self.busy = True
        self.error("")
        menu = self.query_one(OptionList)
        menu.disabled = True
        self.status("Consultando projetos…")
        try:
            self.instance = next((instance for instance in self.wizard.mcp_registry.instances()
                                  if instance.id == self.instance.id), self.instance)
            self.query_one("#hint", Static).update(f"{self.instance.name} · selecione o projeto que deseja utilizar.")
            projects = await self.wizard.mcp_service.projects(self.instance)
            if not self.is_current:
                return
            self.projects = {project.slug: project for project in projects}
            menu.clear_options()
            menu.add_options(Option(Text(f"{project.name}  ·  {project.slug}"), id=f"project:{project.slug}")
                             for project in projects)
            self.status(f"{len(projects)} projeto(s) disponível(is)." if projects else
                        "Nenhum projeto com conteúdo publicado está disponível para esta chave API.")
        except MCPError as exc:
            self.error(str(exc))
            self.projects = {}
            menu.clear_options()
            self.status("")
        finally:
            self.busy = False
            if self.is_mounted and self.is_current:
                menu.add_options([Option("Atualizar projetos / tentar novamente", id="retry"),
                                  Option("Alterar dados do MCP", id="edit")])
                menu.disabled = False
                menu.highlighted = 0
                menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        key = str(event.option.id)
        if self.busy:
            return
        if key == "retry":
            self.load_projects()
        elif key == "edit":
            self.wizard.push_screen(MCPFormStep(self.instance))
        elif key.startswith("project:"):
            self.load_context(key.removeprefix("project:"))

    @work(exclusive=True)
    async def load_context(self, slug: str) -> None:
        self.busy = True
        self.error("")
        menu = self.query_one(OptionList)
        menu.disabled = True
        self.status("Carregando prompt, skills, hooks, memórias e arquivos de apoio…")
        try:
            context = await self.wizard.mcp_service.load_project(self.instance, self.projects[slug])
            if not self.is_current:
                return
            state = self.wizard.state
            state.prompt_source = "mcp"
            state.prompt = context.prompt
            state.prompt_file = None
            state.mcp_context = context
            self.wizard.notify(f"Material carregado: {context.count('skill')} skills, {context.count('hook')} hooks "
                               f"e {context.count('memoria')} memórias.")
            self.wizard.show_providers("writer")
        except MCPError as exc:
            self.error(str(exc) + " Você pode tentar novamente, alterar o MCP ou escolher outro projeto.")
            self.status("")
        finally:
            self.busy = False
            if self.is_mounted and self.is_current:
                menu.disabled = False
