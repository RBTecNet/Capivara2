"""Instruções editáveis fora do Python, lidas novamente em cada chamada."""

import os
from importlib.resources import files
from pathlib import Path


class PromptError(Exception):
    pass


def prompt_directory() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        base = Path.home() / ".config"
    return base / "capivara2ptn" / "prompts"


def install_defaults() -> None:
    """Primeiro uso apenas: não restaura arquivos removidos nem sobrescreve edições."""
    directory = prompt_directory()
    try:
        for marker_name, names in (
            (".init-prompts-installed", ("init.md", "init-final.md", "gates/g0-init.md")),
            (".plan-prompts-installed", ("plan.md", "gates/g0-plan.md")),
            (".build-prompts-installed", ("BUILD.md", "gates/g0-build.md", "G2.md")),
        ):
            marker = directory.parent / marker_name
            if marker.exists():
                continue
            for name in names:
                target = directory / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    with target.open("x", encoding="utf-8") as stream:
                        stream.write(files("capivara2ptn").joinpath("prompts", name).read_text(encoding="utf-8"))
                except FileExistsError:
                    pass
            marker.touch(exist_ok=True)
    except OSError:
        raise PromptError(f"Não foi possível preparar os prompts editáveis em {directory}.") from None


def load_prompt(name: str) -> str:
    path = prompt_directory() / name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise PromptError(f"Prompt ausente ou não legível: {path}. A etapa foi interrompida.") from None
    if not text.strip():
        raise PromptError(f"Prompt vazio: {path}. A etapa foi interrompida.")
    return text
