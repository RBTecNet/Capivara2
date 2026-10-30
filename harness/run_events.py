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
class BuildItem:
    id: str
    title: str
    kind: str
    gates: tuple[str, ...] = ("waiting",) * 4
    status: str = "waiting"


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
    contract_errors: int = 0
    writer_recoveries: int = 0
    build_items: tuple[BuildItem, ...] = ()
    active_agents: int = 0
    max_agents: int = 1
    packages_total: int = 0
    packages_ready: int = 0
    task_id: str | None = None
    active_writers: int = 0
    active_reviewers: int = 0
    environment_status: str = 'waiting'
    environment_total: int = 0
    environment_ready: int = 0
    scopes_total: int = 0
    scopes_ready: int = 0
