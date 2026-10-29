"""Árvore rolável de fases e tasks: criação e validação por item."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import ProgressBar, Static

from .run_events import PlanItem


COLORS = {"waiting": "#7e939e", "running": "#ffbd38", "completed": "#a4d744", "rejected": "#f27f89"}


def indicator(state: str) -> Text:
    return Text("○" if state == "waiting" else "●", style=COLORS[state])


class PlanItemScroll(VerticalScroll):
    can_focus = True


class PlanProgress(Vertical):
    DEFAULT_CSS = """
    PlanProgress { height: 1fr; }
    PlanProgress #plan-creation-summary { height: 1; color: #b2c1c9; }
    PlanProgress ProgressBar { height: 1; margin-bottom: 1; width: 100%; }
    PlanProgress ProgressBar Bar { width: 1fr; }
    PlanProgress ProgressBar .bar--bar { color: #05bfd8; background: #243944; }
    PlanProgress ProgressBar .bar--complete { color: #a4d744; }
    PlanProgress #plan-item-heading { height: 1; color: #05bfd8; }
    PlanProgress #plan-item-scroll { height: 1fr; min-height: 4; scrollbar-size: 1 1; }
    PlanProgress #plan-item-scroll:focus { background: #10232e; }
    PlanProgress #plan-item-rows { height: auto; width: 1fr; }
    PlanProgress #plan-item-legend { height: 1; color: #7e939e; text-wrap: nowrap; text-overflow: ellipsis; }
    """

    def __init__(self):
        super().__init__(id="plan-progress")
        self.items: tuple[PlanItem, ...] = ()
        self.auto_scroll_y = 0

    def compose(self) -> ComposeResult:
        yield Static("Criação: aguardando as fases da origem", id="plan-creation-summary", markup=False)
        yield ProgressBar(total=1, show_eta=False, id="plan-creation-bar")
        yield Static("", id="plan-item-heading")
        with PlanItemScroll(id="plan-item-scroll"):
            yield Static("Aguardando o prompt aprovado do init…", id="plan-item-rows")
        yield Static("○ aguardando   ● em andamento   ● concluído   ● devolvido", id="plan-item-legend")

    def on_mount(self):
        self.query_one("#plan-item-heading").tooltip = "C = criação; V = validação. Duas bolinhas por fase e task. Role a lista para consultar os itens."
        legend = Text("○ aguardando  ", style=COLORS["waiting"])
        for label, state in (("em andamento", "running"), ("concluído", "completed"), ("devolvido", "rejected")):
            legend.append("● ", style=COLORS[state])
            legend.append(label + "  ", style=COLORS["waiting"])
        self.query_one("#plan-item-legend", Static).update(legend)
        self.call_after_refresh(self.render_rows)

    def on_resize(self, event: events.Resize):
        if self.is_mounted:
            self.call_after_refresh(self.render_rows)

    def update_items(self, items: tuple[PlanItem, ...]):
        scroll = self.query_one(PlanItemScroll)
        follow = abs(scroll.scroll_y - self.auto_scroll_y) < 1 or scroll.scroll_y >= scroll.max_scroll_y - 1
        self.items = items
        created = sum(item.creation == "completed" for item in items)
        total = len(items)
        self.query_one("#plan-creation-summary", Static).update(f"Criação: {created}/{total} itens identificados")
        self.query_one(ProgressBar).update(total=max(1, total), progress=created)
        self.render_rows()
        if follow:
            scroll.call_after_refresh(self.follow_active)

    def follow_active(self):
        scroll = self.query_one(PlanItemScroll)
        active = [i for i, item in enumerate(self.items) if "running" in (item.creation, item.validation)]
        changed = [i for i, item in enumerate(self.items) if item.creation != "waiting" or item.validation != "waiting"]
        index = active[-1] if active else changed[-1] if changed else 0
        height = scroll.scrollable_content_region.height
        scroll.scroll_to(y=max(0, index - height + 2), animate=False, immediate=True)
        self.auto_scroll_y = scroll.scroll_y

    def render_rows(self):
        if not self.query("#plan-item-rows"):
            return
        width = max(12, self.query_one("#plan-item-scroll").scrollable_content_region.width - 1)
        label_width = width - 7
        heading = Text("FASES E TASKS"[:label_width].ljust(label_width), style="#05bfd8")
        heading.append(" C   V", style="bold #05bfd8")
        self.query_one("#plan-item-heading", Static).update(heading)
        rows = Text(no_wrap=True, overflow="crop")
        for index, item in enumerate(self.items):
            if index:
                rows.append("\n")
            name = " ".join(item.title.split())
            label = f"{item.id} {name}" if item.kind == "phase" else f"  {item.id.split('-')[1]} {name}"
            title = Text(label, style="bold #05bfd8" if item.kind == "phase" else "#b2c1c9")
            title.truncate(label_width, overflow="ellipsis", pad=True)
            rows.append_text(title)
            rows.append(" ")
            rows.append_text(indicator(item.creation))
            rows.append("   ")
            rows.append_text(indicator(item.validation))
        self.query_one("#plan-item-rows", Static).update(rows)
