"""Entrevistas de múltipla escolha e execução documental dentro da TUI."""

import asyncio
from pathlib import Path
from time import monotonic

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

from .artifacts import InitError
from .init_engine import InitEngine
from .init_models import Answer, Question
from .ui import Step


class InterviewStep(Step):
    BINDINGS = [Binding("ctrl+d", "submit_free", "Confirmar resposta livre", priority=True)]

    def __init__(self, question: Question, index: int, total: int) -> None:
        super().__init__(f"Entrevista · {index}/{total}", question.question, 5)
        self.question = question
        self.free = False

    def fields(self) -> ComposeResult:
        yield Static(f"Recomendação: {self.question.recommendation_reason}\n"
                     f"Objetivo considerado: “{self.question.goal_reference}”", classes="context", markup=False)
        choices = []
        for option in self.question.options:
            recommended = " · Recomendada" if option.id == self.question.recommended_option_id else ""
            choices.append(Option(Text(f"{option.label}{recommended}\n{option.description}"), id=option.id))
        choices.append(Option("Outra opção — descrever livremente", id="other"))
        yield OptionList(*choices, id="interview-options")
        editor = TextArea(id="interview-free", placeholder="Descreva sua escolha. Enter insere uma linha; Ctrl+D confirma.")
        editor.display = False
        yield editor

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.highlighted = next(i for i, option in enumerate(self.question.options)
                                 if option.id == self.question.recommended_option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        key = str(event.option.id)
        if key == "other":
            self.free = True
            self.query_one(OptionList).display = False
            editor = self.query_one(TextArea)
            editor.display = True
            editor.focus()
            return
        option = next(option for option in self.question.options if option.id == key)
        self.dismiss(Answer(question_id=self.question.id, option_id=key, label=option.label, text=""))

    def action_submit_free(self) -> None:
        if not self.free:
            return
        text = self.query_one(TextArea).text
        if not text.strip():
            self.error("Descreva sua escolha antes de confirmar.")
            return
        self.dismiss(Answer(question_id=self.question.id, option_id="other", label="Outra opção", text=text))

    def action_back(self) -> None:
        if self.free:
            self.free = False
            self.error("")
            self.query_one(TextArea).display = False
            menu = self.query_one(OptionList)
            menu.display = True
            menu.focus()
        else:
            self.dismiss(None)


class FinalPromptStep(Step):
    def __init__(self, path: Path) -> None:
        super().__init__("Prompt final aprovado", str(path), 5)
        self.path = path

    def fields(self) -> ComposeResult:
        yield TextArea(self.path.read_text(encoding="utf-8"), read_only=True, id="final-prompt")


class InitExecutionStep(Step):
    BINDINGS = [Binding("escape", "back", "Interromper / voltar", priority=True)]

    def __init__(self) -> None:
        super().__init__("Init · geração do prompt", "Revisor e escritor refinam a documentação até a aprovação.", 5)
        self.busy = False
        self.engine = None
        self.final_path = None
        self.started_at = monotonic()

    def fields(self) -> ComposeResult:
        yield Static("Iniciando…", id="init-progress", markup=False)
        yield Static("", id="init-elapsed", markup=False)
        yield RichLog(wrap=True, highlight=False, markup=False, id="init-log")
        for label, key in (("Ver prompt final", "init-view"), ("Tentar init novamente", "init-retry"), ("Fechar", "init-close")):
            button = Button(label, id=key, compact=True)
            button.display = False
            yield button

    def on_mount(self) -> None:
        self.set_interval(1, self.elapsed)
        self.call_after_refresh(self.start)

    def focus_field(self) -> None:
        if self.query("#back"):
            self.query_one("#back", Button).focus()

    def elapsed(self) -> None:
        if self.busy and self.is_mounted:
            self.query_one("#init-elapsed", Static).update(f"Tempo da execução: {int(monotonic() - self.started_at)} s · Esc interrompe")

    def start(self) -> None:
        if self.busy or not self.is_mounted or not self.is_current:
            return
        self.busy = True
        self.started_at = monotonic()
        self.error("")
        self.query_one("#back", Button).label = "Interromper · Esc"
        for button in self.query("#init-retry, #init-close, #init-view"):
            button.display = False
        self.execute()

    def progress(self, message: str) -> None:
        if self.is_mounted:
            self.query_one("#init-progress", Static).update(message)
            self.query_one(RichLog).write(Text(message))

    async def ask(self, question: Question, index: int, total: int) -> Answer:
        answer = await self.wizard.push_screen_wait(InterviewStep(question, index, total))
        if answer is None:
            raise asyncio.CancelledError()
        return answer

    @work(exclusive=True)
    async def execute(self) -> None:
        try:
            self.engine = InitEngine(self.wizard.state, self.wizard.agents, interview=self.ask, progress=self.progress)
            self.final_path = await self.engine.run()
            self.progress(f"Init concluído. Prompt aprovado: {self.final_path}")
            self.query_one("#init-view", Button).display = True
        except asyncio.CancelledError:
            if self.is_mounted and self.is_current:
                self.progress("Init interrompido. O projeto não foi marcado como concluído.")
                self.query_one("#init-retry", Button).display = True
        except (InitError, OSError, ValueError) as exc:
            if self.is_mounted:
                self.error(str(exc))
                self.progress("Init não concluído. Os registros disponíveis ficaram em .harness.")
                self.query_one("#init-retry", Button).display = True
        finally:
            self.busy = False
            if self.is_mounted and self.is_current:
                self.query_one("#back", Button).label = "Voltar · Esc"
                self.query_one("#init-close", Button).display = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        key = event.button.id
        if key in ("init-view", "init-retry", "init-close"):
            event.prevent_default()
            if key == "init-view" and self.final_path:
                self.wizard.push_screen(FinalPromptStep(self.final_path))
            elif key == "init-retry":
                self.start()
            elif key == "init-close":
                self.wizard.exit(self.wizard.state)

    def action_back(self) -> None:
        if self.busy:
            self.wizard.workers.cancel_node(self)
        else:
            super().action_back()


class PendingModuleStep(Step):
    def __init__(self, module: str) -> None:
        super().__init__(f"{module} · próxima etapa", "O init foi concluído. Este módulo ainda será implementado.", 1)

    def fields(self) -> ComposeResult:
        yield Static("O prompt aprovado está em .harness/prompts/final.md. Use Esc para voltar.", markup=False)
