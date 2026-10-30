"""Estrutura visual compartilhada pelas etapas do wizard."""

from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

if TYPE_CHECKING:
    from .app import HarnessApp


class Step(Screen):
    BINDINGS = [Binding("escape", "back", "Voltar", priority=True)]

    def __init__(self, title: str, hint: str, section: int) -> None:
        super().__init__()
        self.heading = title
        self.hint = hint
        self.section = section

    @property
    def wizard(self) -> "HarnessApp":
        return cast("HarnessApp", self.app)

    def fields(self) -> ComposeResult:
        yield Static("")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="viewport"):
            with Vertical(id="panel"):
                breadcrumb = Text()
                for index, label in enumerate(("Projeto", "Função", "Prompt", "Escritor", "Revisor", "Confirmar")):
                    if self.wizard.state.module in ("plan","build") and index == 2:
                        label = "Plano" if self.wizard.state.module=="build" else "Prompt do init"
                    if index:
                        breadcrumb.append("  ›  ", style="dim")
                    breadcrumb.append(label, style="bold cyan" if index == self.section else "dim")
                yield Static(breadcrumb, id="steps")
                yield Static(self.heading, id="heading", markup=False)
                yield Static(self.hint, id="hint", markup=False)
                yield Static("", id="error", markup=False)
                yield from self.fields()
                with Horizontal(id="actions"):
                    yield Button("Voltar · Esc", id="back", disabled=self.section == 0, compact=True)
        yield Footer()

    def on_mount(self) -> None:
        self.focus_field()

    def on_screen_resume(self) -> None:
        self.focus_field()

    def focus_field(self) -> None:
        fields = self.query("Input, OptionList, TextArea")
        if fields:
            fields.first().focus()

    def error(self, message: str) -> None:
        widget = self.query_one("#error", Static)
        widget.update(message)
        widget.display = bool(message)

    def status(self, message: str) -> None:
        widget = self.query_one("#mcp-status", Static)
        widget.update(message)
        widget.display = bool(message)

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.action_back()
