"""BUILD da Capivarinha: uma task, escritor, verificações e avanço sequencial."""

import asyncio
import copy
import json
import re
from pathlib import Path

from capivara2ptn.build_g1 import evaluate, inventory
from capivara2ptn.plan_markdown import normalize_plan, parse_plan
from capivara2ptn.plan_state import PlanError, counts, digest, load_plan_state, read_init_prd, threads, write_json, write_text
from capivara2ptn.prompt_files import load_prompt
from capivara2ptn.providers import ProviderError
from capivara2ptn.workspace import data_directory, ensure_project_destination
from capivara2ptn.technical_verification import configure_final_task, execute, finished
from capivara2ptn.delivery import configure_delivery_tasks, host_context, readme_ready


class BuildError(Exception):
    pass


def gate_decision(response: str, gate: str) -> tuple[bool, str]:
    text = response.strip()
    decisions = re.findall(rf"\b{gate}\s+(APROVADO|REPROVADO)\b", text, re.I)
    if text == f"{gate} APROVADO" and decisions == ["APROVADO"]:
        return True, ""
    lines = text.splitlines()
    if len(decisions) == 1 and lines[0] == f"{gate} REPROVADO" and "\n".join(lines[1:]).strip():
        return False, "\n".join(lines[1:]).strip()
    raise ProviderError(f"Resposta inválida de {gate}: decisão ausente, ambígua ou reprovação sem motivo objetivo.", response)


def read_build_input(cwd: Path) -> tuple[str, str, str, list[dict]]:
    ensure_project_destination(cwd)
    prd = read_init_prd(cwd)
    state = load_plan_state(cwd)
    if not state or state["status"] != "done" or counts(state)[0] != counts(state)[1] or not counts(state)[1]:
        raise BuildError("BUILD exige PLAN concluído; o planejamento atual ainda não terminou.")
    path = data_directory(cwd) / "plano.md"
    if path.is_symlink():
        raise BuildError("O plano não pode ser um link.")
    plan = path.read_text(encoding="utf-8")
    if state.get("prd_sha256") != digest(prd) or state.get("plan_sha256") != digest(plan):
        raise BuildError("PRD ou plano alterado após a conclusão do PLAN. Os arquivos foram preservados.")
    preamble, phases = parse_plan(normalize_plan(plan))
    configure_final_task(phases, required=state.get("technical_verification_required", False))
    configure_delivery_tasks(phases, required=state.get("delivery_requirements", False))
    return prd, plan, preamble, phases


class BuildStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.directory = data_directory(self.cwd)
        self.folder = self.directory / "build"
        self.state = None
        self.current = None
        self.current_role = "writer"

    def begin(self, prd, plan, phases, selection, options) -> dict:
        if self.directory.is_symlink() or self.folder.is_symlink():
            raise BuildError("O controle do BUILD não pode ser um link.")
        saved = load_plan_state(self.cwd, module="BUILD")
        if saved:
            if saved["prd_sha256"] != digest(prd) or saved["plan_sha256"] != digest(plan):
                raise BuildError("O PRD ou PLAN mudou desde este BUILD. Progresso preservado; não é seguro misturar entradas.")
            self.state = saved
            if saved["status"] == "done":
                return saved
            for task in threads(saved):
                if task["operation"] == "done":
                    continue
                task["gates_enabled"] = options.gates_enabled
                if task.get("technical_check"):
                    task["technical_pending"] = True
                    if task["operation"] in ("technical", "g1", "g2"):
                        task["operation"] = "technical"  # Evidência nova após reabrir, com os arquivos atuais.
                if task.get("environment_check"):
                    task["environment_pending"] = True
                    if task["operation"] in ("environment", "g1", "g2"):
                        task["operation"] = "environment"
                if not options.gates_enabled:
                    if task.get("environment_check"):
                        if task["operation"] != "writer":
                            task["operation"] = "environment"
                    elif task.get("technical_check"):
                        if task["operation"] != "writer":
                            task["operation"] = "technical"
                    else:
                        task["operation"] = "done" if task["writer"] == "done" else "writer"
                    if task["operation"] == "done":
                        task["error"] = ""
                elif task["operation"] in ("writer", "technical", "environment") and task["g0"] != "done":
                    task["operation"] = "g0"
                for role in ("g0", "writer", "g1", "g2"):
                    if task[role] == "running":
                        task[role] = "pending"
        else:
            self.state = {"version": 1, "module": "BUILD", "status": "executing", "error": "",
                "prd_sha256": digest(prd), "plan_sha256": digest(plan), "phases": copy.deepcopy(phases)}
            for task in threads(self.state):
                task.update(writer="pending", g0="pending", g1="pending", g2="pending",
                    operation="g0" if options.gates_enabled else "writer", gates_enabled=options.gates_enabled,
                    attempts={}, preparation="", error="", last_rejection="", feedback_gate="")
                if task.get("technical_check"):
                    task["technical_pending"] = True
                    if not options.gates_enabled:
                        task["operation"] = "technical"
                if task.get("environment_check"):
                    task["environment_pending"] = True
        self.state.update(status="executing", error="", gates_enabled=options.gates_enabled,
            forbid_automated_tests=options.forbid_automated_tests,
            selection={"provider": selection.provider, "model": selection.model, "effort": selection.effort})
        self.folder.mkdir(mode=0o700, exist_ok=True)
        self.save()
        return self.state

    def save(self) -> None:
        if self.state is not None:
            write_json(self.directory / "build-estado.json", self.state)

    def fail(self, reason: str, interrupted=False) -> None:
        if self.state is None or self.state["status"] == "done":
            return
        self.state.update(status="interrupted" if interrupted else "failed", error=reason)
        if self.current:
            self.current[self.current_role] = "failed"
            self.current["error"] = reason
        self.save()

    def path(self, task: dict, suffix: str) -> Path:
        return self.folder / f"task-{task['id']}.{suffix}"


