"""Diálogos de apresentação; não iniciam nem encadeiam módulos."""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content, ContentText
from textual.geometry import Size
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, RadioButton, RadioSet, Static

from capivara2ptn.mcp_config import ConfigError, add_server, make_server


class InterviewChoice(RadioButton):
    """RadioButton nativo com rótulo completo e altura calculada pela quebra."""

    def _make_label(self, label: ContentText) -> Content:
        return Content.from_text(label, markup=False).rstrip()

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return self.render().get_height(self.styles, max(1, width))


class InterviewDialog(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def __init__(self, question: str, choices: tuple[str, ...], preview: bool = True) -> None:
        super().__init__()
        self.question = question
        self.choices = choices
        self.preview = preview

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog interview-dialog"):
            yield Static("Entrevista INIT", classes="dialog-title")
            yield Static("Prévia de interface · nenhuma entrevista em execução" if self.preview else "Entrevista em andamento · confirme sua resposta", classes="muted")
            yield Static(self.question, classes="question", markup=False)
            with RadioSet(id="interview-options"):
                for index, choice in enumerate(self.choices):
                    yield InterviewChoice(choice, id=f"answer-{index}")
                yield InterviewChoice("Outra resposta (texto livre)", id="free-answer")
            yield Input(placeholder="Digite sua resposta", id="answer-text", disabled=True)
            yield Static("Escolha uma alternativa para confirmar.", id="answer-hint", classes="muted")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="cancel")
                yield Button("Confirmar", id="confirm", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#interview-options", RadioSet).focus()

    @on(RadioSet.Changed, "#interview-options")
    def selection_changed(self, event: RadioSet.Changed) -> None:
        free = event.pressed.id == "free-answer"
        self.query_one("#answer-text", Input).disabled = not free
        self.update_confirm()

    @on(Input.Changed, "#answer-text")
    def answer_changed(self) -> None:
        self.update_confirm()

    def update_confirm(self) -> None:
        selected = self.query_one("#interview-options", RadioSet).pressed_button
        valid = selected is not None and (
            selected.id != "free-answer" or bool(self.query_one("#answer-text", Input).value.strip())
        )
        self.query_one("#confirm", Button).disabled = not valid

    @on(Button.Pressed, "#confirm")
    def confirm(self) -> None:
        selected = self.query_one("#interview-options", RadioSet).pressed_button
        if selected is None:
            return
        if selected.id == "free-answer":
            answer = self.query_one("#answer-text", Input).value.strip()
            if not answer:
                return
        else:
            answer = self.choices[int(selected.id.removeprefix("answer-"))]
        self.dismiss(answer)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ResultDialog(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Fechar")]

    def __init__(self, success: bool, message: str) -> None:
        super().__init__()
        self.success = success
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(
                "Execução concluída com sucesso" if self.success else "Falha na execução",
                classes="dialog-title success" if self.success else "dialog-title error",
            )
            yield Static(self.message, classes="question", markup=False)
            yield Button("Fechar", variant="success" if self.success else "error", id="close")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    @on(Button.Pressed, "#close")
    def close(self) -> None:
        self.dismiss(None)


class ContinueDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Voltar")]

    def __init__(self, preview: bool = True, gates_enabled: bool = True) -> None:
        super().__init__()
        self.preview = preview
        self.gates_enabled = gates_enabled

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("Continuação · INIT → PLAN", classes="dialog-title")
            yield Static("Prévia de interface · sem transição de módulos" if self.preview else ("INIT concluído · G0 APROVADO" if self.gates_enabled else "INIT concluído · gates desabilitados"), classes="muted")
            yield Static("Deseja avançar do INIT para o PLAN?" if self.preview else "Deseja selecionar PLAN? O planejamento só inicia ao pressionar Iniciar.", classes="question")
            with Horizontal(classes="dialog-actions"):
                yield Button("Agora não", id="stay")
                yield Button("Avançar para PLAN", variant="primary", id="continue")

    def on_mount(self) -> None:
        self.query_one("#stay", Button).focus()

    @on(Button.Pressed)
    def respond(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "continue")


class RestartStageDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Apenas consultar")]

    def __init__(self, module: str) -> None:
        super().__init__()
        self.module = module

    def compose(self) -> ComposeResult:
        affected = {"init": "INIT, PLAN e BUILD", "plan": "PLAN e BUILD", "build": "BUILD"}[self.module]
        with VerticalScroll(classes="dialog"):
            yield Static(f"Refazer {self.module.upper()}?", classes="dialog-title")
            yield Static(
                f"Ao pressionar Iniciar, todos os resultados de {affected} em .harness/dados serão excluídos e a fase será refeita por inteiro. Essa exclusão é definitiva.",
                classes="question", markup=False,
            )
            yield Static("Os arquivos do produto fora de .harness serão preservados.", classes="muted")
            with Horizontal(classes="dialog-actions"):
                yield Button("Apenas consultar", id="inspect-stage")
                yield Button("Refazer ao iniciar", id="restart-stage", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#inspect-stage", Button).focus()

    @on(Button.Pressed)
    def respond(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "restart-stage")


class PromptFileDialog(ModalScreen[Path | None]):
    """Seletor somente de leitura, enraizado no cwd original."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog file-dialog"):
            yield Static("Selecionar arquivo de prompt", classes="dialog-title")
            yield Static(str(self.directory), classes="muted", markup=False)
            yield Static("Setas para navegar · Enter para escolher · Esc para cancelar", classes="muted")
            yield DirectoryTree(self.directory, id="prompt-tree")
            yield Button("Cancelar", id="cancel-file")

    def on_mount(self) -> None:
        self.query_one(DirectoryTree).focus()

    @on(DirectoryTree.FileSelected)
    def select_file(self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        self.dismiss(event.path)

    @on(Button.Pressed, "#cancel-file")
    def cancel(self) -> None:
        self.dismiss(None)


class ModelIdDialog(ModalScreen[str | None]):
    """Entrada de ID para uma CLI sem catálogo automatizado disponível."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("Identificador do modelo", classes="dialog-title")
            yield Static("Informe o ID aceito pela CLI. A disponibilidade não será validada.", classes="question")
            yield Input(placeholder="ID original do modelo", id="model-id")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="cancel-model")
                yield Button("Usar ID", id="confirm-model", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#model-id", Input).focus()

    @on(Input.Changed, "#model-id")
    def identifier_changed(self, event: Input.Changed) -> None:
        identifier = event.value.strip()
        self.query_one("#confirm-model", Button).disabled = (
            not identifier or any(char.isspace() for char in identifier)
        )

    @on(Input.Submitted, "#model-id")
    @on(Button.Pressed, "#confirm-model")
    def confirm(self) -> None:
        if not self.query_one("#confirm-model", Button).disabled:
            self.dismiss(self.query_one("#model-id", Input).value.strip())

    @on(Button.Pressed, "#cancel-model")
    def cancel(self) -> None:
        self.dismiss(None)


class McpServerDialog(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("Adicionar servidor MCP", classes="dialog-title")
            yield Static("Nome", classes="field-label")
            yield Input(placeholder="Identificação da conexão", id="mcp-name")
            yield Static("URL HTTP ou HTTPS", classes="field-label")
            yield Input(placeholder="https://servidor/mcp", id="mcp-url")
            yield Static("Token Bearer (opcional)", classes="field-label")
            yield Input(placeholder="Somente o token", password=True, id="mcp-token")
            yield Static("", id="mcp-config-error", classes="error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="cancel-server")
                yield Button("Salvar", id="save-server", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#mcp-name", Input).focus()

    @on(Button.Pressed, "#save-server")
    def save(self) -> None:
        try:
            server = make_server(
                self.query_one("#mcp-name", Input).value,
                self.query_one("#mcp-url", Input).value,
                self.query_one("#mcp-token", Input).value,
            )
            add_server(server)
        except ConfigError as error:
            self.query_one("#mcp-config-error", Static).update(str(error))
            return
        self.query_one("#mcp-token", Input).value = ""
        self.dismiss(server.id)

    @on(Button.Pressed, "#cancel-server")
    def cancel(self) -> None:
        self.query_one("#mcp-token", Input).value = ""
        self.dismiss(None)


class McpArgumentsDialog(ModalScreen[dict[str, str] | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def __init__(self, arguments: tuple[dict, ...]) -> None:
        super().__init__()
        self.arguments = arguments

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("Argumentos do prompt MCP", classes="dialog-title")
            for index, argument in enumerate(self.arguments):
                suffix = " · obrigatório" if argument.get("required") else " · opcional"
                yield Static(argument["name"] + suffix, classes="field-label", markup=False)
                yield Input(id=f"mcp-argument-{index}", tooltip=str(argument.get("description", "")))
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="cancel-arguments")
                yield Button("Consultar", id="use-arguments", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.validate_arguments()

    @on(Input.Changed)
    def validate_arguments(self) -> None:
        valid = all(
            not argument.get("required") or self.query_one(f"#mcp-argument-{index}", Input).value.strip()
            for index, argument in enumerate(self.arguments)
        )
        self.query_one("#use-arguments", Button).disabled = not valid

    @on(Button.Pressed, "#use-arguments")
    def confirm(self) -> None:
        self.dismiss({
            argument["name"]: self.query_one(f"#mcp-argument-{index}", Input).value
            for index, argument in enumerate(self.arguments)
            if self.query_one(f"#mcp-argument-{index}", Input).value
        })

    @on(Button.Pressed, "#cancel-arguments")
    def cancel(self) -> None:
        self.dismiss(None)
