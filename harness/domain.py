"""Dados do wizard, sem execução de agentes ou da função init."""

from dataclasses import dataclass
from pathlib import Path


PROVIDERS = {"codex": "Codex", "claude": "Claude", "opencode": "OpenCode", "agy": "agy"}


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    efforts: tuple[str, ...]


@dataclass(frozen=True)
class AgentSelection:
    provider: str
    model: str
    effort: str


@dataclass
class WizardState:
    project_dir: Path
    prompt_source: str = "manual"
    prompt: str = ""
    prompt_file: Path | None = None
    writer: AgentSelection | None = None
    reviewer: AgentSelection | None = None
    reuse_reviewer: bool = True
    execute_requested: bool = False


def resolve_project(value: str, launch_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = launch_dir / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError("Informe uma pasta de projeto existente.")
    return path


def read_prompt_file(value: str, project_dir: Path) -> tuple[Path, str]:
    if not value.strip():
        raise ValueError("Informe o caminho do arquivo de prompt.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("O arquivo precisa estar em UTF-8.") from exc
    except OSError as exc:
        raise ValueError(f"Não foi possível ler o arquivo: {exc.strerror or str(exc)}.") from exc
    if not text.strip():
        raise ValueError("O arquivo de prompt está vazio.")
    return path, text
