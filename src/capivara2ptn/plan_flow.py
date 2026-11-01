"""Uma chamada ao planejador, seguida de escrita e G0 sequenciais por thread."""

import asyncio
import json
from pathlib import Path
from typing import Callable

from capivara2ptn.init_flow import InitError, g0_decision
from capivara2ptn.plan_markdown import normalize_plan, parse_plan, parse_task_correction, replace_task
from capivara2ptn.plan_feedback import commit_correction
from capivara2ptn.plan_state import PlanError, digest, load_plan_state, threads, write_json, write_text
from capivara2ptn.project_config import ExecutionOptions
from capivara2ptn.prompt_files import load_prompt
from capivara2ptn.providers import CliRunner, ProviderError, Selection
from capivara2ptn.workspace import data_directory, migrate_legacy, ensure_project_destination
from capivara2ptn.technical_verification import configure_final_task
from capivara2ptn.delivery import configure_delivery_tasks, host_context


class PlanStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.directory = data_directory(self.cwd)
        self.state: dict | None = None
        self.current: dict | None = None
        self.current_role = "writer"
        self.resuming = False
        self.needs_writer = True
        self.preamble = ""

    def begin(self, prd: str, selection: Selection, options: ExecutionOptions) -> dict:
        migrate_legacy(self.cwd)
        ensure_project_destination(self.cwd)
        if self.directory.is_symlink():
            raise PlanError("O diretório .harness/dados/ não pode ser um link.")
        if (self.directory / "plan-estado.json").exists():
            return self.resume(prd, selection, options)
        targets = [self.directory / name for name in ("plano.md", "plano-original.md", "plan-estado.json", "plan-erro.txt", "plan")]
        if any(path.exists() or path.is_symlink() for path in targets):
            raise PlanError("Existem arquivos de planejamento sem um registro válido de progresso. Arquivos preservados; não é seguro determinar de onde retomar.")
        self.state = {
            "version": 1, "module": "PLAN", "status": "writing", "error": "",
            "provider": selection.provider, "model": selection.model, "effort": selection.effort,
            "gates_enabled": options.gates_enabled, "forbid_automated_tests": options.forbid_automated_tests,
            "prd_sha256": digest(prd), "phases": [],
            "technical_verification_required": True,
            "delivery_requirements": True,
        }
        self.save()
        return self.state

    def resume(self, prd: str, selection: Selection, options: ExecutionOptions) -> dict:
        paths = [self.directory / name for name in ("plan-estado.json", "plano.md", "plano-original.md", "plan", "plan/threads")]
        if any(path.is_symlink() for path in paths):
            raise PlanError("O planejamento não pode ser retomado por destinos que sejam links.")
        saved = load_plan_state(self.cwd)
        if saved is None:
            raise PlanError("Registro de progresso do PLAN não encontrado.")
        if saved["status"] == "done":
            raise PlanError("Este PLAN já está concluído. O resultado permanece disponível no painel; nenhum arquivo foi sobrescrito.")
        if saved.get("prd_sha256") != digest(prd):
            raise PlanError("O PRD mudou desde este planejamento. A retomada foi interrompida para não misturar requisitos e progresso anteriores.")
        plan_path = self.directory / "plano.md"
        if plan_path.exists():
            plan = plan_path.read_text(encoding="utf-8")
            if any(thread.get("pending_correction") for thread in threads(saved)):
                preamble, _ = parse_plan(normalize_plan(plan))
                for phase in saved["phases"]:
                    for thread in phase["threads"]:
                        if thread.get("pending_correction"):
                            commit_correction(self.directory, saved, phase, thread, preamble, thread_definition)
                plan = plan_path.read_text(encoding="utf-8")
            if saved.get("plan_sha256") and saved["plan_sha256"] != digest(plan):
                raise PlanError("O Markdown do plano foi alterado. Nenhum progresso ou definição foi sobrescrito.")
            self.preamble, parsed = parse_plan(normalize_plan(plan))
            configure_final_task(parsed, required=saved.get("technical_verification_required", False))
            configure_delivery_tasks(parsed, required=saved.get("delivery_requirements", False))
            if saved["phases"]:
                def definitions(phases: list[dict]) -> list:
                    return [(phase["id"], phase["title"], phase["description"].strip(), [
                        (thread["id"], thread["title"], thread["description"].strip(), thread["dependencies"])
                        for thread in phase["threads"]
                    ]) for phase in phases]
                try:
                    matches = definitions(parsed) == definitions(saved["phases"])
                except (KeyError, TypeError, AttributeError):
                    matches = False
                if not matches:
                    raise PlanError("O plano e o registro das threads não correspondem. Não é seguro retomar; os arquivos foram preservados.")
            else:
                saved["phases"] = parsed
            configure_final_task(saved["phases"], required=saved.get("technical_verification_required", False))
            configure_delivery_tasks(saved["phases"], required=saved.get("delivery_requirements", False))
            saved["plan_sha256"] = digest(plan)
            self.needs_writer = False
            for phase in saved["phases"]:
                for thread in phase["threads"]:
                    path = self.directory / "plan/threads" / f"task-{thread['id']}.md"
                    if path.is_symlink():
                        raise PlanError("Uma definição de thread é um link; retomada interrompida.")
                    expected = thread_definition(self.preamble, phase, thread)
                    if path.exists():
                        if path.read_text(encoding="utf-8") != expected:
                            raise PlanError(f"A definição salva da thread {thread['id']} foi alterada. Ela não será sobrescrita.")
                        thread["writer"] = "done"
                    elif thread["writer"] == "done":
                        raise PlanError(f"A definição concluída da thread {thread['id']} está ausente; retomada interrompida sem sobrescrever o progresso.")
        elif saved["phases"]:
            raise PlanError("O arquivo .harness/dados/plano.md está ausente; não é seguro retomar as threads salvas.")
        else:
            previous = self.directory / "plano-original.md"
            backup = self.directory / "plano-original-anterior.md"
            if previous.exists() and not backup.exists():
                write_text(backup, previous.read_text(encoding="utf-8"))
        if saved.get("error"):
            saved["previous_error"] = saved["error"]
        saved["error"] = ""
        saved["gates_enabled"] = options.gates_enabled
        saved["forbid_automated_tests"] = options.forbid_automated_tests
        saved["resume_selection"] = {"provider": selection.provider, "model": selection.model, "effort": selection.effort}
        saved["status"] = "defining" if not self.needs_writer else "writing"
        for thread in threads(saved):
            # Compatibilidade com reprovações anteriores à realimentação.
            if "needs_correction" not in thread:
                reason = thread.get("error") or thread.get("previous_error", "")
                verdict_path = self.directory / "plan/threads" / f"task-{thread['id']}.g0.txt"
                if reason.startswith("G0 REPROVADO") and verdict_path.is_file() and not verdict_path.is_symlink():
                    try:
                        approved, issues = g0_decision(verdict_path.read_text(encoding="utf-8"))
                        if not approved:
                            thread.update(needs_correction=True, last_rejection=issues)
                    except InitError:
                        pass
            if thread.get("error"):
                thread["previous_error"] = thread["error"]
            thread["error"] = ""
            if thread["writer"] != "done":
                thread["writer"] = "pending"
            if options.gates_enabled and thread.get("g0") != "done":
                thread["g0"] = "failed" if thread.get("needs_correction") else "pending"
        self.state = saved
        self.resuming = True
        self.save()
        return saved

    def save(self) -> None:
        if self.state is not None:
            write_json(self.directory / "plan-estado.json", self.state)

    def fail(self, message: str, interrupted: bool = False) -> None:
        if self.state is None:
            return
        if self.current is not None:
            self.current[self.current_role] = "failed"
            self.current["error"] = message
        self.state["status"] = "interrupted" if interrupted else "failed"
        self.state["error"] = message
        self.save()
        write_text(self.directory / "plan-erro.txt", message + "\n")


