import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError
from textual.widgets import OptionList, Static, TextArea

from harness.app import HarnessApp
from harness.artifacts import InitError, read_status, suggested_module
from harness.domain import AgentSelection, WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer, FinalPrompt, Interview, Review
from harness.init_ui import InitExecutionStep, InterviewStep
from init_fixture import ScriptedAgents, final_document, review


class InitDocumentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        selection = AgentSelection("codex", "test-model", "low")
        self.state = WizardState(self.root, prompt="Aplicação de tarefas com persistência local.", writer=selection, reviewer=selection)
        self.questions = []

    async def recommend(self, question, index, total):
        self.questions.append(question)
        return Answer(question_id=question.id, option_id=question.recommended_option_id,
                      label="Opção recomendada", text="")

    async def test_final_prompt_preserves_intent_has_phase_zero_and_no_open_gaps(self):
        agents = ScriptedAgents()
        engine = InitEngine(self.state, agents, interview=self.recommend)
        result = await engine.run()
        text = result.read_text()
        self.assertIn("Adicionar, editar, concluir e excluir tarefas", text)
        self.assertIn("Persistir os dados localmente", text)
        self.assertLess(text.index("## Fase 0 — Stack e ferramentas"), text.index("## Fase 1"))
        self.assertLess(text.index("## Fase 1"), text.index("## Fase 2"))
        self.assertEqual({question.topic for question in self.questions}, {"stack", "tools"})
        self.assertTrue(all(question.recommended_option_id == "local" for question in self.questions))
        approval = json.loads((self.root / ".harness/control/approval.json").read_text())
        self.assertEqual(approval["review"]["gaps"], [])
        self.assertTrue(approval["review"]["approved"])
        self.assertEqual(read_status(self.root)["modules"]["init"]["status"], "completed")
        self.assertEqual(suggested_module(self.root), "plan")
        self.assertEqual([path.name for path in self.root.iterdir()], [".harness"])

    async def test_reviewer_context_is_restored_and_new_gap_gets_another_interview(self):
        new_gap = {"id": "editing", "description": "Definir como editar tarefas.", "topic": "requirements"}
        agents = ScriptedAgents([review(gaps=[new_gap], stack=True)])
        engine = InitEngine(self.state, agents, interview=self.recommend)
        await engine.run()
        self.assertEqual(engine.round, 2)
        interviews = sorted((self.root / ".harness/enterviews").glob("*.json"))
        self.assertEqual(len(interviews), 2)
        self.assertEqual(len(json.loads(interviews[0].read_text())["answers"]), 2)
        self.assertEqual(json.loads(interviews[1].read_text())["questions"][0]["gap_ids"], ["editing"])
        finals = [payload for role, payload in agents.calls if role == "reviewer" and payload["current_request"]["task"] == "final_review"]
        history = finals[-1]["preserved_context"]
        self.assertEqual(history[0]["content"]["task"], "initial_review")
        self.assertTrue(any(message["role"] == "assistant" for message in history))
        self.assertIn("Aplicação de tarefas", json.dumps(history, ensure_ascii=False))

    async def test_deviation_and_impossibility_are_corrected_without_a_round_cap(self):
        rejected = [review(stack=True, deviations=["O candidato omitiu excluir tarefas. Restaure esse requisito."]),
                    review(stack=True, feasible=False)]
        rejected += [review(stack=True, deviations=["Restaure a persistência local solicitada."])] * 3
        agents = ScriptedAgents(rejected)
        engine = InitEngine(self.state, agents, interview=self.recommend)
        await engine.run()
        self.assertEqual(engine.round, 6)
        writes = [payload["current_request"]["data"] for role, payload in agents.calls
                  if role == "writer" and payload["current_request"]["task"] == "write"]
        self.assertIn("omitiu excluir", writes[1]["review"]["deviations"][0])
        self.assertIn("impossível", writes[2]["review"]["infeasibilities"][0])
        self.assertEqual(len(self.questions), 2)
        self.assertTrue((self.root / ".harness/prompts/round-0006.md").is_file())

    async def test_invalid_approval_and_non_executable_phases_are_corrected_before_approval(self):
        bad = review(approved=True, stack=True)
        bad["gaps"] = [{"id": "missing", "description": "Requisito não atendido", "topic": "requirements"}]
        with self.assertRaises(ValidationError):
            Review.model_validate(bad)
        document = final_document()
        document["phases"][0]["depends_on"] = [2]
        with self.assertRaises(ValidationError):
            FinalPrompt.model_validate(document)
        agents = ScriptedAgents()
        agents.documents = [deepcopy(document) for _ in range(3)]
        await InitEngine(self.state, agents, interview=self.recommend).run()
        events = [json.loads(line) for line in (self.root / ".harness/log/events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(event["stage"] == "format_correction" for event in events), 3)
        approved = json.loads((self.root / ".harness/prompts/final.json").read_text())
        self.assertEqual(approved["phases"][0]["depends_on"], [0])

    async def test_reexecution_replaces_documentation_and_preserves_application(self):
        application = self.root / "app.txt"
        application.write_text("aplicação existente")
        await InitEngine(self.state, ScriptedAgents(), interview=self.recommend).run()
        marker = self.root / ".harness/log/obsolete.log"
        marker.write_text("registro anterior")
        self.state.prompt = "Novas tarefas com persistência local."
        await InitEngine(self.state, ScriptedAgents(), interview=self.recommend).run()
        self.assertFalse(marker.exists())
        self.assertEqual(application.read_text(), "aplicação existente")
        self.assertEqual((self.root / ".harness/prompts/initial.md").read_text(), self.state.prompt)
        self.assertEqual(read_status(self.root)["modules"]["plan"]["status"], "pending")
        self.assertEqual(suggested_module(self.root), "plan")

    async def test_unanswered_gap_never_produces_an_approved_prompt(self):
        async def cancel(question, index, total):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await InitEngine(self.state, ScriptedAgents(), interview=cancel).run()
        self.assertFalse((self.root / ".harness/prompts/final.md").exists())
        self.assertEqual(read_status(self.root)["modules"]["init"]["status"], "cancelled")
        self.assertEqual(suggested_module(self.root), "init")


class InterviewUITests(unittest.IsolatedAsyncioTestCase):
    async def test_exactly_one_free_option_recommendation_and_multiline_answer(self):
        agents = ScriptedAgents()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = AgentSelection("codex", "test-model", "low")
            app = HarnessApp(launch_dir=root, agents=agents)
            app.state = WizardState(root, prompt="Aplicação de tarefas.", writer=selection, reviewer=selection)
            async with app.run_test(size=(100, 38)) as pilot:
                await pilot.pause()
                await app.push_screen(InitExecutionStep())
                async with asyncio.timeout(10):
                    while not (isinstance(app.screen, InterviewStep) and app.screen.is_mounted and app.screen.query(OptionList)):
                        await pilot.pause()
                menu = app.screen.query_one(OptionList)
                self.assertEqual(menu.option_count, 3)
                self.assertEqual(menu.get_option_at_index(menu.highlighted).id, "local")
                self.assertIn("Recomendada", str(menu.get_option("local").prompt))
                await pilot.press("end", "enter", "P", "y", "t", "h", "o", "n", "enter", "S", "Q", "L", "i", "t", "e")
                self.assertIsInstance(app.screen, InterviewStep)
                self.assertEqual(app.screen.query_one(TextArea).text, "Python\nSQLite")
                await pilot.press("ctrl+d")
                async with asyncio.timeout(10):
                    while True:
                        await pilot.pause()
                        if isinstance(app.screen, InterviewStep):
                            await pilot.press("enter")
                        elif isinstance(app.screen, InitExecutionStep) and not app.screen.busy:
                            break
                self.assertTrue((root / ".harness/prompts/final.md").exists())
                answers = json.loads((root / ".harness/enterviews/round-0001.json").read_text())["answers"]
                self.assertEqual(answers[0]["option_id"], "other")
                self.assertEqual(answers[0]["text"], "Python\nSQLite")
                app.screen.action_back()
                await pilot.pause()
                app.show_functions()
                await pilot.pause()
                choices = app.screen.query_one(OptionList)
                self.assertEqual(choices.get_option_at_index(choices.highlighted).id, "plan")


if __name__ == "__main__":
    unittest.main()
