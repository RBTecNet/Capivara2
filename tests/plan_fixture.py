"""Agentes de plan controlados para conferir contratos e correções documentais."""

import json


def approved_review():
    return {"approved": True, "matches_source": True, "coverage_complete": True,
            "tasks_are_small": True, "no_test_strategy": True, "summary": "Fiel ao recorte de origem.",
            "corrections": [], "additions": []}


def rejected(task_id, instruction="Restaure o escopo original."):
    return {**approved_review(), "approved": False, "matches_source": False,
            "summary": "Existe desvio documental.", "corrections": [
                {"task_id": task_id, "reason": "O conteúdo divergiu da origem.", "instruction": instruction,
                 "replacement_seed": None}]}


class PlanAgents:
    def __init__(self):
        self.calls = []
        self.task_reviews = []
        self.phase_reviews = []
        self.plan_reviews = []
        self.invalid = 0
        self.cancel = False
        self.on_generate = None
        self.repairs = []

    async def generate(self, selection, role, prompt, schema, store, call_id):
        envelope = json.loads(prompt.rsplit("\n\nREQUEST:\n", 1)[1])
        self.calls.append((role, envelope))
        request = envelope["current_request"]
        operation, data = request["task"], request["data"]
        if self.on_generate:
            self.on_generate(operation, store)
        if self.cancel:
            import asyncio
            raise asyncio.CancelledError()
        if self.invalid:
            self.invalid -= 1
            return "resposta inválida"
        if operation == "outline_phase":
            origin = data["phase_source"]
            # Um recorte por atividade; fase 2 é dividida em operações pequenas.
            if origin["number"] == 0:
                seeds = [("Stack", "Documentar Python e SQLite.", "Stack do projeto"),
                         ("Dependências de testes", "Identificar Playwright como dependência já definida.", "Playwright")]
            elif origin["number"] == 1:
                seeds = [("Esquema", "Definir o esquema de tarefas.", "Definir o esquema de tarefas.")]
            else:
                seeds = [(f"{verb} tarefa", f"Permitir {verb} tarefas na interface em português.",
                          "Permitir adicionar, editar, concluir e excluir tarefas.") for verb in ("adicionar", "editar", "concluir", "excluir")]
            tasks = [{"title": title, "scope": scope, "source_quotes": [quote], "coverage": []}
                     for title, scope, quote in seeds]
            requirements = data["requirements"]
            activities = [r for r in requirements if r["kind"] in ("activity", "technology", "tool")]
            if origin["number"] == 0:
                for r in activities:
                    index = 1 if "Playwright" in r["text"] else 0
                    tasks[index]["coverage"].append({"requirement_id": r["id"], "excerpt": r["text"]})
            elif origin["number"] == 1:
                tasks[0]["coverage"] = [{"requirement_id": r["id"], "excerpt": r["text"]} for r in activities]
            else:
                for task, verb in zip(tasks, ("adicionar", "editar", "concluir", "excluir")):
                    task["coverage"] = [{"requirement_id": activities[0]["id"], "excerpt": verb}]
            return json.dumps({"tasks": tasks})
        if operation == "write_task":
            seed = data["seed"]
            return json.dumps({"title": seed["title"], "objective": seed["scope"],
                "implementation_steps": [seed["scope"]], "deliverables": [seed["title"]]})
        if operation == "reassess_phase":
            if self.repairs:
                return json.dumps(self.repairs.pop(0))
            return json.dumps({"summary": "Reavaliar apenas o recorte atribuído.",
                "decisions": [{"issue_id": i["id"], "task_id": i["task_id"],
                    "decision": "A task deve atender apenas seu recorte; não cobrar responsabilidades de outras tasks."}
                    for i in data["scope_decisions"]["issues"] if i["status"] == "open"],
                "corrections": [], "additions": [], "structural_changes": []})
        reviews = {"review_task": self.task_reviews, "review_phase": self.phase_reviews, "review_plan": self.plan_reviews}
        if operation in reviews:
            queue = reviews[operation]
            return json.dumps(queue.pop(0) if queue else approved_review())
        raise AssertionError(operation)
