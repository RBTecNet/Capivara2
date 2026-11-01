"""Cronômetro monotônico; persiste apenas segundos, nunca tempo de pausa."""

from pathlib import Path
from time import monotonic

from capivara2ptn.project_config import read_elapsed, save_elapsed

MODULES = ("INIT", "PLAN", "BUILD")


def display_time(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class ModuleClock:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.totals = read_elapsed(cwd)
        self.active: str | None = None
        self.started = 0.0

    def elapsed(self, module: str) -> float:
        return self.totals.get(module, 0.0) + (monotonic() - self.started if self.active == module else 0.0)

    def start(self, module: str) -> None:
        if self.active is not None:
            raise RuntimeError("Já existe um cronômetro em execução.")
        if module not in MODULES:
            raise ValueError("Módulo desconhecido.")
        self.totals = read_elapsed(self.cwd)
        self.active, self.started = module, monotonic()

    def checkpoint(self) -> None:
        if self.active:
            save_elapsed(self.cwd, {**self.totals, self.active: self.elapsed(self.active)})

    def stop(self, module: str) -> None:
        if self.active == module:
            self.totals[module] = self.elapsed(module)
            self.active = None
            save_elapsed(self.cwd, self.totals)

    def reset_from(self, module: str) -> None:
        values = read_elapsed(self.cwd)
        for name in MODULES[MODULES.index(module):]:
            values.pop(name, None)
        save_elapsed(self.cwd, values)
        self.totals = values