async def run_build(store: BuildStore, runner, options, notify, *, auditor=None) -> Path:
    prd, plan, preamble, phases = await asyncio.to_thread(read_build_input, store.cwd)
    state = store.begin(prd, plan, phases, runner.selection, options)
    if state["status"] == "done":
        if not readme_ready(store.cwd):
            raise BuildError("A entrega anterior não contém README.md com instruções de uso. O resultado antigo foi preservado; a documentação precisa ser produzida.")
        notify(state, "BUILD já concluído; nenhuma task foi repetida.")
        return store.directory / "build-estado.json"

    def update(message):
        store.save()
        notify(state, message)

    def reject(task, gate, reason):
        task.update(last_rejection=reason, feedback_gate=gate, error=f"{gate} REPROVADO\n{reason}")
        task[gate.lower()] = "failed"
        task["operation"] = "preparation" if gate == "G0" else "writer"
        update(f"{gate} REPROVADO — task {task['id']}.\n{reason}\nRetorno ao mesmo escritor para correção localizada.")

    completed = {}
    for phase in state["phases"]:
        for task in phase["threads"]:
            if task["operation"] == "done":
                completed[task["id"]] = task
                continue
            if any(identifier not in completed for identifier in task["dependencies"]):
                raise BuildError(f"Task {task['id']}: dependência anterior ainda não concluída.")
            store.current = task
            phase["started"] = True
            update(f"Fase {phase['id']} · Task {task['id']} — {task['title']}.")

            def payload():
                data = {"prd_final": prd, "contexto_tecnologico": preamble,
                    "fase_atual": {key: phase[key] for key in ("id", "title", "description")},
                    "task_atual": {key: task[key] for key in ("id", "title", "description", "dependencies")},
                    "preparacao_local": task["preparation"], "atualizacao_premissas": task.get("premises_update", ""),
                    "dependencias": [{key: completed[identifier].get(key, "") for key in ("id", "title", "description", "writer_report")}
                                     for identifier in task["dependencies"]],
                    "gate_reprovado": task["feedback_gate"], "motivo_exato": task["last_rejection"],
                    "relato_anterior": task.get("writer_report", ""),
                    "resultado_g1": task.get("g1_result"),
                    "proibir_testes_automatizados": options.forbid_automated_tests,
                    "continuar_sobre_os_arquivos_reais_existentes": True}
                if task.get("technical_check"):
                    data.update(verificacao_tecnica_final=task["technical_check"],
                                resultado_verificacao_tecnica=task.get("technical_result"))
                if task.get("environment_check"):
                    data.update(preparacao_ambiente=task["environment_check"], resultado_ambiente=task.get("environment_result"), ambiente_execucao=host_context())
                if task.get("readme_delivery"):
                    data["entrega_readme"] = True
                return data

            async def model(operation, prompt, access, parse=None):
                role = "writer" if operation == "preparation" else operation
                store.current_role = role
                while True:
                    task[role] = "running"
                    state["status"] = "correcting" if operation == "preparation" or (role == "writer" and task["feedback_gate"]) else "executing"
                    task["attempts"][operation] = task["attempts"].get(operation, 0) + 1
                    attempt = task["attempts"][operation]
                    instructions = load_prompt(prompt)
                    update(f"Task {task['id']} · {'escritor preparando correção' if operation == 'preparation' else operation.upper()} — tentativa {attempt}.")
                    data = payload()
                    data.update(operacao=operation, erro_chamada_anterior=task.get("model_error", ""))
                    try:
                        executor = (auditor or runner) if operation in ("g0", "g2") else runner
                        response = await executor.invoke(instructions, data, access=access)
                        write_text(store.path(task, operation + ".md"), response + "\n")
                        if response.strip().startswith("ERRO_CHAMADA_MODELO"):
                            raise ProviderError(response, response)
                        if operation == "writer" and task.get("readme_delivery") and not readme_ready(store.cwd):
                            raise ProviderError("O README.md obrigatório ainda não foi salvo/preenchido na raiz do projeto. Conclua a documentação desta task antes de avançar.", response)
                        result = parse(response) if parse else response
                        task["model_error"] = ""
                        task["error"] = ""
                        return result
                    except (ProviderError, asyncio.TimeoutError) as error:
                        reason = runner.safe(str(error) or type(error).__name__)
                        task.update(model_error=reason, error=reason)
                        task[role] = "failed"
                        state["status"] = "retrying"
                        if isinstance(error, ProviderError) and error.partial:
                            write_text(store.path(task, operation + ".parcial.md"), error.partial)
                        update(f"Erro na chamada ao modelo — task {task['id']} · {operation}, tentativa {attempt}:\n{reason}\nNova tentativa da mesma operação em 2 segundos; Ctrl+C interrompe o BUILD.")
                        await asyncio.sleep(2)

            while task["operation"] != "done":
                operation = task["operation"]
                if operation == "g0":
                    approved, reason = await model("g0", "gates/g0-build.md", "read", lambda text: gate_decision(text, "G0"))
                    if not approved:
                        reject(task, "G0", reason)
                        continue
                    task["g0"] = "done"
                    task["operation"] = ("technical" if task.get("technical_check") else
                                         "environment" if task.get("environment_check") and task["writer"] == "done" else
                                         "g1" if task["writer"] == "done" else "writer")
                    update(f"G0 APROVADO — task {task['id']} liberada.")
                elif operation == "preparation":
                    response = await model("preparation", "BUILD.md", "read")
                    if response.strip().startswith("ALTERACAO_AMPLA_NECESSARIA"):
                        raise BuildError(response)
                    task.update(preparation=response, writer="pending", g0="pending", operation="g0")
                    update(f"Preparação localizada da task {task['id']} corrigida; PRD e PLAN preservados. Reavaliando G0.")
                elif operation == "writer":
                    store.current_role = "writer"
                    if not task.get("environment_check") and not task.get("readme_delivery"):
                        preparation = next((item for item in threads(state) if item.get("environment_check")), None)
                        if not preparation or preparation["operation"] != "done" or not finished(preparation.get("environment_result")):
                            raise BuildError("Produção de código bloqueada: as dependências de desenvolvimento ainda não foram satisfeitas e verificadas. O PLAN deve definir a primeira task de preparação do ambiente; nenhum código desta task foi produzido.")
                    if options.gates_enabled:
                        before = store.path(task, "antes.json")
                        if not before.exists():
                            write_json(before, await asyncio.to_thread(inventory, store.cwd))
                        task.update(g1="pending", g2="pending")
                    response = await model("writer", "BUILD.md", "write")
                    if response.strip().startswith("ALTERACAO_AMPLA_NECESSARIA"):
                        raise BuildError(response)
                    task.update(writer="done", writer_report=response)
                    if task.get("environment_check"):
                        task["operation"] = "environment"
                        if options.gates_enabled and re.search(r"(?m)^PREMISSAS_G0_ALTERADAS\b", response):
                            task.update(g0="pending", premises_update=response, operation="g0")
                    elif task.get("technical_check"):
                        task["operation"] = "technical"
                        if options.gates_enabled and re.search(r"(?m)^PREMISSAS_G0_ALTERADAS\b", response):
                            task.update(g0="pending", premises_update=response, operation="g0")
                    elif options.gates_enabled:
                        if re.search(r"(?m)^PREMISSAS_G0_ALTERADAS\b", response):
                            task.update(g0="pending", premises_update=response, operation="g0")
                        else:
                            task["operation"] = "g1"
                    else:
                        task["operation"] = "done"
                    update("Configurações de preparação salvas; verificando/instalando as dependências antes da produção." if task.get("environment_check") else
                           f"Correção da task {task['id']} salva; reexecutando os comandos do PLAN." if task.get("technical_check") else
                           f"Escritor concluiu a task {task['id']}." + (" Verificando entrega." if options.gates_enabled else " Gates desabilitados; sem chamadas de validação."))
                elif operation == "environment":
                    store.current_role = "writer"
                    task.update(writer="running", environment_pending=True, environment_result=None)
                    config = task["environment_check"]
                    task["attempts"]["environment"] = task["attempts"].get("environment", 0) + 1
                    prefix = f"task-{task['id']}.environment-{task['attempts']['environment']}"
                    update("Preparação do ambiente: verificando todas as dependências definidas no PLAN antes de produzir código.")
                    check = {"comandos": config["verificacoes"], "nao_aplicavel": config["nao_aplicavel"]}
                    emit = lambda text: notify(state, text)
                    result = await execute(store.cwd, check, store.folder, prefix + ".antes", emit, runner.safe, context=preamble)
                    if not finished(result) and config["instalacoes"]:
                        update("Dependências ausentes: executando somente as instalações declaradas no PLAN.")
                        installation = await execute(store.cwd, {"comandos": config["instalacoes"], "nao_aplicavel": None},
                            store.folder, prefix + ".instalacao", emit, runner.safe, context=preamble)
                        if finished(installation):
                            result = await execute(store.cwd, check, store.folder, prefix + ".depois", emit, runner.safe, context=preamble)
                        else:
                            result = installation
                    task["environment_result"] = result
                    if not finished(result):
                        details = "\n".join(f"$ {item['comando']}\nRetorno: {item['codigo_retorno']}\n{item['saida']}\n{item.get('erro_execucao', '')}" for item in result["comandos"] if item["codigo_retorno"] != 0)
                        hint = ("O PLAN não definiu comandos de instalação para suprir a dependência ausente." if not config["instalacoes"] else
                                "Confira a falha de instalação/verificação abaixo. Instalações que exigem permissão precisam dessa autorização no sistema antes da retomada.")
                        raise BuildError("Ambiente não preparado. Nenhuma task de produção será iniciada. " + hint + "\n" + details)
                    task["writer"] = "done"
                    task["operation"] = "g1" if options.gates_enabled else "done"
                    if not options.gates_enabled:
                        task["environment_pending"] = False
                    update("Dependências verificadas com sucesso. Preparação concluída; produção liberada após os gates aplicáveis desta task.")
                elif operation == "technical":
                    store.current_role = "writer"
                    task.update(writer="running", technical_pending=True, technical_result=None, error="")
                    if options.gates_enabled:
                        before = store.path(task, "antes.json")
                        if not before.exists():
                            write_json(before, await asyncio.to_thread(inventory, store.cwd))
                        task.update(g1="pending", g2="pending")
                    task["attempts"]["technical"] = task["attempts"].get("technical", 0) + 1
                    attempt = task["attempts"]["technical"]
                    state["status"] = "executing"
                    update(f"Verificação técnica final · task {task['id']} · execução {attempt}. Executando os comandos do PLAN.")
                    result = await execute(store.cwd, task["technical_check"], store.folder,
                        f"task-{task['id']}.technical-{attempt}", lambda text: notify(state, text), runner.safe, context=preamble)
                    task["technical_result"] = result
                    if finished(result):
                        task["writer"] = "done"
                        update("Verificação técnica: " + ("comandos executados com retorno zero." if result["status"] == "sucesso" else "não aplicável; justificativa registrada, sem simular compilação."))
                    else:
                        task["error"] = "Verificação técnica falhou. Códigos de retorno: " + ", ".join(str(item["codigo_retorno"]) for item in result["comandos"])
                        update(task["error"] + ". Saída real preservada para orientar a correção.")
                    if options.gates_enabled:
                        task["operation"] = "g1"
                    elif finished(result):
                        task.update(operation="done", technical_pending=False)
                    else:
                        task.update(operation="writer", feedback_gate="verificacao_tecnica", last_rejection=task["error"])
                        update("Gates desabilitados: retornando ao escritor com a saída real, sem chamar G2.")
                elif operation == "g1":
                    store.current_role = "g1"
                    task["g1"] = "running"
                    task["attempts"]["g1"] = task["attempts"].get("g1", 0) + 1
                    update(f"G1 determinístico — task {task['id']}, tentativa {task['attempts']['g1']}: comparando com o estado anterior à primeira tentativa.")
                    before = json.loads(store.path(task, "antes.json").read_text(encoding="utf-8"))
                    after = await asyncio.to_thread(inventory, store.cwd)
                    result = evaluate(before, after, task)
                    task["g1_result"] = result
                    write_json(store.path(task, "g1.json"), result)
                    if not result["aprovado"]:
                        reject(task, "G1", result["motivo"])
                        continue
                    task.update(g1="done", operation="g2")
                    update(f"G1 APROVADO — task {task['id']}. {result['motivo']}")
                elif operation == "g2":
                    def decision(text):
                        verdict = gate_decision(text, "G2")
                        if verdict[0] and task.get("technical_check") and not finished(task.get("technical_result")):
                            raise ProviderError("G2 não pode aprovar: os comandos reais da verificação técnica falharam. Interprete a saída e informe objetivamente a correção necessária.", text)
                        if verdict[0] and task.get("environment_check") and not finished(task.get("environment_result")):
                            raise ProviderError("G2 não pode aprovar a preparação sem evidência de dependências satisfeitas.", text)
                        return verdict
                    approved, reason = await model("g2", "G2.md", "read", decision)
                    if not approved:
                        reject(task, "G2", reason)
                        continue
                    task.update(g2="done", operation="done")
                    if task.get("technical_check"):
                        task["technical_pending"] = False
                    if task.get("environment_check"):
                        task["environment_pending"] = False
                    update(f"G2 APROVADO — task {task['id']}.")
                else:
                    raise BuildError("Operação de BUILD salva não reconhecida; progresso preservado.")
                store.save()
            task["error"] = ""
            completed[task["id"]] = task
            store.current = None
            update(f"Task {task['id']} concluída. Avançando na ordem do PLAN.")
    if not readme_ready(store.cwd):
        raise BuildError("README.md obrigatório ausente ou vazio. A entrega não será marcada como concluída sem instruções de instalação e execução; atualize o PLAN para incluir a documentação.")
    state.update(status="done", error="")
    update("BUILD concluído. Todas as tasks e fases concluídas; nenhuma validação adicional de fase ou projeto.")
    return store.directory / "build-estado.json"
