"""Integração da TUI real com executores simulados e projetos temporários."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Input, RadioButton, Select, TextArea

from capivara2ptn.app import CapivaraApp
from capivara2ptn.models import ModelCatalog
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.reverse_ui import ReverseControls, ReversePanel, ReverseDirectoryDialog, ReverseDomainsDialog, ReverseConfirmDialog
from test_reverse import FakeRunner


class ReverseUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_module_switching_keeps_existing_views_and_clocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('pathlib.Path.cwd', return_value=root), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture'))):
                app = CapivaraApp()
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    self.assertFalse(app.query_one(ReverseControls).display)
                    self.assertTrue(app.query_one(PromptSource).display)
                    original_clock = app._clock
                    app.query_one('#module-reverse', RadioButton).value = True
                    await pilot.pause()
                    self.assertTrue(app.query_one(ReverseControls).display)
                    self.assertTrue(app.query_one(ReversePanel).display)
                    self.assertFalse(app.query_one(PromptSource).display)
                    self.assertIsNone(app.reverse.store.state)
                    self.assertFalse(app.reverse.running)
                    app.query_one('#module-plan', RadioButton).value = True
                    await pilot.pause()
                    self.assertTrue(app.has_class('planning'))
                    self.assertTrue(app.query_one('#plan-progress-area').display)
                    app.query_one('#module-reverse', RadioButton).value = True
                    await pilot.pause()
                    self.assertFalse(app.query_one('#plan-progress-area').display)
                    self.assertFalse(app.query_one('#plan-count').visible and app.query_one('#plan-progress-area').display)
                    app.query_one('#module-plan', RadioButton).value = True
                    await pilot.pause()
                    self.assertTrue(app.query_one('#plan-progress-area').display)
                    self.assertFalse(app.query_one(ReversePanel).display)
                    app.query_one('#module-init', RadioButton).value = True
                    await pilot.pause()
                    self.assertTrue(app.query_one(PromptSource).display)
                    self.assertFalse(app.has_class('planning'))
                    self.assertIs(app._clock, original_clock)
                    self.assertEqual(app._clock.totals, {})
                    self.assertEqual(app._clock.active, None)
                    # Verifica também a vista estreita existente.
                    await pilot.resize_terminal(80, 35)
                    await pilot.pause()
                    self.assertTrue(app.has_class('narrow'))

    async def test_directory_picker_navigation_and_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / 'legado'
            child.mkdir()
            with patch('pathlib.Path.cwd', return_value=root), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([], 'fixture', True))):
                app = CapivaraApp()
                async with app.run_test(size=(110, 40)) as pilot:
                    app.query_one('#module-reverse', RadioButton).value = True
                    await pilot.pause()
                    app.push_screen(ReverseDirectoryDialog(root))
                    await pilot.pause()
                    dialog = app.screen
                    self.assertIsInstance(dialog, ReverseDirectoryDialog)
                    dialog.query_one('#reverse-folder-path', Input).value = str(child)
                    dialog.navigate()
                    self.assertEqual(dialog.path(), child)
                    dialog.go_parent()
                    self.assertEqual(dialog.path(), root)
                    dialog.cancel()
                    await pilot.pause()

    async def test_reverse_start_approval_completion_and_init_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dest, source = root / 'novo', root / 'legado'
            dest.mkdir()
            source.mkdir()
            (source / 'precos.py').write_text('def calcular(v):\n    return v * .9\n')
            fake = FakeRunner()
            with patch('pathlib.Path.cwd', return_value=dest), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture'))), \
                 patch('capivara2ptn.reverse_ui.CliRunner', return_value=fake), \
                 patch.object(CapivaraApp, 'execute_init') as init_executor, patch.object(CapivaraApp, 'execute_plan') as plan_executor:
                app = CapivaraApp()
                async with app.run_test(size=(120, 45)) as pilot:
                    app.query_one('#model', Select).value = 'fixture'
                    app.query_one('#effort', Select).value = 'High'
                    app.query_one('#module-reverse', RadioButton).value = True
                    app.query_one('#reverse-source', Input).value = str(source)
                    await pilot.pause()
                    app.start_pressed()
                    for _ in range(60):
                        if isinstance(app.screen, ReverseDomainsDialog):
                            break
                        await pilot.pause(0.02)
                    self.assertIsInstance(app.screen, ReverseDomainsDialog)
                    self.assertEqual(len(fake.calls), 1)
                    app.screen.confirm()
                    for _ in range(60):
                        if isinstance(app.screen, ReverseConfirmDialog):
                            break
                        await pilot.pause(0.02)
                    self.assertIsInstance(app.screen, ReverseConfirmDialog)
                    self.assertEqual(app.reverse.store.state['status'], 'done')
                    app.screen.confirm()
                    await pilot.pause()
                    self.assertTrue(app.query_one('#module-init', RadioButton).value)
                    self.assertEqual(app.query_one('#prompt-source', Select).value, 'file')
                    self.assertEqual(app.query_one('#prompt-path', Input).value, str(dest / '.harness/reverse/prompt-final.md'))
                    self.assertEqual(app.query_one('#model', Select).value, 'fixture')
                    self.assertEqual(app.query_one('#effort', Select).value, 'High')
                    self.assertFalse(app.reverse.running)
                    self.assertFalse(app.query_one('#configuration').disabled)
                    init_executor.assert_not_called()
                    plan_executor.assert_not_called()
                    self.assertEqual(app._clock.totals, {})
                    self.assertFalse((source / '.harness').exists())

    async def test_interrupt_and_reopen_preserve_progress_without_auto_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dest, source = root / 'novo', root / 'legado'
            dest.mkdir()
            source.mkdir()
            (source / 'precos.py').write_text('def calcular(v):\n    return v * .9\n')
            (source / 'estoque.py').write_text('def reservar(v):\n    return v > 0\n')
            waiting = asyncio.Event()
            class BlockingRunner(FakeRunner):
                async def invoke(inner, instructions, data, **kwargs):
                    if 'chunk' in data and data['path'] == 'precos.py':
                        waiting.set()
                        await asyncio.Event().wait()
                    return await super().invoke(instructions, data, **kwargs)
            fake = BlockingRunner()
            with patch('pathlib.Path.cwd', return_value=dest), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture'))), \
                 patch('capivara2ptn.reverse_ui.CliRunner', return_value=fake):
                app = CapivaraApp()
                async with app.run_test(size=(120, 45)) as pilot:
                    app.query_one('#model', Select).value = 'fixture'
                    app.query_one('#module-reverse', RadioButton).value = True
                    app.query_one('#reverse-source', Input).value = str(source)
                    await pilot.pause()
                    app.start_pressed()
                    for _ in range(50):
                        if isinstance(app.screen, ReverseDomainsDialog):
                            break
                        await pilot.pause(.02)
                    app.screen.confirm()
                    await asyncio.wait_for(waiting.wait(), timeout=5)
                    self.assertEqual(len(app.reverse.store.state['units']), 1)
                    app.query_one('#reverse-stop', Button).press()
                    for _ in range(50):
                        if not app.reverse.running:
                            break
                        await pilot.pause(.02)
                    self.assertFalse(app.reverse.running)
                    self.assertFalse(app.query_one('#configuration').disabled)
                    self.assertEqual(app.reverse.store.state['status'], 'interrupted')
                    self.assertEqual(len(app.reverse.store.state['units']), 1)
                    count = len(fake.calls)
                reopened = CapivaraApp()
                async with reopened.run_test(size=(120, 45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(reopened.query_one('#reverse-source', Input).value, str(source))
                    self.assertEqual(len(reopened.reverse.store.state['units']), 1)
                    self.assertFalse(reopened.reverse.running)
                    self.assertEqual(len(fake.calls), count)
                    reopened.query_one('#module-reverse', RadioButton).value = True
                    await pilot.pause()
                    self.assertEqual(str(reopened.query_one('#start', Button).label), 'Retomar')
                    self.assertEqual(len(fake.calls), count)


if __name__ == '__main__':
    unittest.main()
