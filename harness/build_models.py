"""Contratos executáveis do build; revisões focadas na entrega de cada task."""

from typing import Literal

from pydantic import Field, model_validator

from .init_models import DocumentModel


class EnvironmentVariable(DocumentModel):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    value: str


class CommandSpec(DocumentModel):
    argv: list[str] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    timeout: int = Field(default=120, ge=1, le=600)
    env: list[EnvironmentVariable] = Field(default_factory=list)

    @model_validator(mode='after')
    def noninteractive(self):
        if any(not arg or '\0' in arg for arg in self.argv):
            raise ValueError('Argumentos de comando precisam ser explícitos e válidos.')
        if len({v.name for v in self.env}) != len(self.env):
            raise ValueError('Não repita nomes de variáveis de ambiente.')
        return self


class Dependency(DocumentModel):
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    check: CommandSpec
    install: list[CommandSpec]
    manual_instructions: str = Field(min_length=1)


class EnvironmentSpec(DocumentModel):
    dependencies: list[Dependency]


class TaskScope(DocumentModel):
    task_id: str = Field(min_length=1)
    kind: Literal['code', 'setup', 'documentation']
    writes: list[str] = Field(min_length=1)
    reads: list[str]
    depends_on: list[str]
    requirement_ids: list[str] = Field(default_factory=list)


class BuildBlueprint(DocumentModel):
    tasks: list[TaskScope]


class WriterResult(DocumentModel):
    summary: str = Field(min_length=1)
    files: list[str] = Field(min_length=1)


class CodeIssue(DocumentModel):
    file: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    required_change: str = Field(min_length=1)
    acceptance: str = Field(min_length=1)
    requirement_quote: str = Field(min_length=1)
    evidence_quote: str
    missing: bool


class FidelityReview(DocumentModel):
    approved: bool
    summary: str = Field(min_length=1)
    issues: list[CodeIssue]

    @model_validator(mode='after')
    def verdict(self):
        if self.approved == bool(self.issues):
            raise ValueError('Aprovação não tem pendências; rejeição exige problemas observáveis e correções específicas.')
        return self


class FunctionalCheck(DocumentModel):
    command: CommandSpec
    target_files: list[str] = Field(min_length=1)
    requirement_quote: str = Field(min_length=1)
    assertion_file: str | None = None
    expected_stdout: str | None = None


class VerificationSpec(DocumentModel):
    summary: str = Field(min_length=1)
    checks: list[FunctionalCheck] = Field(max_length=3)
    dependencies: list[Dependency] = Field(default_factory=list)


class BuildResult(DocumentModel):
    status: Literal['completed', 'blocked']
    summary: str
    artifact: str
