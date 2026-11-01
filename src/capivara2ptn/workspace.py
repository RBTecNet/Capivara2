"""Arquivos locais da ferramenta, migração de legado e exclusão confirmada."""

import json
import os
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path
from urllib.parse import unquote, urlsplit

INIT_FILES = {
    "prompt-original.txt", "entrevista.txt", "resposta.txt", "prompt-final.md",
    "g0-init.txt", "erro-init.txt", "init-concluido.json",
}
PLAN_FILES = {
    "plano.md", "plano-original.md", "plano-original-anterior.md",
    "plan-estado.json", "plan-erro.txt", "plan",
}


class WorkspaceError(Exception):
    pass


def harness_directory(cwd: Path) -> Path:
    return cwd / ".harness"


def data_directory(cwd: Path) -> Path:
    return harness_directory(cwd) / "dados"


def local_config_path(cwd: Path) -> Path:
    return harness_directory(cwd) / ".capivara2ptn.json"


def ensure_harness(cwd: Path) -> None:
    directory = harness_directory(cwd)
    if directory.is_symlink():
        raise WorkspaceError(".harness não pode ser um link.")
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        raise WorkspaceError("Não foi possível criar ou acessar .harness no diretório de trabalho.") from None
    if not directory.is_dir():
        raise WorkspaceError(".harness precisa ser um diretório.")


def _move(source: Path, target: Path) -> None:
    if source.is_symlink() or target.is_symlink():
        raise WorkspaceError("Migração interrompida: um arquivo da ferramenta é um link.")
    if target.exists():
        if source.is_file() and target.is_file() and source.read_bytes() == target.read_bytes():
            source.unlink()
            return
        if source.is_dir() and target.is_dir():
            for child in source.iterdir():
                _move(child, target / child.name)
            source.rmdir()
            return
        raise WorkspaceError("Existem arquivos diferentes no legado e em .harness. Nenhum arquivo conflitante foi sobrescrito.")
    os.replace(source, target)


def migrate_legacy(cwd: Path) -> None:
    """Move somente artefatos reconhecidos, mantendo dados alheios da aplicação."""
    if harness_directory(cwd).is_symlink():
        raise WorkspaceError(".harness não pode ser um link.")
    old_config = cwd / ".capivara2ptn.json"
    old_data = cwd / "dados"
    owned = old_data.is_dir() and (
        (old_data / "plan-estado.json").exists() or (old_data / "init-concluido.json").exists()
        or all((old_data / name).is_file() for name in ("prompt-original.txt", "entrevista.txt", "resposta.txt"))
    )
    try:
        if old_config.exists() or old_config.is_symlink():
            ensure_harness(cwd)
            _move(old_config, local_config_path(cwd))
        if owned:
            if old_data.is_symlink():
                raise WorkspaceError("O diretório legado dados/ não pode ser um link.")
            ensure_harness(cwd)
            target = data_directory(cwd)
            known = INIT_FILES | PLAN_FILES
            if not target.exists() and all(child.name in known for child in old_data.iterdir()):
                _move(old_data, target)
            else:
                if target.is_symlink():
                    raise WorkspaceError(".harness/dados não pode ser um link.")
                target.mkdir(mode=0o700, exist_ok=True)
                for name in known:
                    source = old_data / name
                    if source.exists() or source.is_symlink():
                        _move(source, target / name)
                if not any(old_data.iterdir()):
                    old_data.rmdir()
    except OSError:
        raise WorkspaceError("Não foi possível migrar os arquivos para .harness. Verifique permissões e conflitos; nada foi sobrescrito.") from None


def ensure_project_destination(cwd: Path) -> None:
    protected = [Path(__file__).resolve().parent]
    if sys.prefix != sys.base_prefix:
        protected.append(Path(sys.prefix).resolve())
    try:
        source = urlsplit(json.loads(distribution("capivara2ptn").read_text("direct_url.json") or "{}").get("url", ""))
        if source.scheme == "file":
            protected.append(Path(unquote(source.path)).resolve())
    except (OSError, ValueError):
        pass
    if any(cwd.resolve().is_relative_to(path) for path in protected):
        raise WorkspaceError("Escolha um projeto fora da instalação e do código-fonte da CAPIVARA2PTN.")


def reset_stage(cwd: Path, stage: str) -> None:
    """Chamado somente após confirmação; nunca remove arquivos do produto."""
    ensure_project_destination(cwd)
    directory = data_directory(cwd)
    if harness_directory(cwd).is_symlink() or directory.is_symlink():
        raise WorkspaceError("Não é seguro refazer a fase por diretórios que sejam links.")
    if stage not in ("init", "plan", "build"):
        raise WorkspaceError("Essa fase não pode ser refeita nesta versão.")
    try:
        if directory.exists():
            for path in directory.iterdir():
                if stage == "plan" and path.name in INIT_FILES:
                    continue
                if stage == "build" and path.name in INIT_FILES | PLAN_FILES:
                    continue
                if path.is_symlink() or not path.is_dir():
                    path.unlink()
                else:
                    shutil.rmtree(path)
    except OSError:
        raise WorkspaceError("Não foi possível excluir todos os resultados da fase confirmada. Verifique .harness/dados antes de continuar.") from None
