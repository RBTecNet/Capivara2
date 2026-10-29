"""Contratos do plano documental, sem código ou estratégias de testes."""

from typing import Literal

from pydantic import Field, model_validator

from .init_models import DocumentModel


def agent_schema(model):
    """Schema estrito para agentes; defaults continuam aceitos na leitura de planos antigos."""
    schema = model.model_json_schema()
    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if "properties" in node:
                node["required"] = list(node["properties"])
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)
    strict(schema)
    return schema


class Requirement(DocumentModel):
    id: str
    phase_number: int
    kind: Literal["activity", "technology", "tool", "goal", "deliverable", "criterion"]
    text: str
    execution_owner: Literal["tasks", "build", "collective"] = "tasks"
    delegation_reason: str = ""


class CoverageClaim(DocumentModel):
    requirement_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class TaskSeed(DocumentModel):
    title: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    source_quotes: list[str] = Field(min_length=1)
    coverage: list[CoverageClaim] = Field(default_factory=list)


class ValidationDelegation(DocumentModel):
    requirement_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PhaseOutline(DocumentModel):
    tasks: list[TaskSeed]
    delegations: list[ValidationDelegation] = Field(default_factory=list)


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
    tasks: list[PlannedTask]


class TaskCorrection(DocumentModel):
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    replacement_seed: TaskSeed | None
    category: Literal["fidelity", "coverage", "granularity", "test_strategy", "structure"] = "fidelity"


class TaskAddition(DocumentModel):
    phase_number: int = Field(ge=0)
    after_task_id: str  # vazio insere no começo da fase
    task: TaskSeed


class StructuralChange(DocumentModel):
    operation: Literal["remove", "split", "merge"]
    task_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    replacements: list[TaskSeed]

    @model_validator(mode="after")
    def shape(self):
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("Não repita IDs numa operação estrutural.")
        if self.operation == "remove" and self.replacements:
            raise ValueError("Remoção não possui replacements.")
        if self.operation == "split" and (len(self.task_ids) != 1 or len(self.replacements) < 2):
            raise ValueError("Divisão substitui uma task por pelo menos duas.")
        if self.operation == "merge" and (len(self.task_ids) < 2 or len(self.replacements) != 1):
            raise ValueError("Consolidação substitui pelo menos duas tasks por uma.")
        return self


class ScopeDecision(DocumentModel):
    issue_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)


class PlanReview(DocumentModel):
    approved: bool
    matches_source: bool
    coverage_complete: bool
    tasks_are_small: bool
    no_test_strategy: bool
    summary: str = Field(min_length=1)
    corrections: list[TaskCorrection]
    additions: list[TaskAddition]
    structural_changes: list[StructuralChange] = Field(default_factory=list)
    delegations: list[ValidationDelegation] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        checks = (self.matches_source, self.coverage_complete, self.tasks_are_small, self.no_test_strategy)
        if self.approved and (not all(checks) or self.corrections or self.additions or self.structural_changes or self.delegations):
            raise ValueError("Aprovar exige fidelidade, cobertura, tasks pequenas e nenhuma estratégia de testes ou pendência.")
        if not self.approved and all(checks):
            raise ValueError("Se todos os critérios estão atendidos, aprove. Reprovações exigem um critério objetivo não atendido.")
        if not self.approved and not (self.corrections or self.additions or self.structural_changes or self.delegations):
            raise ValueError("Uma reprovação exige correções pontuais ou tasks ausentes, com orientação concreta.")
        if len({item.task_id for item in self.corrections}) != len(self.corrections):
            raise ValueError("Agrupe as correções de cada task em uma única orientação.")
        return self


class PlanRepair(DocumentModel):
    summary: str = Field(min_length=1)
    decisions: list[ScopeDecision]
    corrections: list[TaskCorrection]
    additions: list[TaskAddition]
    structural_changes: list[StructuralChange]
    delegations: list[ValidationDelegation] = Field(default_factory=list)

    @model_validator(mode="after")
    def actionable(self):
        if not any((self.decisions, self.corrections, self.additions, self.structural_changes, self.delegations)):
            raise ValueError("Reavaliação deve resolver pendências ou corrigir a estrutura; nunca aprova automaticamente.")
        return self


class FinalPlan(DocumentModel):
    title: str
    objective: str
    source_prompt_sha256: str
    context_policy: Literal["fresh"] = "fresh"
    phases: list[PlannedPhase] = Field(min_length=1)
    requirements: list[Requirement] = Field(default_factory=list)

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
            if not phase.tasks:
                sections.append("As atividades de validação desta fase permanecem sob responsabilidade do build; "
                                "o plan preserva suas referências sem gerar instruções de testes.")
            for task in phase.tasks:
                content = task.content
                sections.append(f"### {task.id} — {content.title}\n\n{content.objective}\n\n"
                    "Contexto: novo, sem histórico de outras tasks.\n\n#### Escopo\n\n" + task.seed.scope +
                    "\n\n#### Passos de implementação\n\n" + "\n".join(f"- {s}" for s in content.implementation_steps) +
                    "\n\n#### Entregáveis\n\n" + "\n".join(f"- {s}" for s in content.deliverables) +
                    "\n\n#### Referências da origem\n\n" + "\n".join(f"> {s.replace(chr(10), chr(10) + '> ')}" for s in task.seed.source_quotes))
                if task.seed.coverage:
                    sections.append("Responsabilidades: " + "; ".join(
                        f"{claim.requirement_id}: {claim.excerpt}" for claim in task.seed.coverage))
        return "\n\n".join(sections) + "\n"
