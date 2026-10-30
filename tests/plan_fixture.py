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
        self.phase_reviews_by_number = {}
        self.plan_reviews = []
        self.invalid = 0
        self.cancel = False
        self.on_generate = None
        self.repairs = []
        self.classifications = []
        self.classification_reviews = []
        self.contract_reviews = []
        self.ignore_correction = False
        self.task_repairs = {}

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
        if operation == "classify_work":
            if self.classifications:
                return json.dumps(self.classifications.pop(0))
            return json.dumps({"activities": [{"source_id": a["source_id"], "parts": [{"text": a["text"],
                "responsibility": "build" if a["text"].startswith(("Executar testes", "Registrar resultados")) else "implementation",
                "reason": "Responsabilidade de implementação ou execução de QA conforme a origem."}]} for a in data["activities"]]})
        if operation == "review_classification":
            return json.dumps(self.classification_reviews.pop(0) if self.classification_reviews else
                              {"status": "approved", "summary": "Responsabilidades fiéis à origem.", "corrections": []})
        if operation == "validate_completion":
            return json.dumps({"approved": True, "reason": "Todas as tasks, fases e versões foram explicitamente aprovadas e a origem foi preservada."})
        if operation == "validate_correction":
            return json.dumps(self.contract_reviews.pop(0) if self.contract_reviews else {"approved": True, "reason": "Contrato concreto e fiel."})
        if operation == "repair_correction":
            return json.dumps(data["rejected_contract"])
        if operation == "correct_task":
            result = dict(data["previous_task"])
            if not self.ignore_correction:
                for change in data["correction"]["changes"]:
                    result[change["field"]] = change["after"]
            return json.dumps(result)
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
            activities = [r for r in requirements if r["kind"] in ("activity", "technology", "tool") and r["execution_owner"] == "tasks"]
            if not activities:
                return json.dumps({"tasks": []})
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
        if operation == "reassess_task":
            if self.repairs:
                return json.dumps(self.repairs.pop(0))
            return json.dumps({"summary": "Reavaliar apenas o recorte atribuído.",
                "decisions": [{"issue_id": i["id"], "task_id": i["task_id"],
                    "decision": "A task deve atender apenas seu recorte; não cobrar responsabilidades de outras tasks."}
                    for i in data["scope_decisions"]["issues"] if i["status"] == "open"],
                "additions": [], "structural_changes": []})
        reviews = {"review_task": self.task_reviews, "review_phase": self.phase_reviews, "review_plan": self.plan_reviews}
        if operation in reviews:
            if operation=='review_task' and self.task_repairs.get(data['task']['id']):
                repair=self.task_repairs[data['task']['id']].pop(0)
                self.repairs.append({key:repair.get(key,[]) for key in ('structural_changes','additions','decisions')} |
                                    {'summary':repair.get('summary','Reparo da task atual.')})
                claim=data['task']['seed']['coverage'][0]
                requirement=next(r for r in data['assigned_requirements'] if r['id']==claim['requirement_id'])
                return json.dumps({'approved':False,'matches_source':False,'coverage_complete':True,
                    'tasks_are_small':False,'no_test_strategy':True,'summary':'Reavaliar somente a estrutura desta task.',
                    'corrections':[],'replan':{'task_id':data['task']['id'],'reason':'Reparo estrutural delimitado.',
                    'evidence':[{'requirement_id':requirement['id'],'quote':requirement['text']}]}})
            queue = (self.phase_reviews_by_number.get(data["phase"]["number"], self.phase_reviews)
                     if operation == "review_phase" else reviews[operation])
            review = queue.pop(0) if queue else approved_review()
            # Respostas antigas da fixture são traduzidas para o contrato próprio do papel.
            if "replan" in review:
                return json.dumps(review)
            result = {k: review[k] for k in ("approved", "matches_source", "coverage_complete", "tasks_are_small", "no_test_strategy", "summary")}
            result["corrections"] = []
            for correction in review["corrections"]:
                task = data.get("task")
                if task is None:
                    phases = [data["phase"]] if "phase" in data else data["phases"]
                    task = next((t for p in phases for t in p["tasks"] if t["id"] == correction["task_id"]), None)
                if task is None:
                    task = {"id": correction["task_id"], "content": {"objective": "Inválido"}, "seed": {"coverage": []}}
                claims = task["seed"]["coverage"]
                req_id = claims[0]["requirement_id"] if claims else "invalid"
                reqs = data.get("assigned_requirements", []) or [r["requirement"] for r in data.get("coverage_map", [])]
                req = next((r for r in reqs if r["id"] == req_id), {"text": "Inválido"})
                if operation == "review_task" and correction.get("category") in ("structure", "granularity"):
                    result["replan"] = {"task_id": correction["task_id"], "reason": correction["reason"],
                                       "evidence": [{"requirement_id": req_id, "quote": req["text"]}]}
                    continue
                before = task["content"]["objective"]
                marker = "Requisito preservado: " + req["text"]
                spec = correction.get("contract") or {"task_id": correction["task_id"],
                    "base_version": data.get("base_version") or data["approved_versions"].get(correction["task_id"], "invalid"),
                    "category": correction.get("category", "fidelity"), "reason": correction["reason"], "instruction": correction["instruction"],
                    "evidence": [{"requirement_id": req_id, "quote": req["text"]}],
                    "changes": [{"field": "objective", "before": before, "after": before + " " + marker}],
                    "acceptance": [{"field": "objective", "operator": "contains", "value": marker}]}
                result["corrections"].append(spec)
            if operation == "review_task":
                result.setdefault("replan", None)
            else:
                result.update(additions=review.get("additions", []), structural_changes=review.get("structural_changes", []))
            return json.dumps(result)
        raise AssertionError(operation)
