"""Aceite opcional do CHANGE com escritor real em aplicação sintética."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capivara2ptn.change_flow import ChangeRun
from capivara2ptn.change_source import Secrets
from capivara2ptn.change_state import ChangeStore
from capivara2ptn.project_config import ExecutionOptions
from capivara2ptn.providers import CliRunner, Selection


@unittest.skipUnless(os.environ.get('CAPIVARA_CHANGE_TESTS') == '1', 'Modelo real requer execução opt-in.')
class ChangeModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_bugfix_with_real_writer_and_direct_verification(self):
        model = os.environ.get('CAPIVARA_CHANGE_TEST_MODEL')
        self.assertTrue(model)
        with tempfile.TemporaryDirectory(prefix='capivara-change-real-') as temporary:
            root = Path(temporary)
            project = root / 'project'
            project.mkdir()
            original = 'def normalize(phone):\n    return phone[:8]\n'
            (project / 'app.py').write_text(original)
            (project / 'unrelated.txt').write_text('não alterar')
            store = ChangeStore(project)
            store.create('Corrigir somente app.py: normalize(phone) recebe sempre uma string. Deve remover somente espaços ASCII, preservar todos os demais caracteres e todos os dígitos, sem truncamento. Preserve nome, assinatura e a ausência de dependências externas. Não acrescente outros arquivos. Verifique a função por comando pontual, sem criar arquivos de teste.')
            selection = Selection(os.environ.get('CAPIVARA_CHANGE_TEST_PROVIDER', 'codex'), model, 'Low')
            runner = CliRunner(selection, project, lambda text: None, forbid_automated_tests=True)
            async def ask(question):
                self.fail('O pedido sintético já determina entrada, transformação e preservação: ' + question['text'])
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}):
                run = ChangeRun(store, runner, ExecutionOptions(forbid_automated_tests=True), Secrets(project),
                                lambda state, message: print(message[:500], flush=True), ask)
                result = await asyncio.wait_for(run.run(), timeout=300)
            self.assertEqual(store.state['tasks'], [])
            self.assertIsNone(store.state['plan'])
            self.assertIn(store.state['status'], {'done', 'done_with_limits'})
            if store.state['limitations']:
                print('Limitações registradas:', store.state['limitations'], flush=True)
            self.assertEqual(store.state['verification']['status'], 'sucesso')
            self.assertEqual((project / 'unrelated.txt').read_text(), 'não alterar')
            self.assertNotEqual((project / 'app.py').read_text(), original)
            self.assertTrue(result.exists())