def thread_definition(preamble: str, phase: dict, thread: dict) -> str:
    return (
        f"# Task {thread['id']} — {thread['title']}\n\n"
        f"## Contexto tecnológico\n{preamble}\n\n"
        f"## Contexto da fase {phase['id']} — {phase['title']}\n{phase['description']}\n\n"
        f"## Definição da thread\n{thread['description']}\n"
    )


async def correct_thread(prd, store, runner, state, phase, thread, preamble, defined, folder, update) -> None:
    store.current, store.current_role = thread, "writer"
    state["status"] = "correcting"
    thread["writer"] = "running"
    instructions = load_prompt("plan.md")
    attempt = thread.get("correction_attempts", 0) + 1
    thread["correction_attempts"] = attempt
    reason = thread["last_rejection"]
    update(f"Escritor corrigindo exclusivamente a task {thread['id']} — tentativa {attempt}.\nMotivo exato do G0:\n{reason}")
    payload = {
        "modo": "corrigir_task", "prd_final": prd, "motivo_g0": reason,
        "contexto_tecnologico": preamble,
        "fase": {key: phase[key] for key in ("id", "title", "description")},
        "task_atual": {key: thread[key] for key in ("id", "title", "description", "dependencies")},
        "dependencias_relevantes": [defined[identifier] for identifier in thread["dependencies"]],
        "ids_dependencias_disponiveis": list(defined),
        "tasks_preservadas": [
            {key: item[key] for key in ("id", "title", "description", "dependencies")}
            for item in threads(state) if item["id"] != thread["id"]
        ],
        "proibir_testes_automatizados": state["forbid_automated_tests"],
        "ambiente_execucao": host_context(),
    }
    proposal_path = folder / f"task-{thread['id']}.correcao.md"
    try:
        response = await runner.invoke(instructions, payload)
    except ProviderError as error:
        if error.partial:
            write_text(proposal_path, error.partial)
        raise
    write_text(proposal_path, response)
    corrected = parse_task_correction(response, phase, thread, set(defined))
    path = store.directory / "plano.md"
    plan = path.read_text(encoding="utf-8")
    if digest(plan) != state["plan_sha256"]:
        raise PlanError("O plano foi alterado durante a chamada ao escritor; proposta preservada, sem aplicação.")
    patched = replace_task(plan, thread["id"], corrected)
    _, proposed = parse_plan(patched)
    configure_final_task(proposed, required=state.get("technical_verification_required", False))
    configure_delivery_tasks(proposed, required=state.get("delivery_requirements", False))
    proposal = next(item for phase_item in proposed for item in phase_item["threads"] if item["id"] == thread["id"])
    for key in ("technical_check", "environment_check", "readme_delivery"):
        if key in proposal:
            corrected[key] = proposal[key]
    thread["pending_correction"] = {
        "task": corrected, "before_sha": digest(plan), "after_sha": digest(patched),
        "old_definition_sha": digest((folder / f"task-{thread['id']}.md").read_text(encoding="utf-8")),
        "revision": thread.get("revision", 0) + 1,
    }
    store.save()
    commit_correction(store.directory, state, phase, thread, preamble, thread_definition)
    update(f"Escritor concluiu a correção {attempt} da task {thread['id']}; aguardando nova avaliação do G0.")


