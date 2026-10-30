"""Contratos por papel e condições documentais verificáveis de uma correção."""

import json
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import InitError, digest
from .init_models import DocumentModel
from .plan_models import (StructuralChange, TaskAddition, TaskContent,
                          TaskCorrection, PlanReview, ScopeDecision, TaskSeed)


class PlanContractError(InitError):
    """Inconsistência não pode virar tentativas repetidas nem aprovação implícita."""


class SealedOutline(DocumentModel):
    tasks: list[TaskSeed]


class ActivityPart(DocumentModel):
    text: str = Field(min_length=1)
    responsibility: Literal['implementation', 'documentation', 'build']
    reason: str = Field(min_length=1)


class ClassifiedActivity(DocumentModel):
    source_id: str = Field(min_length=1)
    parts: list[ActivityPart] = Field(min_length=1)


class WorkClassification(DocumentModel):
    activities: list[ClassifiedActivity]


class ResponsibilityChange(DocumentModel):
    source_id: str
    part_index: int = Field(ge=0)
    responsibility: Literal['implementation', 'documentation', 'build']
    reason: str = Field(min_length=1)


class ClassificationReview(DocumentModel):
    status: Literal['approved', 'corrected', 'conflict']
    summary: str = Field(min_length=1)
    corrections: list[ResponsibilityChange]

    @model_validator(mode='after')
    def consistent(self):
        if (self.status == 'corrected') != bool(self.corrections):
            raise ValueError('corrected aprova o mapa com mudanças de responsabilidade; approved/conflict não possuem mudanças.')
        return self

class FieldChange(DocumentModel):
    field: Literal['title', 'objective', 'implementation_steps', 'deliverables']
    before: str | list[str]
    after: str | list[str]

    @model_validator(mode='after')
    def typed_values(self):
        expected = list if self.field in ('implementation_steps', 'deliverables') else str
        if not isinstance(self.before, expected) or not isinstance(self.after, expected):
            raise ValueError('before e after precisam ter o tipo do campo: texto ou lista de textos.')
        if self.before == self.after:
            raise ValueError('Uma alteração precisa corrigir efetivamente o campo.')
        return self


class AcceptanceCondition(DocumentModel):
    field: Literal['title', 'objective', 'implementation_steps', 'deliverables']
    operator: Literal['contains', 'excludes']
    value: str = Field(min_length=1)

    def satisfied(self, content):
        value = getattr(content, self.field)
        text = '\n'.join(value) if isinstance(value, list) else value
        found = self.value in text
        return found if self.operator == 'contains' else not found


class SourceEvidence(DocumentModel):
    requirement_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class ContentCorrection(DocumentModel):
    task_id: str = Field(min_length=1)
    base_version: str = Field(min_length=1)
    category: Literal['fidelity', 'coverage', 'test_strategy']
    reason: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)
    changes: list[FieldChange] = Field(min_length=1)
    acceptance: list[AcceptanceCondition] = Field(min_length=1)

    @model_validator(mode='after')
    def specific(self):
        fields = [change.field for change in self.changes]
        if len(fields) != len(set(fields)):
            raise ValueError('Agrupe as mudanças de cada campo numa única substituição.')
        if set(fields) != {condition.field for condition in self.acceptance}:
            raise ValueError('Cada campo alterado precisa de condição de aceite; não avalie campos fora da correção.')
        return self

    def legacy(self):
        return TaskCorrection(task_id=self.task_id, category=self.category, reason=self.reason,
            instruction=self.instruction, replacement_seed=None, contract=self.model_dump())


class StructureConcern(DocumentModel):
    task_id: str
    reason: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class ReviewChecks(DocumentModel):
    approved: bool
    matches_source: bool
    coverage_complete: bool
    tasks_are_small: bool
    no_test_strategy: bool
    summary: str = Field(min_length=1)
    corrections: list[ContentCorrection]

    def validate_actions(self, actions):
        checks = (self.matches_source, self.coverage_complete, self.tasks_are_small, self.no_test_strategy)
        if self.approved and (not all(checks) or actions):
            raise ValueError('Aprovar exige todos os critérios verdadeiros e nenhuma pendência.')
        if not self.approved and (all(checks) or not actions):
            raise ValueError('Reprovar exige critério objetivo falso e uma ação concreta; não reprovar por preferência editorial.')

    def legacy(self, **extra):
        return PlanReview(**self.model_dump(exclude={'corrections', 'replan', 'structural_changes', 'additions'}),
            corrections=[c.legacy() for c in self.corrections], **extra)


class TaskAssessment(ReviewChecks):
    replan: StructureConcern | None

    @model_validator(mode='after')
    def valid_actions(self):
        self.validate_actions(bool(self.corrections or self.replan))
        if len(self.corrections) > 1 or (self.corrections and self.replan):
            raise ValueError('Agrupe todos os defeitos de conteúdo num contrato único, ou solicite reparo estrutural.')
        return self


class CollectiveAssessment(ReviewChecks):
    structural_changes: list[StructuralChange]
    additions: list[TaskAddition]

    @model_validator(mode='after')
    def valid_actions(self):
        self.validate_actions(bool(self.corrections or self.structural_changes or self.additions))
        if len({c.task_id for c in self.corrections}) != len(self.corrections):
            raise ValueError('Um contrato completo por task, reunindo todos os defeitos encontrados.')
        return self


class StructureRepair(DocumentModel):
    summary: str = Field(min_length=1)
    structural_changes: list[StructuralChange]
    additions: list[TaskAddition]
    decisions: list[ScopeDecision]

    @model_validator(mode='after')
    def concrete(self):
        if not (self.structural_changes or self.additions or self.decisions):
            raise ValueError('Resolva o conflito com operação concreta ou decisão de escopo; não o encaminhe de volta ao escritor.')
        return self


class ContractValidation(DocumentModel):
    approved: bool
    reason: str = Field(min_length=1)


def correction_id(correction):
    key = (correction.task_id, correction.category,
           sorted({e.requirement_id for e in correction.evidence}), sorted(c.field for c in correction.changes))
    return 'C-' + digest(json.dumps(key, ensure_ascii=False))[:16]


def patched_content(task, correction, requirements):
    from .plan_control import version
    if correction.task_id != task.id or correction.base_version != version(task):
        raise ValueError('O contrato deve apontar a task e base_version atuais fornecidos pelo harness.')
    assigned = {claim.requirement_id for claim in task.seed.coverage}
    if not any(e.requirement_id in assigned for e in correction.evidence):
        raise ValueError('Cite ao menos um requisito atribuído à task para delimitar a correção.')
    for evidence in correction.evidence:
        requirement = requirements.get(evidence.requirement_id)
        contextual = requirement is not None and requirement.phase_number == -1
        if (evidence.requirement_id not in assigned and not contextual) or requirement is None or evidence.quote not in requirement.text:
            raise ValueError('A evidência deve citar literalmente um requisito atribuído a esta task.')
    data = task.content.model_dump()
    for change in correction.changes:
        if data[change.field] != change.before:
            raise ValueError(f'before de {change.field} não corresponde ao documento atual. Copie seu valor exato.')
        data[change.field] = change.after
    target = TaskContent.model_validate(data)
    for condition in correction.acceptance:
        if not condition.satisfied(target):
            raise ValueError('A mudança proposta não satisfaz sua própria condição de aceite.')
        if condition.satisfied(task.content):
            raise ValueError('A condição já está satisfeita no documento atual: não justifica esta devolução.')
    return target
