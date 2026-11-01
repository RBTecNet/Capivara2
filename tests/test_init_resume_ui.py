"""A TUI restaura INIT incompleto sem execução automática ou nova origem externa."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Input, Select

from capivara2ptn.app import CapivaraApp
from capivara2ptn.dialogs import ContinueDialog, InterviewDialog, ResultDialog
from capivara2ptn.models import ModelCatalog
from capivara2ptn.project_config import save_elapsed
from test_init_resume import ORIGINAL, PRD, REJECTED, ScriptRunner


class InitResumeUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reopened_legacy_init_resumes_from_gate_rejection(self):
        with tempfile.TemporaryDirectory(prefix='capivara-init-ui-') as temporary:
            root = Path(temporary)
            cwd = root / 'project'
            data = cwd / '.harness/dados'
            data.mkdir(parents=True)
            history = 'Pergunta: Quem revisa?\nResposta: Todos\n\n'
            snapshots = {'prompt-original.txt': ORIGINAL, 'entrevista.txt': history,
                         'resposta.txt': 'CONCLUIDO', 'prompt-final.md': 'Estado em revisão.',
                         'g0-init.txt': REJECTED, 'erro-init.txt': REJECTED}
            for name, text in snapshots.items():
                (data / name).write_text(text)
            writer, auditor = ScriptRunner(PRD), ScriptRunner('G0 APROVADO')
            with patch('pathlib.Path.cwd', return_value=cwd), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture'))), \
                 patch.object(CapivaraApp, 'execution_runners', return_value=(writer, auditor)):
                save_elapsed(cwd, {'INIT': 12.0})
                app = CapivaraApp()
                async with app.run_test(size=(120, 45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(str(app.query_one('#start', Button).label), 'Retomar INIT')
                    self.assertEqual(app.query_one('#prompt-source', Select).value, 'file')
                    self.assertEqual(app.query_one('#prompt-path', Input).value, str(data / 'prompt-original.txt'))
                    self.assertEqual(writer.calls, [])
                    self.assertFalse(app._init_running)
                    self.assertFalse((data / 'init-estado.json').exists())
                    # A origem externa não precisa mais existir para retomar.
                    app.query_one('#prompt-path', Input).value = '/arquivo-externo-que-nao-existe.md'
                    app.query_one('#model', Select).value = 'fixture'
                    await pilot.pause()
                    app.start_pressed()
                    for _ in range(70):
                        self.assertNotIsInstance(app.screen, InterviewDialog)
                        if isinstance(app.screen, ResultDialog):
                            break
                        await pilot.pause(.02)
                    self.assertIsInstance(app.screen, ResultDialog)
                    self.assertTrue(app._init_saved)
                    self.assertEqual(len(writer.calls), 1)
                    self.assertEqual(writer.calls[0][1]['historico_entrevista'], history)
                    self.assertIn('reprovacao_g0', writer.calls[0][1])
                    self.assertEqual(len(auditor.calls), 1)
                    self.assertEqual((data / 'entrevista.txt').read_text(), history)
                    self.assertTrue((data / 'init-concluido.json').exists())
                    app.screen.close()
                    for _ in range(40):
                        if isinstance(app.screen, ContinueDialog):
                            break
                        await pilot.pause(.02)
                    self.assertIsInstance(app.screen, ContinueDialog)
                    app.screen.dismiss(False)
                    await pilot.pause()
                    self.assertFalse(app._init_running)
                    self.assertEqual(str(app.query_one('#start', Button).label), 'Refazer INIT')
                    self.assertGreaterEqual(app._clock.elapsed('INIT'), 12.0)

    async def test_close_and_reopen_restores_the_unanswered_interview_question(self):
        from test_init_resume import QUESTION
        with tempfile.TemporaryDirectory(prefix='capivara-init-close-') as temporary:
            root = Path(temporary)
            cwd = root / 'project'
            cwd.mkdir()
            writer = ScriptRunner(QUESTION)
            with patch('pathlib.Path.cwd', return_value=cwd), patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}), \
                 patch('capivara2ptn.app.discover_models', new=AsyncMock(return_value=ModelCatalog([('Fixture', 'fixture')], 'fixture'))), \
                 patch.object(CapivaraApp, 'execution_runners', return_value=(writer, writer)):
                app = CapivaraApp()
                async with app.run_test(size=(120, 45)) as pilot:
                    await pilot.pause()
                    app.query_one('#model', Select).value = 'fixture'
                    app.query_one('#manual-prompt').load_text(ORIGINAL)
                    await pilot.pause()
                    app.start_pressed()
                    for _ in range(60):
                        if isinstance(app.screen, InterviewDialog):
                            break
                        await pilot.pause(.02)
                    self.assertIsInstance(app.screen, InterviewDialog)
                    self.assertEqual(len(writer.calls), 1)
                    app.exit()
                state = json.loads((cwd / '.harness/dados/init-estado.json').read_text())
                self.assertEqual(state['question']['text'], 'Quem pode revisar?')
                self.assertEqual(state['questions_answered'], 0)
                reopened = CapivaraApp()
                async with reopened.run_test(size=(120, 45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(str(reopened.query_one('#start', Button).label), 'Retomar INIT')
                    self.assertEqual(len(writer.calls), 1)
                    reopened.query_one('#model', Select).value = 'fixture'
                    await pilot.pause()
                    reopened.start_pressed()
                    for _ in range(60):
                        if isinstance(reopened.screen, InterviewDialog):
                            break
                        await pilot.pause(.02)
                    self.assertIsInstance(reopened.screen, InterviewDialog)
                    self.assertEqual(reopened.screen.question, 'Quem pode revisar?')
                    self.assertEqual(len(writer.calls), 1)
                    reopened.exit()
