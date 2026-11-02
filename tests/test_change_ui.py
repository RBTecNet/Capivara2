"""Integração mínima do CHANGE com a TUI real e os módulos protegidos."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, RadioButton, RadioSet, Select, TextArea
from capivara2ptn.app import CapivaraApp
from capivara2ptn.change_ui import ChangeControls, ChangePanel, ChangeRequestDialog, ChangeClarificationDialog
from capivara2ptn.models import ModelCatalog
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.reverse_ui import ReversePanel
from test_change import FakeChangeRunner, REQUEST, SOURCE, RESULT


class ChangeUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capivara-change-ui-')
        self.root = Path(self.temp.name)
        self.cwd = self.root / 'project'
        self.cwd.mkdir()
        (self.cwd / 'app.py').write_text(SOURCE)
        self.cwd_patch = patch('pathlib.Path.cwd', return_value=self.cwd)
        self.cfg_patch = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.models = patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture')))
        self.cwd_patch.start();self.cfg_patch.start();self.models.start()
    def tearDown(self):
        self.models.stop();self.cfg_patch.stop();self.cwd_patch.stop();self.temp.cleanup()

    async def test_register_select_and_reopen_without_starting_models(self):
        app = CapivaraApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            module = app.query_one('#module', RadioSet)
            change = app.query_one('#module-change', RadioButton)
            self.assertLessEqual(change.region.right, module.content_region.right, f'{change.region} outside {module.content_region}')
            self.assertTrue(app.query_one(PromptSource).display)
            change.value = True
            await pilot.pause()
            self.assertTrue(app.query_one(ChangeControls).display)
            self.assertTrue(app.query_one(ChangePanel).display)
            self.assertFalse(app.query_one(ReversePanel).display)
            self.assertFalse(app.query_one('#plan-progress-area').display)
            self.assertGreaterEqual(app.query_one('#log-card').region.height, 5)
            self.assertLessEqual(app.query_one('#log-card').region.bottom, app.query_one('#monitor').region.bottom)
            app.change.create_dialog()
            await pilot.pause()
            self.assertIsInstance(app.screen, ChangeRequestDialog)
            app.screen.query_one(TextArea).load_text(REQUEST)
            app.screen.confirm()
            await pilot.pause()
            self.assertEqual(app.change.store.identifier, 'change-001')
            self.assertEqual(app.change.store.state['status'], 'draft')
            self.assertFalse(app.change.running)
            self.assertFalse((self.cwd / '.harness/dados').exists())
            app.change.create_dialog()
            await pilot.pause()
            app.screen.query_one(TextArea).load_text('Acrescentar fornecedores seguindo os cadastros existentes.')
            app.screen.confirm()
            await pilot.pause()
            self.assertEqual(app.change.store.identifier, 'change-002')
            self.assertEqual(len(app.change.store.requests()), 2)
            app.query_one('#module-reverse', RadioButton).value = True
            await pilot.pause()
            self.assertTrue(app.query_one(ReversePanel).display)
            self.assertFalse(app.query_one(ChangePanel).display)
            app.query_one('#module-init', RadioButton).value = True
            await pilot.pause()
            self.assertTrue(app.query_one(PromptSource).display)
            self.assertEqual(app._clock.totals, {})
        reopened = CapivaraApp()
        async with reopened.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            self.assertFalse(reopened.change.running)
            reopened.query_one('#module-change', RadioButton).value = True
            await pilot.pause()
            self.assertEqual(reopened.change.store.identifier, 'change-002')
            self.assertEqual(reopened.change.store.state['status'], 'draft')

    async def test_executes_directly_without_plan_auditor_or_approval(self):
        runner = FakeChangeRunner(self.cwd)
        with patch('capivara2ptn.change_ui.CliRunner', return_value=runner) as adapter, \
             patch.object(CapivaraApp, 'execute_build') as build, patch.object(CapivaraApp, 'execute_init') as init:
            app = CapivaraApp()
            async with app.run_test(size=(120, 45)) as pilot:
                await pilot.pause()
                app.change.store.create(REQUEST)
                app.change.reload(app.change.store.identifier)
                app.query_one('#module-change', RadioButton).value = True
                app.query_one('#model', Select).value = 'fixture'
                await pilot.pause()
                app.start_pressed()
                for _ in range(100):
                    if not app.change.running:break
                    self.assertNotIsInstance(app.screen, ChangeClarificationDialog)
                    await pilot.pause(.02)
                self.assertFalse(app.change.running)
                self.assertEqual(app.change.store.state['status'], 'done')
                self.assertFalse(app.query_one('#configuration').disabled)
                self.assertGreater(app.change.store.elapsed(), 0)
                self.assertEqual(app._clock.totals, {})
                self.assertIn('phone.replace', (self.cwd / 'app.py').read_text())
                self.assertEqual(len(runner.calls), 1)
                self.assertEqual(runner.calls[0][2]['access'], 'write')
                adapter.assert_called_once()
                self.assertEqual(adapter.call_args.args[1], self.cwd)
                self.assertEqual(app.change.store.state['tasks'], [])
                self.assertFalse(app.change.store.path('plano.md').exists())
                build.assert_not_called();init.assert_not_called()

    async def test_stop_preserves_partial_work_without_approved_plan(self):
        waiting = asyncio.Event()
        class WaitingRunner(FakeChangeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                await super(WaitingRunner, inner).invoke(instructions, data, **kwargs)
                waiting.set()
                await asyncio.Event().wait()
        with patch('capivara2ptn.change_ui.CliRunner', return_value=WaitingRunner(self.cwd)):
            app = CapivaraApp()
            async with app.run_test(size=(120, 45)) as pilot:
                await pilot.pause()
                app.change.store.create(REQUEST)
                app.change.reload(app.change.store.identifier)
                app.query_one('#module-change', RadioButton).value = True
                app.query_one('#model', Select).value = 'fixture'
                await pilot.pause()
                app.start_pressed()
                await asyncio.wait_for(waiting.wait(), 8)
                app.query_one('#change-stop', Button).press()
                for _ in range(70):
                    if not app.change.running:break
                    await pilot.pause(.02)
                self.assertEqual(app.change.store.state['status'], 'interrupted')
                self.assertIsNone(app.change.store.state['approval'])
                self.assertTrue(app.change.store.state['direct']['changes'])
                self.assertIn('phone.replace', (self.cwd / 'app.py').read_text())
                self.assertFalse(app.query_one('#configuration').disabled)
