from pathlib import Path
import unittest

from textual.app import App, ComposeResult
from textual.widgets import ProgressBar, Static

from harness.dashboard import InitDashboard
from harness.domain import AgentSelection
from harness.plan_progress import COLORS, PlanItemScroll, PlanProgress, indicator
from harness.run_events import InitEvent, PlanItem


class PlanDashboardApp(App):
    def compose(self) -> ComposeResult:
        agent = AgentSelection("codex", "test", "low")
        yield InitDashboard(Path("/projeto/exemplo"), {"writer": agent, "reviewer": agent}, module="plan")


class PlanProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_status_dots_colors_progress_scroll_and_resize(self):
        items = [PlanItem("P0", "Stack e dependências", "phase", "completed", "completed")]
        for i in range(1, 41):
            creation, validation = ("completed", "completed") if i < 20 else (
                ("running", "rejected") if i == 20 else ("waiting", "waiting"))
            items.append(PlanItem(f"P0-T{i:03d}", f"Documentar o recorte {i}", "task", creation, validation))
        app = PlanDashboardApp()
        async with app.run_test(size=(150, 54)) as pilot:
            await pilot.pause()
            dashboard = app.query_one(InitDashboard)
            dashboard.start()
            event = InitEvent("items_changed", "writing_task", 2, "writer", "writer", 5, 5, 1, 0, 0, 0,
                              module="plan", phase_number=0, phases_total=1, phases_approved=0,
                              tasks_total=40, tasks_approved=19, items=tuple(items))
            dashboard.update_event(event)
            await pilot.pause()
            tree = app.query_one(PlanProgress)
            bar = tree.query_one(ProgressBar)
            self.assertEqual(bar.total, 41)
            self.assertEqual(bar.progress, 20)
            rows = tree.query_one("#plan-item-rows", Static).render()
            self.assertEqual(len(rows.plain.splitlines()), 41)
            for row in rows.plain.splitlines():
                self.assertEqual(row.count("○") + row.count("●"), 2)
            self.assertEqual(indicator("waiting").plain, "○")
            for state, color in COLORS.items():
                self.assertEqual(str(indicator(state).style), color)
            scroll = tree.query_one(PlanItemScroll)
            self.assertGreater(scroll.max_scroll_y, 0)
            self.assertGreater(scroll.scroll_y, 0)
            scroll.focus()
            scroll.scroll_home(animate=False, immediate=True)
            await pilot.pause()
            dashboard.update_event(event)
            await pilot.pause()
            self.assertEqual(scroll.scroll_y, 0)
            await pilot.press("end")
            await pilot.pause()
            self.assertGreater(scroll.scroll_y, 0)
            for width, height in ((100, 38), (80, 24), (60, 26)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                self.assertEqual(dashboard.max_scroll_x, 0)
                self.assertEqual(scroll.max_scroll_x, 0)
                rows = tree.query_one("#plan-item-rows", Static).render()
                self.assertTrue(all(line.count("○") + line.count("●") == 2 for line in rows.plain.splitlines()))
