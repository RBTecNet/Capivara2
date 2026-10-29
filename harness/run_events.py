"""Metadados operacionais do init, sem prompts, respostas ou segredos."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanItem:
    id: str
    title: str
    kind: str
    creation: str = "waiting"
    validation: str = "waiting"


@dataclass(frozen=True)
class InitEvent:
    kind: str
    stage: str
    round: int
    active_role: str | None
    last_role: str | None
    operations: int
    invocations: int
    corrections: int
    questions: int
    answers: int
    inferences: int
    module: str = "init"
    phase_number: int | None = None
    phases_total: int = 0
    phases_approved: int = 0
    tasks_total: int = 0
    tasks_approved: int = 0
    items: tuple[PlanItem, ...] = ()
