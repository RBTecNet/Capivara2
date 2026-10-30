import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import OptionList, Static

from harness.app import ConfirmStep, HarnessApp
from harness.artifacts import ArtifactStore, InitError, read_status, suggested_module
from harness.domain import AgentSelection, WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer
from harness.init_ui import InitExecutionStep
from harness.plan_artifacts import load_init, plan_completed
from harness.plan_engine import PlanEngine
from harness.plan_models import PlanReview
from harness.plan_contracts import PlanContractError
from init_fixture import ScriptedAgents
from plan_fixture import PlanAgents, approved_review, rejected
from test_wizard import test_catalog


class PlanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        selection = AgentSelection("codex", "test", "low")
        self.state = WizardState(self.root, prompt="Aplicação de tarefas com persistência local.",
                                 writer=selection, reviewer=selection)

        async def answer(question, index, total):
            return Answer(question_id=question.id, option_id=question.recommended_option_id, label="Local", text="")

        await InitEngine(self.state, ScriptedAgents(), interview=answer).run()
        self.state.module = "plan"
        self.state.prompt = load_init(self.root)["approved_prompt"]
        self.init_files = {p.relative_to(self.root): p.read_bytes() for p in (self.root / ".harness").rglob("*")
                           if p.is_file() and p.name not in ("status.json", ".init.lock")}

    def assert_init_preserved(self):
        for path, content in self.init_files.items():
            self.assertEqual((self.root / path).read_bytes(), content, path)

    async def test_phases_tasks_document_fidelity_fresh_context_and_build_unlock(self):
        agents = PlanAgents()
        events = []
        path = await PlanEngine(self.state, agents, on_event=events.append).run()
        text = path.read_text()
        self.assertIn("Fase 0 — Stack de implantação e dependências de desenvolvimento e testes", text)
        self.assertIn("P2-T004", text)
        plan = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual([p["number"] for p in plan["phases"]], [0, 1, 2])
        self.assertEqual([p["title"] for p in plan["phases"][1:]], ["Persistência", "Interface e operações"])
        self.assertEqual(len(plan["phases"][2]["tasks"]), 4)
        self.assertEqual(suggested_module(self.root), "build")
        self.assertEqual(read_status(self.root)["modules"]["build"]["status"], "pending")
        self.assert_init_preserved()
        self.assertEqual([p.name for p in self.root.iterdir()], [".harness"])
        for role, envelope in agents.calls:
            self.assertEqual(envelope["preserved_context"], [])
            self.assertEqual(envelope["context_policy"], "fresh")
            if role == "writer":
                data = envelope["current_request"]["data"]
                self.assertNotIn("source", data)
                self.assertNotIn("phase", data)
                self.assertNotIn("phases", data)
                self.assertIn("seed", data)
                self.assertEqual(envelope["current_request"]["task"], "write_task")
        self.assertEqual(events[-1].tasks_approved, 7)
        self.assertEqual(events[-1].phases_approved, 3)
        self.assertTrue(all(event.module == "plan" for event in events))

    async def test_only_divergent_task_is_rewritten_until_approved(self):
        agents = PlanAgents()
        agents.task_reviews = [rejected("P0-T001", "Explicite o requisito da stack aprovada.")]
        agents.task_reviews += [approved_review(),rejected("P0-T002", "Preserve apenas a dependência definida no init.")]

        events = []
        await PlanEngine(self.state, agents, on_event=events.append).run()
        writes = [data["current_request"]["data"] for role, data in agents.calls if role == "writer"]
        counts = Counter(data["task_id"] for data in writes)
        self.assertEqual(counts["P0-T001"], 2)
        self.assertEqual(counts["P0-T002"], 2)
        self.assertEqual(counts["P2-T003"], 1)
        self.assertEqual(counts["P2-T004"], 1)
        self.assertEqual(suggested_module(self.root), "build")
        states = [(item.creation, item.validation) for event in events for item in event.items if item.id == "P0-T001"]
        for expected in (("waiting", "waiting"), ("running", "waiting"), ("completed", "running"),
                         ("completed", "rejected"), ("running", "rejected"), ("completed", "completed")):
            self.assertIn(expected, states)

    async def test_missing_task_is_added_then_individually_and_collectively_reviewed(self):
        agents = PlanAgents()
        verdict = {**approved_review(), "approved": False, "coverage_complete": False,
            "additions": [{"phase_number": 2, "after_task_id": "P2-T002", "task": {
                "title": "Persistência das operações", "scope": "Conectar operações à persistência local.",
                "source_quotes": ["Gerenciar as tarefas."],
                "coverage": [{"requirement_id": "P2-A001", "excerpt": "tarefas"}]}}]}
        agents.task_repairs = {'P2-T002':[verdict]}
        path = await PlanEngine(self.state, agents).run()
        plan = json.loads(path.with_suffix(".json").read_text())
        ids = [t["id"] for t in plan["phases"][2]["tasks"]]
        self.assertEqual(ids, ["P2-T001", "P2-T002", "P2-T005", "P2-T003", "P2-T004"])
        task_reviews = [e for _, e in agents.calls if e["current_request"]["task"] == "review_task"]
        self.assertTrue(any(e["current_request"]["data"]["task"]["id"] == "P2-T005" for e in task_reviews))
        self.assertEqual(suggested_module(self.root), "build")

    async def test_content_correction_preserves_the_approved_seed(self):
        agents = PlanAgents()
        verdict = rejected("P0-T001", "Corrija também o recorte: preservar Python como stack.")
        replacement = {"title": "Runtime Python aprovado", "scope": "Preparar Python e preservar SQLite como ferramenta de persistência.",
                       "source_quotes": ["Python"], "coverage": [
                           {"requirement_id": "P0-S001", "excerpt": "Aplicação: Python. Atende ao uso local."},
                           {"requirement_id": "P0-T001", "excerpt": "SQLite: Persistir tarefas no computador."}]}
        verdict["corrections"][0]["replacement_seed"] = replacement
        agents.task_reviews = [verdict]
        final = await PlanEngine(self.state, agents).run()
        plan = json.loads(final.with_suffix(".json").read_text())
        self.assertNotEqual(plan["phases"][0]["tasks"][0]["seed"], replacement)
        writes = [e["current_request"]["data"] for role, e in agents.calls if role == "writer"
                  and e["current_request"]["data"]["task_id"] == "P0-T001"]
        self.assertEqual(writes[-1]["seed"], writes[0]["seed"])
        self.assertEqual(len(writes), 2)

    async def test_reexecution_cancellation_and_lock_preserve_init(self):
        await PlanEngine(self.state, PlanAgents()).run()
        marker = self.root / ".harness/plan/log/obsolete.txt"
        marker.write_text("antigo")
        agents = PlanAgents()
        agents.cancel = True
        await PlanEngine(self.state, agents).run()
        self.assertTrue(marker.exists())
        self.assertTrue((self.root / ".harness/plan/final.md").exists())
        self.assertEqual(agents.calls, [])
        self.assertEqual(read_status(self.root)["modules"]["plan"]["status"], "completed")
        self.assertEqual(suggested_module(self.root), "build")
        self.assert_init_preserved()
        active = PlanEngine(self.state, PlanAgents())
        active.store.begin(self.state)
        try:
            with self.assertRaisesRegex(InitError, "módulo em execução"):
                await PlanEngine(self.state, PlanAgents()).run()
            with self.assertRaisesRegex(InitError, "módulo em execução"):
                ArtifactStore(self.root).begin(self.state)
        finally:
            active.store.close()

    async def test_invalid_init_and_plan_never_unlock_modules(self):
        await PlanEngine(self.state, PlanAgents()).run()
        final = self.root / ".harness/plan/final.md"
        final.write_text(final.read_text() + "Conteúdo adulterado")
        self.assertEqual(suggested_module(self.root), "plan")
        self.assertFalse(plan_completed(self.root, load_init(self.root)["source_prompt_sha256"]))
        approved = self.root / ".harness/prompts/final.md"
        approved.write_text(approved.read_text() + "Alterado")
        before = final.read_bytes()
        with self.assertRaises(InitError):
            await PlanEngine(self.state, PlanAgents()).run()
        self.assertEqual(final.read_bytes(), before)
        self.assertEqual(suggested_module(self.root), "init")
        with tempfile.TemporaryDirectory() as directory:
            state = deepcopy(self.state)
            state.project_dir = Path(directory)
            with self.assertRaisesRegex(InitError, "Conclua o init"):
                await PlanEngine(state, PlanAgents()).run()
            self.assertEqual(list(state.project_dir.iterdir()), [])

    async def test_invalid_contract_and_unknown_corrections_return_to_reviewer_without_history(self):
        agents = PlanAgents()
        agents.invalid = 1
        agents.task_reviews = [rejected("P999-T999")]
        await PlanEngine(self.state, agents).run()
        corrected = [envelope for _, envelope in agents.calls if envelope["format_correction"]]
        self.assertGreaterEqual(len(corrected), 2)
        self.assertTrue(all(not envelope["preserved_context"] for envelope in corrected))
        bad = {**approved_review(), "no_test_strategy": False}
        with self.assertRaises(ValueError):
            PlanReview.model_validate(bad)
    async def test_source_change_during_plan_never_marks_completed(self):
        agents = PlanAgents()

        def change(operation, store):
            request=agents.calls[-1][1]['current_request']
            if operation == 'review_task' and request['data']['task']['id']=='P2-T004':
                (self.root / ".harness/prompts/final.md").write_text("alteração externa")

        agents.on_generate = change
        with self.assertRaises(InitError):
            await PlanEngine(self.state, agents).run()
        self.assertEqual(read_status(self.root)["modules"]["plan"]["status"], "failed")
        self.assertFalse((self.root / ".harness/plan/final.md").exists())

    async def test_plan_wizard_skips_source_configures_agents_and_shows_actual_dashboard(self):
        app = HarnessApp(launch_dir=self.root, catalog=test_catalog(), agents=PlanAgents())
        with patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Não executar ferramentas")):
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.press("enter")
                menu = app.screen.query_one(OptionList)
                self.assertEqual(menu.get_option_at_index(menu.highlighted).id, "plan")
                await pilot.press("enter")
                self.assertEqual(app.screen.kind, "provider")
                self.assertEqual(app.state.prompt, load_init(self.root)["approved_prompt"])
                await pilot.press("enter", "enter", "enter", "down", "enter")
                self.assertEqual(app.screen.role, "reviewer")
                await pilot.press("down", "enter", "enter", "enter")
                self.assertIsInstance(app.screen, ConfirmStep)
                self.assertEqual(app.state.reviewer.provider, "claude")
                await pilot.press("enter")
                async with asyncio.timeout(15):
                    while not isinstance(app.screen, InitExecutionStep) or app.screen.busy:
                        await pilot.pause()
                    await pilot.pause()
                self.assertEqual(app.screen.module, "plan")
                self.assertIn("plan", str(app.screen.query_one("#dash-workflow .dash-field-value", Static).render()))
                self.assertIn("7/7", str(app.screen.query_one("#metric-tasks .dash-field-value", Static).render()))
                self.assertIn("3/3", str(app.screen.query_one("#metric-phases .dash-field-value", Static).render()))
                self.assertFalse(app.screen.query("#metric-questions"))
                for width in (100, 80, 60):
                    await pilot.resize_terminal(width, 30)
                    await pilot.pause()
                    dashboard = app.screen.query_one("#init-dashboard")
                    self.assertLessEqual(dashboard.virtual_size.width, dashboard.size.width)
                self.assertEqual(suggested_module(self.root), "build")
