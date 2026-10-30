"""Entrevistas de múltipla escolha e execução documental dentro da TUI."""

import asyncio
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Footer, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .artifacts import InitError
from .build_artifacts import DependenciesBlocked
from .dashboard import InitDashboard
from .init_engine import InitEngine
from .init_models import Answer, Question
from .run_events import InitEvent
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
    def __init__(self, path: Path, *, module: str = "init") -> None:
        super().__init__("Relatório do build" if module=="build" else "Plano final aprovado" if module == "plan" else "Prompt final aprovado", str(path), 5)
        self.path = path

    def fields(self) -> ComposeResult:
        yield TextArea(self.path.read_text(encoding="utf-8"), read_only=True, id="final-prompt")


class InitExecutionStep(Step):
    BINDINGS = [Binding("escape", "back", "Interromper / voltar", priority=True)]

    def __init__(self, *, module: str = "init") -> None:
        super().__init__(f"{module.title()} · documentação", "Revisor e escritor refinam a documentação até a aprovação.", 5)
        self.module = module
        self.busy = False
        self.engine = None
        self.final_path = None
        self.add_class("execution-screen")

    def compose(self) -> ComposeResult:
        agents = {role: agent for role, agent in (("writer", self.wizard.state.writer),
                                                  ("reviewer", self.wizard.state.reviewer)) if agent is not None}
        yield InitDashboard(self.wizard.state.project_dir, agents, module=self.module)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1, self.elapsed)
        self.call_after_refresh(self.start)

    def focus_field(self) -> None:
        if self.query(InitDashboard):
            self.query_one(InitDashboard).focus()

    def elapsed(self) -> None:
        if self.busy and self.is_mounted:
            self.query_one(InitDashboard).tick()

    def start(self) -> None:
        if self.busy or not self.is_mounted or not self.is_current:
            return
        self.busy = True
        self.error("")
        self.query_one(InitDashboard).start()
        self.query_one("#back", Button).label = "Interromper · Esc"
        for button in self.query("#init-retry, #init-close, #init-view"):
            button.display = False
        self.execute()

    def progress(self, message: str) -> None:
        if self.is_mounted:
            self.query_one("#init-progress", Static).update(message)
            self.query_one(InitDashboard).log(message)

    def execution_event(self, event: InitEvent) -> None:
        if self.is_mounted and self.query(InitDashboard):
            self.query_one(InitDashboard).update_event(event)

    async def ask(self, question: Question, index: int, total: int) -> Answer:
        answer = await self.wizard.push_screen_wait(InterviewStep(question, index, total))
        if answer is None:
            raise asyncio.CancelledError()
        return answer

    @work(exclusive=True)
    async def execute(self) -> None:
        try:
            if self.module == "build":
                from .build_engine import BuildEngine
                self.engine = BuildEngine(self.wizard.state,self.wizard.build_agents,on_event=self.execution_event)
            elif self.module == "plan":
                from .plan_engine import PlanEngine
                self.engine = PlanEngine(self.wizard.state, self.wizard.agents, on_event=self.execution_event)
            else:
                self.engine = InitEngine(self.wizard.state, self.wizard.agents, interview=self.ask, on_event=self.execution_event)
            self.final_path = await self.engine.run()
            self.query_one(InitDashboard).finish("completed")
            self.progress("Build concluído. Aplicação e README entregues na raiz; relatório em .harness/build/report.md" if self.module=="build" else "Plan concluído. Plano aprovado: .harness/plan/final.md" if self.module == "plan"
                          else "Init concluído. Prompt aprovado: .harness/prompts/final.md")
            self.query_one("#init-view", Button).display = True
        except asyncio.CancelledError:
            if self.is_mounted and self.is_current:
                self.query_one(InitDashboard).finish("cancelled")
                self.progress(f"{self.module.title()} interrompido. O módulo não foi marcado como concluído.")
                self.query_one("#init-retry", Button).display = True
        except DependenciesBlocked as exc:
            if self.is_mounted:
                self.query_one(InitDashboard).finish("blocked")
                self.error(str(exc))
                self.progress("Build pausado por dependências. Instale os pacotes indicados e execute o build novamente.")
                self.query_one("#init-retry",Button).display=True
        except (InitError, OSError, ValueError) as exc:
            if self.is_mounted:
                self.query_one(InitDashboard).finish("failed")
                self.error(str(exc))
                self.progress(f"{self.module.title()} não concluído. Os registros disponíveis ficaram em .harness.")
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
                self.wizard.push_screen(FinalPromptStep(self.final_path, module=self.module))
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
        super().__init__(f"{module} · próxima etapa", "O plan foi concluído. O build ainda será implementado.", 1)

    def fields(self) -> ComposeResult:
        yield Static("O plano aprovado está em .harness/plan/final.md. Use Esc para voltar.", markup=False)
