"""Requisitos de entrega declarados como tasks do PLAN, sem novos gates."""

import json
import platform
import re
import shutil
from pathlib import Path

from capivara2ptn.plan_markdown import PlanFormatError

ENVIRONMENT_TITLE = "Preparar ambiente de desenvolvimento"
README_TITLE = "Documentar instalação e uso"


def host_context() -> dict:
    info = {"sistema": platform.system(), "python_do_harness": platform.python_version(),
            "executaveis_python": {name: shutil.which(name) for name in ("python", "python3")}}
    if info["sistema"] == "Linux":
        try:
            release = platform.freedesktop_os_release()
            info["distribuicao"] = {key: release.get(key, "") for key in ("ID", "ID_LIKE", "VERSION_ID")}
        except OSError:
            pass
    return info


def _commands(value) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) or set(item) != {"diretorio", "comando"}
        or any(not isinstance(v, str) or not v.strip() or "\x00" in v for v in item.values())
        for item in value
    ):
        raise ValueError


def configure_delivery_tasks(phases: list[dict], *, required: bool = False) -> None:
    tasks = [task for phase in phases for task in phase["threads"]]
    environment, documentation = [], []
    for index, task in enumerate(tasks):
        task.pop("environment_check", None)
        task.pop("readme_delivery", None)
        title = " ".join(task["title"].casefold().split())
        blocks = re.findall(r"(?ms)^\s*```preparacao-ambiente[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*\r?$", task["description"])
        if title == ENVIRONMENT_TITLE.casefold() or blocks:
            if index != 0 or title != ENVIRONMENT_TITLE.casefold() or len(blocks) != 1:
                raise PlanFormatError("A primeira task deve ser Preparar ambiente de desenvolvimento, com um bloco preparacao-ambiente.")
            try:
                config = json.loads(blocks[0])
                if not isinstance(config, dict) or set(config) != {"verificacoes", "instalacoes", "nao_aplicavel"}:
                    raise ValueError
                _commands(config["verificacoes"])
                _commands(config["instalacoes"])
                reason = config["nao_aplicavel"]
                if config["verificacoes"]:
                    if reason is not None:
                        raise ValueError
                elif not isinstance(reason, str) or not reason.strip() or config["instalacoes"]:
                    raise ValueError
            except (ValueError, TypeError):
                raise PlanFormatError("Preparação: declare verificacoes/instalacoes com diretorio e comando, ou nao_aplicavel explícito. Dependências e instalações não serão inventadas pelo BUILD.") from None
            task["environment_check"] = config
            environment.append(task)
        if title == README_TITLE.casefold():
            if required and (index != len(tasks) - 2 or "README.md" not in task["description"]):
                raise PlanFormatError("A penúltima task deve ser Documentar instalação e uso, incluindo README.md na raiz.")
            task["readme_delivery"] = True
            documentation.append(task)
    if required and (len(environment) != 1 or len(documentation) != 1):
        raise PlanFormatError("O PLAN deve incluir a preparação do ambiente na primeira task e a entrega do README.md antes da verificação técnica final.")


def readme_ready(cwd: Path) -> bool:
    path = cwd / "README.md"
    try:
        return path.is_file() and not path.is_symlink() and bool(path.read_text(encoding="utf-8-sig").strip())
    except (OSError, UnicodeError):
        return False
