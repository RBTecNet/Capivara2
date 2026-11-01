"""Configuração local simples: opções, auditor e segundos acumulados."""

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from capivara2ptn.workspace import ensure_harness, local_config_path, migrate_legacy
from capivara2ptn.models import PROVIDERS
from capivara2ptn.providers import EFFORTS, Selection


class ProjectConfigError(Exception):
    pass


@dataclass(frozen=True)
class ExecutionOptions:
    gates_enabled: bool = True
    forbid_automated_tests: bool = False


@dataclass(frozen=True)
class AuditorConfig:
    provider: str = "writer"
    model: str | None = None
    effort: str = "Default"

    def resolve(self, writer: Selection) -> Selection:
        if self.provider == "writer":
            return writer
        if self.model is None:
            raise ProjectConfigError("Selecione o modelo do auditor antes de executar com gates habilitados.")
        return Selection(self.provider, self.model, self.effort)


def _read(cwd: Path) -> dict:
    migrate_legacy(cwd)
    path = local_config_path(cwd)
    try:
        if path.is_symlink():
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
            key in data and not isinstance(data[key], bool)
            for key in ("gates_enabled", "forbid_automated_tests")
        ):
            raise ValueError
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ProjectConfigError("Configuração local inválida ou não legível: .harness/.capivara2ptn.json. Use valores booleanos para as opções.") from None


def read_options(cwd: Path) -> ExecutionOptions:
    data = _read(cwd)
    return ExecutionOptions(data.get("gates_enabled", True), data.get("forbid_automated_tests", False))


def save_options(cwd: Path, options: ExecutionOptions) -> None:
    data = _read(cwd)
    data.update(gates_enabled=options.gates_enabled, forbid_automated_tests=options.forbid_automated_tests)
    _write(cwd, data)


def _write(cwd: Path, data: dict) -> None:
    ensure_harness(cwd)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".capivara2ptn-", suffix=".tmp", dir=local_config_path(cwd).parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, local_config_path(cwd))
        temporary = None
    except OSError:
        raise ProjectConfigError("Não foi possível salvar a configuração em .harness/.capivara2ptn.json no diretório de trabalho.") from None
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _auditor(data: object) -> AuditorConfig:
    if (not isinstance(data, dict) or not isinstance(data.get("provider", "writer"), str)
            or data.get("provider", "writer") not in {"writer", *(value for _, value in PROVIDERS)}
            or data.get("effort", "Default") not in EFFORTS):
        raise ProjectConfigError("Configuração do auditor inválida em .harness/.capivara2ptn.json.")
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model or any(char.isspace() or char == "\x00" for char in model)):
        raise ProjectConfigError("Identificador de modelo do auditor inválido em .harness/.capivara2ptn.json.")
    return AuditorConfig(data.get("provider", "writer"), model, data.get("effort", "Default"))


def read_auditor(cwd: Path) -> AuditorConfig:
    return _auditor(_read(cwd).get("auditor", {}))


def save_auditor(cwd: Path, config: AuditorConfig) -> None:
    value = {"provider": config.provider, "model": config.model, "effort": config.effort}
    _auditor(value)
    data = _read(cwd)
    data["auditor"] = value
    _write(cwd, data)


def read_elapsed(cwd: Path) -> dict[str, float]:
    values = _read(cwd).get("elapsed_seconds", {})
    if not isinstance(values, dict) or any(
        name not in ("INIT", "PLAN", "BUILD") or type(value) not in (int, float)
        or not math.isfinite(value) or value < 0 for name, value in values.items()
    ):
        raise ProjectConfigError("Tempos acumulados inválidos em .harness/.capivara2ptn.json.")
    return {name: float(value) for name, value in values.items()}


def save_elapsed(cwd: Path, values: dict[str, float]) -> None:
    data = _read(cwd)
    data["elapsed_seconds"] = values
    _write(cwd, data)
