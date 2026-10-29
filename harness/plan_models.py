"""Contratos do plano documental, sem código ou estratégias de testes."""

from typing import Literal

from pydantic import Field, model_validator

from .init_models import DocumentModel


class TaskSeed(DocumentModel):
    title: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    source_quotes: list[str] = Field(min_length=1)


class PhaseOutline(DocumentModel):
    tasks: list[TaskSeed] = Field(min_length=1)


class TaskContent(DocumentModel):
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    implementation_steps: list[str] = Field(min_length=1, max_length=6)
    deliverables: list[str] = Field(min_length=1, max_length=4)


class PlannedTask(DocumentModel):
    id: str = Field(pattern=r"^P\d+-T\d+$")
    phase_number: int = Field(ge=0)
    context_policy: Literal["fresh"] = "fresh"
    seed: TaskSeed
    content: TaskContent


class PlannedPhase(DocumentModel):
    number: int = Field(ge=0)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    depends_on: list[int]
    tasks: list[PlannedTask] = Field(min_length=1)


class TaskCorrection(DocumentModel):
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    replacement_seed: TaskSeed | None


class TaskAddition(DocumentModel):
    phase_number: int = Field(ge=0)
    after_task_id: str  # vazio insere no começo da fase
    task: TaskSeed


class PlanReview(DocumentModel):
    approved: bool
    matches_source: bool
    coverage_complete: bool
    tasks_are_small: bool
    no_test_strategy: bool
    summary: str = Field(min_length=1)
    corrections: list[TaskCorrection]
    additions: list[TaskAddition]

    @model_validator(mode="after")
    def consistent(self):
        checks = (self.matches_source, self.coverage_complete, self.tasks_are_small, self.no_test_strategy)
        if self.approved and (not all(checks) or self.corrections or self.additions):
            raise ValueError("Aprovar exige fidelidade, cobertura, tasks pequenas e nenhuma estratégia de testes ou pendência.")
        if not self.approved and not (self.corrections or self.additions):
            raise ValueError("Uma reprovação exige correções pontuais ou tasks ausentes, com orientação concreta.")
        if len({item.task_id for item in self.corrections}) != len(self.corrections):
            raise ValueError("Agrupe as correções de cada task em uma única orientação.")
        return self


class FinalPlan(DocumentModel):
    title: str
    objective: str
    source_prompt_sha256: str
    context_policy: Literal["fresh"] = "fresh"
    phases: list[PlannedPhase] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_structure(self):
        if [phase.number for phase in self.phases] != list(range(len(self.phases))):
            raise ValueError("Preserve fase 0 e todas as fases consecutivas do init.")
        ids = [task.id for phase in self.phases for task in phase.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Os IDs das tasks devem ser únicos.")
        for phase in self.phases:
            if any(task.phase_number != phase.number for task in phase.tasks):
                raise ValueError("Uma task deve pertencer à fase declarada.")
            if any(number < 0 or number >= phase.number for number in phase.depends_on):
                raise ValueError("Uma fase só depende de fases anteriores.")
        return self

    def render(self) -> str:
        sections = [f"# {self.title}", f"## Objetivo\n\n{self.objective}",
            f"Origem: prompt aprovado do init (SHA-256 `{self.source_prompt_sha256}`).",
            "## Contrato de execução futura\n\nCada task é uma unidade independente. O escritor e o revisor "
            "devem iniciar com contexto vazio em cada chamada; fornecer apenas a task e suas referências "
            "necessárias. A aplicação pertence à raiz do projeto; .harness armazena documentação e controle. "
            "Os testes de validação serão definidos pelo build."]
        for phase in self.phases:
            sections.append(f"## Fase {phase.number} — {phase.title}\n\n{phase.goal}\n\n"
                f"Dependências: {', '.join(f'fase {n}' for n in phase.depends_on) or 'nenhuma'}.")
            for task in phase.tasks:
                content = task.content
                sections.append(f"### {task.id} — {content.title}\n\n{content.objective}\n\n"
                    "Contexto: novo, sem histórico de outras tasks.\n\n#### Escopo\n\n" + task.seed.scope +
                    "\n\n#### Passos de implementação\n\n" + "\n".join(f"- {s}" for s in content.implementation_steps) +
                    "\n\n#### Entregáveis\n\n" + "\n".join(f"- {s}" for s in content.deliverables) +
                    "\n\n#### Referências da origem\n\n" + "\n".join(f"> {s.replace(chr(10), chr(10) + '> ')}" for s in task.seed.source_quotes))
        return "\n\n".join(sections) + "\n"