async def run_plan(
    prd: str, store: PlanStore, runner: CliRunner, options: ExecutionOptions,
    notify: Callable[[dict, str], None],
    *, auditor: CliRunner | None = None,
) -> Path:
    instructions = load_prompt("plan.md") if not (store.directory / "plan-estado.json").exists() else None
    state = store.begin(prd, runner.selection, options)

    def update(message: str) -> None:
        store.save()
        notify(state, message)

    if store.needs_writer:
        update("Retomando a geração inicial do plano." if store.resuming else "Gerando fases e tasks a partir do PRD final do INIT.")
        try:
            response = await runner.invoke(instructions or load_prompt("plan.md"), {"prd_final": prd, "ambiente_execucao": host_context()})
        except ProviderError as error:
            if error.partial:
                write_text(store.directory / "plano-original.md", error.partial)
            raise
        write_text(store.directory / "plano-original.md", response)
        normalized = normalize_plan(response)
        write_text(store.directory / "plano.md", normalized)
        preamble, phases = parse_plan(normalized)
        state["technical_verification_required"] = True
        state["delivery_requirements"] = True
        configure_final_task(phases, required=True)
        configure_delivery_tasks(phases, required=True)
        state["phases"] = phases
        state["plan_sha256"] = digest(normalized)
    else:
        preamble, phases = store.preamble, state["phases"]
        update("Retomando o PLAN salvo, preservando definições e aprovações anteriores; " + ("G0 habilitado nas threads que ainda precisam dele." if options.gates_enabled else "gates desabilitados, sem chamadas G0."))
    for thread in threads(state):
        if options.gates_enabled and "g0" not in thread:
            thread["g0"] = "pending"
    folder = store.directory / "plan/threads"
    folder.mkdir(mode=0o700, parents=True, exist_ok=store.resuming)
    state["status"] = "defining"
    update("Plano salvo e identificado. Salvando as definições individuais, na ordem declarada.")
    defined: dict[str, dict] = {}
    for phase in phases:
        for thread in phase["threads"]:
            if thread["writer"] != "done":
                store.current, store.current_role = thread, "writer"
                state["status"] = "defining"
                thread["writer"] = "running"
                update(f"Salvando definição da thread {thread['id']}.")
                await asyncio.to_thread(write_text, folder / f"task-{thread['id']}.md", thread_definition(preamble, phase, thread))
                thread["writer"] = "done"
                update(f"Definição da thread {thread['id']} salva.")
            while options.gates_enabled and thread.get("g0") != "done":
                if thread.get("needs_correction"):
                    await correct_thread(prd, store, runner, state, phase, thread, preamble, defined, folder, update)
                store.current = thread
                store.current_role = "g0"
                thread["g0"] = "running"
                state["status"] = "validating"
                # Releitura a cada thread; não há prompt ou aprovação da fase inteira.
                gate_instructions = load_prompt("gates/g0-plan.md")
                attempt = thread.get("g0_attempts", 0) + 1
                thread["g0_attempts"] = attempt
                update(f"G0 PLAN avaliando somente a task {thread['id']} — tentativa {attempt}.")
                previous = folder / f"task-{thread['id']}.g0.txt"
                backup = folder / f"task-{thread['id']}.g0-anterior.txt"
                if previous.is_symlink():
                    raise PlanError(f"A resposta G0 salva da thread {thread['id']} é um link; retomada interrompida.")
                if previous.exists() and not backup.exists():
                    write_text(backup, previous.read_text(encoding="utf-8"))
                payload = {
                    "prd_final": prd, "contexto_tecnologico": preamble,
                    "fase": {"id": phase["id"], "nome": phase["title"], "objetivo": phase["description"]},
                    "thread": {key: thread[key] for key in ("id", "title", "description", "dependencies")},
                    "dependencias": [defined[identifier] for identifier in thread["dependencies"]],
                    "proibir_testes_automatizados": options.forbid_automated_tests,
                    "ambiente_execucao": host_context(),
                }
                try:
                    verdict = await (auditor or runner).invoke(gate_instructions, payload)
                except ProviderError as error:
                    if error.partial:
                        write_text(folder / f"task-{thread['id']}.g0.txt", error.partial)
                    raise
                write_text(folder / f"task-{thread['id']}.g0.txt", verdict)
                try:
                    approved, issues = g0_decision(verdict)
                except InitError:
                    raise PlanError(f"G0 PLAN da thread {thread['id']} inconclusivo: decisão ausente, ambígua ou sem justificativa. Veja sua resposta em .harness/dados/plan/threads/.") from None
                if not approved:
                    thread.update(g0="failed", needs_correction=True, last_rejection=issues)
                    thread["error"] = f"G0 REPROVADO — task {thread['id']}\n{issues}"
                    update(f"G0 reprovou a task {thread['id']} na tentativa {attempt}.\nMotivo exato:\n{issues}")
                    continue
                thread["g0"] = "done"
                thread["needs_correction"] = False
                thread["error"] = ""
                update(f"G0 APROVADO — thread {thread['id']}.")
            defined[thread["id"]] = {key: thread[key] for key in ("id", "title", "description", "dependencies")}
            store.current = None
    state["status"] = "done"
    update("PLAN concluído: todas as threads foram definidas e concluídas. BUILD não foi executado.")
    return store.directory / "plano.md"
