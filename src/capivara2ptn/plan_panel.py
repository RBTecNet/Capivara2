"""Painel rolável de fases/threads; a pulsação é apenas animação visual."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from capivara2ptn.plan_state import phase_status


class PlanPanel(VerticalScroll):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state: dict | None = None
        self.active = False
        self._bright = True
        self._timer = None
        self._last_active = None

    def compose(self) -> ComposeResult:
        yield Static("Nenhuma execução salva para este módulo.", id="plan-content", markup=False)

    def on_mount(self) -> None:
        self.border_title = "Fases e threads"
        self._timer = self.set_interval(0.45, self.pulse, pause=True)

    def show(self, state: dict | None, active: bool = False) -> None:
        self.state, self.active = state, active
        if self._timer:
            self._timer.resume() if active else self._timer.pause()
        self.draw()

    def pulse(self) -> None:
        if self.active:
            self._bright = not self._bright
            self.draw()

    def color(self, status: str) -> str:
        if status == "running":
            return ("#ffbf3f" if self._bright else "#96701f") if self.active else "#ff8e8e"
        return {"pending": "#688398", "done": "#93d6b4", "failed": "#ff8e8e"}.get(status, "#688398")

    def draw(self) -> None:
        text = Text()
        active_key = None
        active_offset = 0
        state = self.state
        if state is None:
            text.append("Nenhuma execução salva para este módulo.", style="#92a5b8")
        else:
            if not state["phases"]:
                if self.active and state["status"] == "writing":
                    text.append("● ", self.color("running"))
                    text.append("Escritor definindo fases e threads…\n")
                else:
                    text.append("Ainda não há fases identificadas.\n", style="#92a5b8")
            if not self.active and state["status"] in ("writing", "defining", "validating", "correcting", "executing", "retrying"):
                text.append("Execução anterior interrompida. Pressione Iniciar para retomar.\n\n", style="#ff8e8e")
            for phase in state["phases"]:
                status = phase_status(phase, state["gates_enabled"])
                if state.get("module") == "BUILD" and self.active and phase.get("started") and status != "done":
                    status = "running"
                text.append("● ", self.color(status))
                text.append(f"Fase {phase['id']} — {phase['title']}\n")
                for thread in phase["threads"]:
                    gates = state["gates_enabled"] and thread.get("gates_enabled", True)
                    roles = (["g0", "writer", "g1", "g2"] if state.get("module") == "BUILD" else ["writer", "g0"]) if gates else ["writer"]
                    statuses = {role: ("running" if role == "writer" and self.active and (thread.get("technical_pending") or thread.get("environment_pending")) else thread[role]) for role in roles}
                    running = next((role for role in roles if statuses[role] == "running"), None)
                    if self.active and running:
                        active_key = (thread["id"], running)
                        active_offset = len(text)
                    text.append("   ")
                    for role in roles:
                        text.append("● ", self.color(statuses[role]))
                        text.append("Escritor " if role == "writer" else role.upper() + " ")
                    text.append(f"· Task {thread['id']} — {thread['title']}\n")
                    if thread.get("error"):
                        text.append("      " + thread["error"] + "\n", style="#ff8e8e")
                text.append("\n")
            if state.get("error"):
                text.append(state["error"], style="#ff8e8e")
        self.query_one("#plan-content", Static).update(text)
        if active_key is not None and active_key != self._last_active:
            width = max(1, self.query_one("#plan-content").content_size.width)
            line = len(text[:active_offset].wrap(self.app.console, width))
            self.call_after_refresh(self.scroll_to, y=max(0, line - 4), animate=False)
        self._last_active = active_key
