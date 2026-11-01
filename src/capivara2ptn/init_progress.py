"""Linha de etapas do INIT, atualizada pelos eventos reais da execução."""

from rich.text import Text
from textual.geometry import Size
from textual.widgets import Static


class InitProgress(Static):
    LABELS = ("Pedido", "Análise", "Entrevista", "Redação", "Revisão", "Concluído")
    DONE = "#9bdc58"
    ACTIVE = "#ffbf3f"
    WAITING = "#688398"
    ERROR = "#ff8e8e"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.phase: int | None = None
        self.gates_enabled = True
        self.interview_started = False
        self.failure: str | None = None

    def reset(self, gates_enabled: bool) -> None:
        self.phase = None
        self.gates_enabled = gates_enabled
        self.interview_started = False
        self.failure = None
        self.refresh(layout=True)

    def set_stage(self, stage: str) -> None:
        if stage == "Aguardando resposta":
            self.interview_started = True
        phases = {
            "Preparando": 0,
            "Entrevista": 2 if self.interview_started else 1,
            "Aguardando resposta": 2,
            "Entrevista concluída": 3,
            "Gerando PRD": 3,
            "Avaliando G0": 4,
            "Concluído": 5,
        }
        if stage in phases:
            self.phase = phases[stage]
            self.failure = None
            self.refresh(layout=True)

    def fail(self, cancelled: bool = False, rejected: bool = False) -> None:
        if self.phase is None:
            self.phase = 0
        self.failure = "cancelado" if cancelled else ("reprovado" if rejected else "falhou")
        self.refresh(layout=True)

    @staticmethod
    def _columns(width: int) -> int:
        return 6 if width >= 65 else (3 if width >= 32 else (2 if width >= 21 else 1))

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        rows = 6 // self._columns(width)
        return rows * 3 + rows - 1

    def _appearance(self, index: int) -> tuple[str, str, str]:
        if index == 4 and not self.gates_enabled:
            return "–", "pulada", self.WAITING
        if self.phase is not None:
            if index == self.phase and self.failure:
                status = self.failure
                if index in (1, 2, 3, 4) and status.endswith("ado"):
                    status = status[:-1] + "a"
                return "✕", status, self.ERROR
            if index < self.phase or (self.phase == 5 and not self.failure):
                done = "concluído" if index == 0 else ("finalizado" if index == 5 else "concluída")
                return "✓", done, self.DONE
            if index == self.phase:
                return "●", "em curso", self.ACTIVE
        return "□", "aguardando", self.WAITING

    def render(self) -> Text:
        width = max(1, self.content_size.width)
        columns = self._columns(width)
        text = Text(no_wrap=True, overflow="crop")
        for start in range(0, 6, columns):
            if start:
                text.append("\n\n")
            available = width - (columns - 1)
            widths = [available // columns + (1 if column < available % columns else 0) for column in range(columns)]
            appearances = [self._appearance(index) for index in range(start, start + columns)]
            for column, (cell_width, (marker, _, color)) in enumerate(zip(widths, appearances)):
                if column:
                    text.append("─", self.WAITING)
                left = cell_width // 2
                right = cell_width - left - 1
                text.append((" " if column == 0 else "─") * left, self.WAITING)
                text.append(marker, color)
                text.append((" " if column == columns - 1 else "─") * right, self.WAITING)
            text.append("\n")
            for column, (index, cell_width, (_, _, color)) in enumerate(zip(range(start, start + columns), widths, appearances)):
                if column:
                    text.append(" ")
                text.append(self.LABELS[index].center(cell_width), color)
            text.append("\n")
            for column, (cell_width, (_, status, color)) in enumerate(zip(widths, appearances)):
                if column:
                    text.append(" ")
                text.append(status.center(cell_width), color)
        return text
