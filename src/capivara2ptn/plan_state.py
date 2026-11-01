"""Arquivos de planejamento e registro local de progresso."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from capivara2ptn.workspace import data_directory, migrate_legacy

class PlanError(Exception):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text(path: Path, text: str) -> None:
    temporary = None
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise OSError
        descriptor, temporary = tempfile.mkstemp(prefix=".plan-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise PlanError(f"Não foi possível salvar {path.name}; arquivos anteriores preservados.") from None
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def record_init_completion(cwd: Path, prd: Path, gates_enabled: bool) -> None:
    write_json(data_directory(cwd) / "init-concluido.json", {"version": 1, "completed": True, "prd_sha256": digest(prd.read_text(encoding="utf-8")), "gates_enabled": gates_enabled})


def read_init_prd(cwd: Path) -> str:
    migrate_legacy(cwd)
    directory = data_directory(cwd)
    try:
        if directory.is_symlink():
            raise ValueError
        prd = (directory / "prompt-final.md").read_text(encoding="utf-8-sig")
        if not prd.strip():
            raise ValueError
        receipt = directory / "init-concluido.json"
        if receipt.exists():
            data = json.loads(receipt.read_text(encoding="utf-8"))
            if data.get("version") != 1 or data.get("completed") is not True or data.get("prd_sha256") != digest(prd):
                raise ValueError
        else:
            # Compatibilidade com os INITs anteriores ao registro de conclusão.
            if (directory / "erro-init.txt").exists() or not (directory / "prompt-original.txt").read_text(encoding="utf-8").strip():
                raise ValueError
            if not (directory / "entrevista.txt").is_file() or (directory / "resposta.txt").read_text(encoding="utf-8").strip().upper() != "CONCLUIDO":
                raise ValueError
            g0 = directory / "g0-init.txt"
            if g0.exists():
                if g0.read_text(encoding="utf-8").strip() != "G0 APROVADO":
                    raise ValueError
        return prd
    except (OSError, ValueError, AttributeError):
        raise PlanError("PLAN exige um PRD final de INIT concluído em .harness/dados/prompt-final.md. INIT incompleto, reprovado ou PRD alterado não pode ser planejado.") from None


def threads(state: dict):
    for phase in state.get("phases", []):
        yield from phase["threads"]


def thread_status(thread: dict, gates: bool) -> str:
    if thread.get("technical_pending") or thread.get("environment_pending"):
        return "running"
    gates = gates and thread.get("gates_enabled", True)
    values = [thread["writer"]] + ([thread[role] for role in ("g0", "g1", "g2") if role in thread] if gates else [])
    if "running" in values:
        return "running"
    if "failed" in values:
        return "failed"
    if all(value == "done" for value in values):
        return "done"
    return "running" if "running" in values else "pending"


def phase_status(phase: dict, gates: bool) -> str:
    values = [thread_status(thread, gates) for thread in phase["threads"]]
    if "running" in values:
        return "running"
    if "failed" in values:
        return "failed"
    if values and all(value == "done" for value in values):
        return "done"
    return "running" if "running" in values else "pending"


def counts(state: dict) -> tuple[int, int, int, int]:
    gates = state["gates_enabled"]
    entries = list(threads(state))
    phases = state["phases"]
    return (sum(thread_status(thread, gates) == "done" for thread in entries), len(entries),
            sum(phase_status(phase, gates) == "done" for phase in phases), len(phases))


def load_plan_state(cwd: Path, *, module: str = "PLAN") -> dict | None:
    filename = "build-estado.json" if module == "BUILD" else "plan-estado.json"
    try:
        migrate_legacy(cwd)
        data = json.loads((data_directory(cwd) / filename).read_text(encoding="utf-8"))
        if (data.get("version") != 1 or not isinstance(data.get("gates_enabled"), bool) or not isinstance(data.get("phases"), list)
                or data.get("status") not in ("writing", "defining", "validating", "correcting", "executing", "retrying", "done", "failed", "interrupted") or not isinstance(data.get("error", ""), str)):
            raise ValueError
        for phase in data["phases"]:
            if not isinstance(phase.get("id"), str) or not isinstance(phase.get("title"), str) or not isinstance(phase.get("threads"), list):
                raise ValueError
            for thread in phase["threads"]:
                if not isinstance(thread.get("id"), str) or not isinstance(thread.get("title"), str) or not isinstance(thread.get("error", ""), str) or thread.get("writer") not in ("pending", "running", "done", "failed"):
                    raise ValueError
                if data["gates_enabled"] and thread.get("g0") not in ("pending", "running", "done", "failed"):
                    raise ValueError
                if module == "BUILD":
                    if data.get("module") != "BUILD" or any(thread.get(role) not in ("pending", "running", "done", "failed") for role in ("g1", "g2")):
                        raise ValueError
                    if thread.get("operation") not in ("g0", "preparation", "writer", "technical", "environment", "g1", "g2", "done"):
                        raise ValueError
                for key in ("g0_attempts", "correction_attempts", "revision"):
                    if key in thread and (type(thread[key]) is not int or thread[key] < 0):
                        raise ValueError
                if "needs_correction" in thread and not isinstance(thread["needs_correction"], bool):
                    raise ValueError
                if "last_rejection" in thread and not isinstance(thread["last_rejection"], str):
                    raise ValueError
                if thread.get("pending_correction"):
                    pending = thread["pending_correction"]
                    if not isinstance(pending, dict) or pending.get("task", {}).get("id") != thread["id"]:
                        raise ValueError
        counts(data)
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise PlanError(f"Registro de {module} inválido ou não legível em .harness/dados/{filename}; nenhum arquivo foi alterado.") from None
