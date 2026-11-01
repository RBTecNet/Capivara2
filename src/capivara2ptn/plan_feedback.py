"""Aplicação isolada de uma proposta já produzida pelo escritor do PLAN."""

from pathlib import Path

from capivara2ptn.plan_markdown import replace_task
from capivara2ptn.plan_state import PlanError, digest, write_json, write_text


def commit_correction(directory: Path, state: dict, phase: dict, thread: dict, preamble: str, definition) -> None:
    pending = thread.get("pending_correction")
    if not pending:
        return
    plan_path = directory / "plano.md"
    task_path = directory / "plan/threads" / f"task-{thread['id']}.md"
    if plan_path.is_symlink() or task_path.is_symlink():
        raise PlanError("Não é seguro aplicar uma correção por arquivos que sejam links.")
    plan = plan_path.read_text(encoding="utf-8")
    if digest(plan) == pending["before_sha"]:
        plan = replace_task(plan, thread["id"], pending["task"])
    if digest(plan) != pending["after_sha"]:
        raise PlanError("O plano mudou durante a correção; proposta pendente preservada, sem sobrescrever alterações.")
    new_thread = {**thread, **pending["task"]}
    new_definition = definition(preamble, phase, new_thread)
    if task_path.exists() and digest(task_path.read_text(encoding="utf-8")) not in (pending["old_definition_sha"], digest(new_definition)):
        raise PlanError("A definição foi alterada durante a correção; arquivo preservado.")
    # Sem await entre as gravações: cancelamento normal não intercala o commit.
    # Se houver falha de disco/processo, pending_correction permite completar
    # somente esta mesma proposta na próxima retomada explícita.
    write_text(plan_path, plan)
    write_text(task_path, new_definition)
    thread.update(pending["task"])
    thread.update(writer="done", g0="pending", needs_correction=False, error="")
    thread["revision"] = pending["revision"]
    thread.pop("pending_correction", None)
    state["plan_sha256"] = digest(plan)
    write_json(directory / "plan-estado.json", state)
