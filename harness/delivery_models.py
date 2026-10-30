"""Contrato operacional de entrega: instalar, compilar e iniciar em ambiente limpo."""
from typing import Literal
from pydantic import Field,model_validator
from .build_models import CommandSpec,Dependency
from .init_models import DocumentModel


class DeliveryCheck(DocumentModel):
    kind: Literal['build','typecheck']
    command: CommandSpec


class StartupProbe(DocumentModel):
    command: CommandSpec
    mode: Literal['exit','http']
    path: str = '/'
    status: int = Field(default=200,ge=100,le=599)
    default_port: int = Field(default=3000,ge=1,le=65535)
    expected_text: str | None = None
    expected_stdout: str | None = None


class ReadmeChange(DocumentModel):
    before: str = Field(min_length=1)
    after: str
    reason: str = Field(min_length=1)


class DeliveryRequirement(Dependency):
    manual_commands: list[CommandSpec] = Field(min_length=1)


class DeliveryPlan(DocumentModel):
    summary: str = Field(min_length=1)
    prerequisites: list[DeliveryRequirement] = Field(min_length=1)
    install: list[CommandSpec] = Field(max_length=4)
    checks: list[DeliveryCheck] = Field(max_length=3)
    startup: list[StartupProbe] = Field(min_length=1,max_length=2)
    readme_changes: list[ReadmeChange] = Field(default_factory=list)


class DeliveryDiagnosis(DocumentModel):
    kind: Literal['host_dependencies','project','procedure','review_context','transient']
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    dependencies: list[DeliveryRequirement]
    repair_files: list[str]
    instruction: str = Field(min_length=1)

    @model_validator(mode='after')
    def actionable(self):
        if self.kind=='host_dependencies' and not self.dependencies:raise ValueError('Liste os pacotes ausentes e seus checks/instaladores.')
        if self.kind=='project' and not self.repair_files:raise ValueError('Liste os arquivos exatos necessários para corrigir a instalação.')
        return self


class DeliveryRepairReview(DocumentModel):
    approved: bool
    needs_context: bool = False
    context_files: list[str] = Field(default_factory=list)
    preserves_functionality: bool
    preserves_validation: bool
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def preserves_contract(self):
        if self.approved and (self.needs_context or self.context_files):
            raise ValueError('Não aprovar e solicitar contexto ao mesmo tempo.')
        if self.context_files and not self.needs_context:
            raise ValueError('Uma solicitação de arquivos de contexto deve usar needs_context=true.')
        if self.approved and not (self.preserves_functionality and self.preserves_validation):
            raise ValueError('O reparo deve preservar o comportamento solicitado e sua comprovação funcional real.')
        return self
