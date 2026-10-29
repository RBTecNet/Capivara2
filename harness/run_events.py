"""Metadados operacionais do init, sem prompts, respostas ou segredos."""

from dataclasses import dataclass


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
