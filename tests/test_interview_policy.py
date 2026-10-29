import json
from pathlib import Path
import tempfile
import unittest

from harness.domain import AgentSelection, WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer, Question
from harness.interview_policy import internal_question_reason
from init_fixture import ScriptedAgents, final_document, review


QA_GAP = {"id": "test_tools", "description": "Definir ferramentas para testes automatizados e fluxos reais no navegador.", "topic": "tools"}
PRODUCT_GAP = {"id": "storage", "description": "Como os dados das tarefas devem ser guardados?", "topic": "tools"}


def qa_question():
    # Regressão da entrevista relatada: alternativas são ferramentas de QA,
    # apesar de terem sido apresentadas como ferramentas do projeto.
    return {"id": "qa", "gap_ids": ["test_tools"], "topic": "tools",
            "question": "Quais ferramentas do projeto devem ser usadas para atender aos testes automatizados e aos fluxos reais no navegador?",
            "options": [
                {"id": "vitest_playwright", "label": "Vitest e Playwright", "description": "Testes automatizados e navegador real."},
                {"id": "jest_playwright", "label": "Jest e Playwright", "description": "Testes automatizados e navegador real."},
                {"id": "vitest_cypress", "label": "Vitest e Cypress", "description": "Testes automatizados e navegador real."}],
            "recommended_option_id": "vitest_playwright", "recommendation_reason": "Validar os requisitos de qualidade.",
            "goal_reference": "tarefas"}


class InternalToolsAgents(ScriptedAgents):
    def __init__(self, *, mixed=False):
        super().__init__()
        self.initial = review(stack=True, gaps=[QA_GAP, PRODUCT_GAP] if mixed else [QA_GAP])
        self.mixed = mixed
        self.rejected_question = False

    async def generate(self, selection, role, prompt, schema, store, call_id):
        envelope = json.loads(prompt.rsplit("\n\nREQUEST:\n", 1)[1])
        if envelope["current_request"]["task"] == "interview":
            self.calls.append((role, envelope))
            questions = []
            if self.mixed:
                questions.append({"id": "storage", "gap_ids": ["storage"], "topic": "tools",
                    "question": PRODUCT_GAP["description"], "options": [
                        {"id": "local", "label": "SQLite local", "description": "Dados no computador do usuário."},
                        {"id": "server", "label": "Servidor compartilhado", "description": "Dados acessíveis por várias pessoas."}],
                    "recommended_option_id": "local", "recommendation_reason": "Atende à persistência local das tarefas.",
                    "goal_reference": "tarefas"})
            if not self.rejected_question:
                self.rejected_question = True
                return json.dumps({"questions": [qa_question(), *questions], "inferred_decisions": []})
            if "inferred_decisions" not in envelope["format_correction"]:
                raise AssertionError("O escritor precisa receber o motivo e a orientação de inferência.")
            return json.dumps({"questions": questions, "inferred_decisions": [{
                "gap_ids": ["test_tools"], "scope": "harness",
                "decision": "Usar Pytest para a stack Python e Playwright para os fluxos no navegador nas etapas futuras.",
                "reason": "Atende à stack e aos critérios de qualidade do pedido sem exigir conhecimento técnico do usuário."}]})
        if envelope["current_request"]["task"] == "write":
            document = final_document()
            document["tools"].append({"name": "Pytest", "purpose": "Validar o código Python nas etapas futuras.", "scope": "harness"})
            self.documents.append(document)
        return await super().generate(selection, role, prompt, schema, store, call_id)


class InterviewPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        selection = AgentSelection("codex", "test-model", "low")
        self.state = WizardState(self.root, prompt="Aplicação de tarefas em Python com SQLite e testes automatizados.",
                                 writer=selection, reviewer=selection)
        self.presented = []

    async def answer(self, question, index, total):
        self.presented.append(question)
        return Answer(question_id=question.id, option_id=question.recommended_option_id, label="Recomendada", text="")

    async def test_reported_test_suite_question_is_inferred_without_reaching_user(self):
        agents = InternalToolsAgents()
        result = await InitEngine(self.state, agents, interview=self.answer).run()
        self.assertEqual(self.presented, [])
        inferred = json.loads((self.root / ".harness/control/inferred-decisions.json").read_text())
        self.assertEqual(inferred[0]["decisions"][0]["scope"], "harness")
        self.assertIn("Pytest", result.read_text())
        self.assertIn("Ferramentas internas do harness", result.read_text())
        for role, envelope in agents.calls:
            if envelope["current_request"]["task"] in ("write", "final_review"):
                self.assertEqual(envelope["current_request"]["data"]["inferred_decisions"], inferred)

    async def test_product_decision_is_kept_while_internal_qa_is_inferred(self):
        await InitEngine(self.state, InternalToolsAgents(mixed=True), interview=self.answer).run()
        self.assertEqual([question.id for question in self.presented], ["storage"])
        record = json.loads((self.root / ".harness/enterviews/round-0001.json").read_text())
        self.assertEqual(len(record["answers"]), 1)
        self.assertEqual(len(record["inferred_decisions"]), 1)

    async def test_invalid_original_quote_is_corrected_by_reviewer_without_creating_interview(self):
        class InvalidQuoteAgents(ScriptedAgents):
            def __init__(self):
                super().__init__()
                self.initial = review(stack=True)
                self.bad_quote_sent = False

            async def generate(inner, selection, role, prompt, schema, store, call_id):
                envelope = json.loads(prompt.rsplit("\n\nREQUEST:\n", 1)[1])
                if envelope["current_request"]["task"] == "initial_review" and not inner.bad_quote_sent:
                    inner.bad_quote_sent = True
                    inner.calls.append((role, envelope))
                    bad = review(stack=True)
                    bad["technology"]["tools_quote"] = "Python\n\nSQLite"
                    return json.dumps(bad)
                return await super().generate(selection, role, prompt, schema, store, call_id)

        agents = InvalidQuoteAgents()
        await InitEngine(self.state, agents, interview=self.answer).run()
        self.assertEqual(self.presented, [])
        initial_calls = [data for _, data in agents.calls if data["current_request"]["task"] == "initial_review"]
        self.assertEqual(len(initial_calls), 2)
        self.assertIn("não transforme um erro de citação em nova entrevista", initial_calls[1]["format_correction"])

    async def test_final_document_clarification_does_not_repeat_stack_interview(self):
        self.state.prompt = "Aplicação de tarefas com persistência local."
        agents = ScriptedAgents([review(stack=False)])
        engine = InitEngine(self.state, agents, interview=self.answer)
        await engine.run()
        self.assertEqual(engine.round, 2)
        self.assertEqual(len(self.presented), 2)
        self.assertEqual(len(list((self.root / ".harness/enterviews").glob("*.json"))), 1)
        writes = [data["current_request"]["data"] for _, data in agents.calls if data["current_request"]["task"] == "write"]
        self.assertIn("Corrija a documentação sem repetir perguntas", writes[1]["review"]["deviations"][-1])

    async def test_approved_review_with_nonliteral_stack_quote_is_repaired_without_reinterview(self):
        bad = review(approved=True, stack=True)
        bad["technology"]["stack_quote"] = "- **Aplicação: Python.**\n- **Banco: SQLite.**"
        agents = ScriptedAgents([bad])
        agents.initial = review(stack=True)
        engine = InitEngine(self.state, agents, interview=self.answer)
        await engine.run()
        self.assertEqual(self.presented, [])
        self.assertEqual(engine.round, 1)
        final_calls = [data for _, data in agents.calls if data["current_request"]["task"] == "final_review"]
        self.assertEqual(len(final_calls), 2)
        self.assertIn("não transforme um erro de citação em nova entrevista", final_calls[1]["format_correction"])
        approval = json.loads((self.root / ".harness/control/approval.json").read_text())
        self.assertTrue(approval["review"]["approved"])
        self.assertEqual(approval["review"]["gaps"], [])

    def test_tool_policy_does_not_block_product_features_or_runtime_choices(self):
        question = qa_question()
        self.assertIsNotNone(internal_question_reason(Question.model_validate(question)))
        question.update(topic="requirements", question="Quais integrações de testes a plataforma deve oferecer aos clientes?")
        self.assertIsNone(internal_question_reason(Question.model_validate(question)))
        question.update(topic="tools", question="Qual banco deve armazenar os dados do produto?", options=[
            {"id": "sqlite", "label": "SQLite", "description": "Armazenamento local."},
            {"id": "postgres", "label": "PostgreSQL", "description": "Servidor de banco de dados."}], recommended_option_id="sqlite")
        self.assertIsNone(internal_question_reason(Question.model_validate(question)))
        question.update(question="Qual suíte de testes gostaria de usar?")
        self.assertIsNotNone(internal_question_reason(Question.model_validate(question)))


if __name__ == "__main__":
    unittest.main()
