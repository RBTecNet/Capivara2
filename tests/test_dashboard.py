import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from textual.widgets import Button, RichLog

from harness.app import HarnessApp
from harness.dashboard import DashboardField, InitDashboard
from harness.domain import AgentSelection, WizardState
from harness.init_engine import InitEngine
from harness.init_models import Answer
from harness.init_ui import InitExecutionStep, InterviewStep
from init_fixture import ScriptedAgents, final_document, review


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_counters_distinguish_agent_retries_from_document_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            selection = AgentSelection("codex", "test-model", "low")
            state = WizardState(Path(directory), prompt="Aplicação de tarefas.", writer=selection, reviewer=selection)
            agents = ScriptedAgents()
            document = final_document()
            document["phases"][0]["depends_on"] = [2]
            agents.documents = [document]
            events = []

            async def answer(question, index, total):
                return Answer(question_id=question.id, option_id=question.recommended_option_id, label="Recomendada", text="")

            await InitEngine(state, agents, interview=answer, on_event=events.append).run()
            final = events[-1]
            self.assertEqual(final.stage, "completed")
            self.assertEqual(final.operations, 4)
            self.assertEqual(final.invocations, 5)
            self.assertEqual(final.corrections, 1)
            self.assertEqual((final.questions, final.answers), (2, 2))
            started = [event.active_role for event in events if event.kind == "agent_started"]
            self.assertEqual(started, ["reviewer", "writer", "writer", "writer", "reviewer"])
            self.assertIsNone(final.active_role)

    async def test_live_dashboard_has_real_metadata_and_keeps_agent_content_out_of_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = "CONTEUDO_PRIVADO_NAO_EXIBIR"
            rejection = review(stack=True, deviations=["Restaure um requisito do pedido."])
            rejection["summary"] = private
            agents = ScriptedAgents([rejection])
            app = HarnessApp(launch_dir=root, agents=agents)
            selection = AgentSelection("codex", "test-model", "low")
            app.state = WizardState(root, prompt=f"Aplicação de tarefas. {private}", writer=selection, reviewer=selection)
            async with app.run_test(size=(170, 54)) as pilot:
                await pilot.pause()
                await app.push_screen(InitExecutionStep())
                async with asyncio.timeout(15):
                    while True:
                        await pilot.pause()
                        if isinstance(app.screen, InterviewStep) and app.screen.is_mounted:
                            await pilot.press("enter")
                        elif isinstance(app.screen, InitExecutionStep) and app.screen.final_path and not app.screen.busy:
                            break
                dashboard = app.screen.query_one(InitDashboard)
                self.assertEqual(dashboard.run_status, "completed")
                self.assertEqual(dashboard.event.invocations, 6)
                self.assertEqual(dashboard.event.operations, 6)
                self.assertEqual(dashboard.event.corrections, 1)
                self.assertEqual(dashboard.event.round, 2)
                self.assertEqual(dashboard.event.last_role, "reviewer")
                self.assertIn("não medido", str(dashboard.query_one("#metric-tokens", DashboardField).query_one(".dash-field-value").render()))
                log = "\n".join(line.text for line in dashboard.query_one(RichLog).lines)
                self.assertNotIn(private, log)
                self.assertIn("revisão documental", log)
                self.assertEqual(json.loads((root / ".harness/status.json").read_text())["modules"]["init"]["status"], "completed")
                for width, height in ((100, 38), (80, 24), (60, 26)):
                    await pilot.resize_terminal(width, height)
                    await pilot.pause()
                    self.assertEqual(dashboard.max_scroll_x, 0)
                close = app.screen.query_one("#init-close", Button)
                close.focus()
                await pilot.pause()
                self.assertGreaterEqual(close.region.y, 0)
                self.assertLessEqual(close.region.bottom, app.size.height)
                await pilot.press("enter")
            self.assertIs(app.return_value, app.state)
