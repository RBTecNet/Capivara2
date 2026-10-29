"""Contratos documentais usados pelo revisor, escritor e entrevista."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Gap(DocumentModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    topic: Literal["stack", "tools", "requirements"]


class TechnologyEvidence(DocumentModel):
    stack_specified: bool
    stack_quote: str
    tools_specified: bool
    tools_quote: str


class Review(DocumentModel):
    summary: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)
    matches_original: bool
    feasible: bool
    phases_valid: bool
    approved: bool
    technology: TechnologyEvidence
    gaps: list[Gap]
    deviations: list[str]
    infeasibilities: list[str]

    @model_validator(mode="after")
    def consistent_verdict(self):
        if len({gap.id for gap in self.gaps}) != len(self.gaps):
            raise ValueError("Cada lacuna precisa de um identificador único.")
        if not self.matches_original and not self.deviations:
            raise ValueError("Justifique cada desvio do pedido original em deviations.")
        if not self.feasible and not self.infeasibilities:
            raise ValueError("Justifique a impossibilidade e oriente a correção em infeasibilities.")
        if self.approved and (not self.matches_original or not self.feasible or not self.phases_valid
                              or self.gaps or self.deviations or self.infeasibilities):
            raise ValueError("Aprovação exige fidelidade, viabilidade, fases válidas e nenhuma pendência.")
        return self


class InterviewOption(DocumentModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Question(DocumentModel):
    id: str = Field(min_length=1)
    gap_ids: list[str] = Field(min_length=1)
    topic: Literal["stack", "tools", "requirements"]
    question: str = Field(min_length=1)
    options: list[InterviewOption] = Field(min_length=2, max_length=5)
    recommended_option_id: str = Field(min_length=1)
    recommendation_reason: str = Field(min_length=1)
    goal_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def choice_contract(self):
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids) or any(option_id in ("other", "free_text") for option_id in ids):
            raise ValueError("Opções precisam ter IDs únicos; a única opção livre é adicionada pelo harness.")
        if self.recommended_option_id not in ids:
            raise ValueError("Marque exatamente uma opção existente como recomendada.")
        return self


class InferredDecision(DocumentModel):
    gap_ids: list[str] = Field(min_length=1)
    scope: Literal["harness"]
    decision: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Interview(DocumentModel):
    questions: list[Question]
    inferred_decisions: list[InferredDecision]

    @model_validator(mode="after")
    def unique_questions(self):
        if not self.questions and not self.inferred_decisions:
            raise ValueError("Resolva as lacunas com perguntas do produto ou decisões internas inferidas.")
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("Perguntas precisam ter identificadores únicos.")
        return self


class Answer(DocumentModel):
    question_id: str
    option_id: str
    label: str
    text: str


class StackItem(DocumentModel):
    component: str = Field(min_length=1)
    choice: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Tool(DocumentModel):
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    scope: Literal["project", "harness"]


class Phase(DocumentModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    depends_on: list[int]


class FinalPrompt(DocumentModel):
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)
    constraints: list[str]
    stack: list[StackItem] = Field(min_length=1)
    tools: list[Tool] = Field(min_length=1)
    phases: list[Phase] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def executable_phases(self):
        if [phase.number for phase in self.phases] != list(range(1, len(self.phases) + 1)):
            raise ValueError("As fases de implementação devem ser consecutivas, começando em 1; a fase 0 é criada pelo harness.")
        if any(dependency < 0 or dependency >= phase.number
               for phase in self.phases for dependency in phase.depends_on):
            raise ValueError("Uma fase só pode depender de fases anteriores.")
        if not any(tool.scope == "project" for tool in self.tools):
            raise ValueError("Especifique as ferramentas necessárias ao projeto.")
        for values in (self.requirements, self.constraints, self.acceptance_criteria,
                       *(items for phase in self.phases for items in (phase.tasks, phase.deliverables, phase.acceptance_criteria))):
            if any(not value.strip() for value in values):
                raise ValueError("Não deixe itens documentais vazios.")
        return self

    def render(self) -> str:
        def bullets(values):
            return "\n".join(f"- {value}" for value in values)

        sections = [f"# {self.title}", f"## Objetivo\n\n{self.objective}",
                    f"## Requisitos\n\n{bullets(self.requirements)}"]
        if self.constraints:
            sections.append(f"## Restrições e decisões\n\n{bullets(self.constraints)}")
        sections.append("## Fase 0 — Stack e ferramentas\n\n### Stack do projeto\n\n" +
                        bullets(f"**{item.component}: {item.choice}.** {item.reason}" for item in self.stack))
        for scope, heading in (("project", "Ferramentas do projeto"), ("harness", "Ferramentas internas do harness (inferidas)")):
            tools = [f"**{tool.name}:** {tool.purpose}" for tool in self.tools if tool.scope == scope]
            if tools:
                sections.append(f"### {heading}\n\n{bullets(tools)}")
        for phase in self.phases:
            sections.append(f"## Fase {phase.number} — {phase.title}\n\n{phase.goal}\n\n"
                            f"Dependências: {', '.join(f'fase {number}' for number in phase.depends_on) or 'nenhuma'}.\n\n"
                            f"### Atividades\n\n{bullets(phase.tasks)}\n\n"
                            f"### Entregáveis\n\n{bullets(phase.deliverables)}\n\n"
                            f"### Critérios de aceite\n\n{bullets(phase.acceptance_criteria)}")
        sections.append(f"## Critérios de conclusão do projeto\n\n{bullets(self.acceptance_criteria)}")
        return "\n\n".join(sections) + "\n"
