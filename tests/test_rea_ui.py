"""Integração isolada REA na TUI, preservando os seletores e vistas existentes."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from textual.widgets import Button,Input,RadioButton,Select,TextArea
from capivara2ptn.app import CapivaraApp
from capivara2ptn.models import ModelCatalog
from capivara2ptn.prompt_source import PromptSource
from capivara2ptn.rea_flow import ReaRun
from capivara2ptn.rea_ui import ReaControls,ReaPanel,ReaTargetDialog,ReaConsentDialog,ReaDocumentDialog,ReaPrivilegeDialog
from test_rea import Client,Setup,Writer,CALL,FINISH


class UiSetup(Setup):
    def __init__(self,log,safe,approve,privilege=None):super().__init__();self.approve=approve;self.privilege=privilege


class UiWriter(Writer):
    secrets=[]
    def __init__(self,*args,**kwargs):super().__init__([CALL,FINISH])


class ReaUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='capivara-rea-ui-');self.root=Path(self.temp.name)
        self.project=self.root/'project';self.project.mkdir();self.target=self.root/'alvo.bin';self.target.write_bytes(b'fixture')
        self.cwd=patch('pathlib.Path.cwd',return_value=self.project);self.cwd.start()
        self.config=patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config')});self.config.start()
        self.models=patch('capivara2ptn.app.discover_models',new=AsyncMock(return_value=ModelCatalog([('Fixture','fixture')],'fixture')));self.models.start()
    def tearDown(self):self.models.stop();self.config.stop();self.cwd.stop();self.temp.cleanup()
    async def test_module_is_idle_on_open_and_switching_preserves_existing_views(self):
        app=CapivaraApp()
        async with app.run_test(size=(120,45)) as pilot:
            await pilot.pause();self.assertFalse(app.rea.running)
            self.assertFalse(app.query_one(ReaControls).display);self.assertTrue(app.query_one(PromptSource).display)
            app.query_one('#module-rea',RadioButton).value=True;await pilot.pause()
            self.assertTrue(app.query_one(ReaControls).display);self.assertTrue(app.query_one(ReaPanel).display)
            self.assertFalse(app.query_one(PromptSource).display);self.assertFalse(app.query_one('#plan-progress-area').display)
            self.assertEqual(app._clock.totals,{})
            for module in ('reverse','change','plan','init'):
                app.query_one('#module-'+module,RadioButton).value=True;await pilot.pause()
                self.assertFalse(app.query_one(ReaPanel).display)
            self.assertTrue(app.query_one(PromptSource).display)
            self.assertFalse((self.project/'.harness/rea').exists())
    async def test_file_and_directory_picker_and_consent(self):
        app=CapivaraApp()
        async with app.run_test(size=(120,45)) as pilot:
            app.push_screen(ReaTargetDialog(self.root));await pilot.pause()
            app.screen.query_one(Input).value=str(self.target)
            self.assertEqual(app.screen.path(),self.target)
            app.screen.query_one(Input).value=str(self.root)
            self.assertEqual(app.screen.path(),self.root)
            app.screen.cancel();await pilot.pause()
            app.push_screen(ReaConsentDialog({'title':'Preparar REA','items':['REA','Node.js se necessário'],'commands':['npm ci'],'note':'Sem modificar CLIs.'}))
            await pilot.pause();self.assertIsInstance(app.screen,ReaConsentDialog)
            app.screen.cancel();await pilot.pause()
    async def test_real_controller_calls_own_mcp_flow_and_restores_documents(self):
        with patch('capivara2ptn.rea_ui.ReaSetup',UiSetup),patch('capivara2ptn.rea_ui.CliRunner',UiWriter),\
             patch('capivara2ptn.rea_ui.ReaRun',side_effect=lambda *a,**k:ReaRun(*a,**k,client_factory=Client)):
            app=CapivaraApp()
            async with app.run_test(size=(120,45)) as pilot:
                await pilot.pause();app.query_one('#module-rea',RadioButton).value=True
                app.query_one('#model',Select).value='fixture';app.query_one('#rea-target',Input).value=str(self.target)
                await pilot.pause();app.start_pressed()
                for _ in range(100):
                    if not app.rea.running:break
                    await pilot.pause(.02)
                self.assertEqual(app.rea.store.state['status'],'done');self.assertFalse(app.rea.running)
                self.assertFalse(app.query_one('#configuration').disabled)
                self.assertEqual(app._clock.totals,{})
                app.rea.document();await pilot.pause();self.assertIsInstance(app.screen,ReaDocumentDialog)
                app.screen.close();await pilot.pause()
            reopened=CapivaraApp()
            async with reopened.run_test(size=(120,45)) as pilot:
                await pilot.pause();self.assertFalse(reopened.rea.running)
                self.assertEqual(reopened.query_one('#rea-target',Input).value,str(self.target))
                self.assertEqual(reopened.rea.store.state['status'],'done')
    async def test_administrative_password_is_not_kept_after_dismissal(self):
        app=CapivaraApp()
        async with app.run_test(size=(120,45)) as pilot:
            app.push_screen(ReaPrivilegeDialog());await pilot.pause()
            dialog=app.screen;field=dialog.query_one(Input)
            self.assertTrue(field.password);field.value='sudo-canary';dialog.cancel()
            self.assertEqual(field.value,'')
