"""Respostas documentais controladas, sem executar CLI ou ferramentas."""

from copy import deepcopy
import json


def review(*, approved=False, gaps=None, stack=False, feasible=True, deviations=None):
    return {"summary": "Documento aprovado." if approved else "Definir as decisões pendentes e manter todos os requisitos.",
            "requirements": ["Adicionar, editar, concluir e excluir tarefas.", "Persistir os dados localmente."],
            "matches_original": not bool(deviations), "feasible": feasible, "phases_valid": approved,
            "approved": approved, "technology": {"stack_specified": stack, "stack_quote": "Python",
            "tools_specified": stack, "tools_quote": "SQLite"}, "gaps": gaps or [],
            "deviations": deviations or [], "infeasibilities": [] if feasible else ["A sincronização offline instantânea entre máquinas desconectadas é impossível; preserve o uso local solicitado."]}


def final_document():
    return {"title": "Aplicação de tarefas", "objective": "Gerenciar tarefas pessoais com persistência local.",
            "requirements": ["Adicionar, editar, concluir e excluir tarefas.", "Persistir os dados localmente."],
            "constraints": ["Interface em português; um usuário, sem autenticação."],
            "stack": [{"component": "Aplicação", "choice": "Python", "reason": "Atende ao uso local."}],
            "tools": [{"name": "SQLite", "purpose": "Persistir tarefas no computador.", "scope": "project"},
                      {"name": "Playwright", "purpose": "Validar a interface na etapa futura de build.", "scope": "harness"}],
            "phases": [
                {"number": 1, "title": "Persistência", "goal": "Guardar tarefas localmente.",
                 "tasks": ["Definir o esquema de tarefas."], "deliverables": ["Persistência especificada."],
                 "acceptance_criteria": ["As tarefas persistem após reiniciar a aplicação."], "depends_on": [0]},
                {"number": 2, "title": "Interface e operações", "goal": "Gerenciar as tarefas.",
                 "tasks": ["Permitir adicionar, editar, concluir e excluir tarefas."], "deliverables": ["Interface em português."],
                 "acceptance_criteria": ["Todas as operações solicitadas estão disponíveis."], "depends_on": [1]}],
            "acceptance_criteria": ["Operações preservam os dados e atendem ao pedido original."]}


class ScriptedAgents:
    def __init__(self, final_reviews=None):
        self.calls = []
        self.final_reviews = list(final_reviews or [])
        self.initial = review()
        self.documents = []
        self.goal = "tarefas"

    async def generate(self, selection, role, prompt, schema, store, call_id):
        envelope = json.loads(prompt.rsplit("\n\nREQUEST:\n", 1)[1])
        self.calls.append((role, envelope))
        request = envelope["current_request"]
        task, data = request["task"], request["data"]
        if task == "initial_review":
            self.goal = data["source"]["initial_prompt"].split()[0]
            return json.dumps(self.initial)
        if task == "interview":
            questions = []
            for index, gap in enumerate(data["review"]["gaps"]):
                questions.append({"id": f"Q{index}", "gap_ids": [gap["id"]], "topic": gap["topic"],
                    "question": gap["description"], "options": [
                        {"id": "local", "label": "Aplicação local", "description": "Python e SQLite, simples para uso pessoal."},
                        {"id": "server", "label": "Servidor compartilhado", "description": "Infraestrutura para vários usuários."}],
                    "recommended_option_id": "local", "recommendation_reason": "O uso pessoal favorece simplicidade e dados locais.",
                    "goal_reference": self.goal})
            return json.dumps({"questions": questions})
        if task == "write":
            return json.dumps(self.documents.pop(0) if self.documents else final_document())
        if task == "final_review":
            return json.dumps(self.final_reviews.pop(0) if self.final_reviews else review(approved=True, stack=True))
        raise AssertionError(task)
