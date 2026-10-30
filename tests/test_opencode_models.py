import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from textual.widgets import OptionList
from harness.app import HarnessApp
from harness.opencode_models import discover, cache_path, parse_model_info, parse_model_lines
from test_wizard import test_catalog


def payload():
    return {'location':{'directory':'/private'},'data':[
        {'providerID':'tenant-a','id':'gpt-example','name':'Example','enabled':True,'type':'chat',
         'variants':[{'id':'low'},{'id':'high'}], 'headers':{'Authorization':'NEVER_SAVE_THIS'},'body':{'apiKey':'NEVER_SAVE_THIS'}},
        {'providerID':'tenant-b','id':'gpt-example','name':'Example B','variants':{'medium':{}}},
        {'providerID':'tenant-a','id':'disabled','enabled':False},
        {'providerID':'tenant-a','id':'embedding','type':'embedding'},
        {'providerID':None,'id':'invalid'}]}


class OpenCodeModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        env=patch.dict('os.environ',{'XDG_CACHE_HOME':str(self.root/'cache')})
        env.start();self.addCleanup(env.stop)

    def test_api_models_preserve_provider_and_actual_variants(self):
        models=parse_model_info(payload())
        self.assertEqual([m.id for m in models],['tenant-a/gpt-example','tenant-b/gpt-example'])
        self.assertEqual(models[0].efforts,('default','low','high'))
        self.assertEqual(models[1].efforts,('default','medium'))
        self.assertEqual(parse_model_lines('notice\ntenant-a/model\ntenant-b/model\ntenant-a/model\n')[0].id,'tenant-a/model')

    async def test_real_query_contract_and_safe_cache_no_credentials(self):
        query=AsyncMock(return_value=json.dumps(payload()))
        with patch('harness.opencode_models.query',query):
            models,source=await discover(self.root)
            await discover(self.root)
        self.assertEqual(query.await_count,1)
        self.assertEqual(query.await_args.args[0],['opencode','api','--standalone','model.list'])
        self.assertEqual(len(models),2)
        text=cache_path(self.root).read_text()
        for secret in ('NEVER_SAVE_THIS','headers','body','Authorization','apiKey','/private'):
            self.assertNotIn(secret,text)
        self.assertEqual(cache_path(self.root).stat().st_mode & 0o777,0o600)

    async def test_old_cli_fallback_and_offline_previous_catalog(self):
        query=AsyncMock(side_effect=[ValueError('API indisponível'),'provider/model-a\nprovider/model-b\n'])
        with patch('harness.opencode_models.query',query):
            models,_=await discover(self.root)
        self.assertEqual(len(models),2)
        self.assertEqual(query.await_args.args[0],['opencode','models'])
        path=cache_path(self.root);data=json.loads(path.read_text());data['saved_at']=0;path.write_text(json.dumps(data))
        with patch('harness.opencode_models.query',AsyncMock(side_effect=OSError('offline'))):
            models,source=await discover(self.root)
        self.assertEqual(len(models),2);self.assertIn('indisponível',source)

    async def test_invalid_cache_recovers_and_empty_catalog_never_becomes_default(self):
        path=cache_path(self.root);path.parent.mkdir(parents=True)
        path.write_text('{"saved_at":"invalid","models":[null]}')
        with patch('harness.opencode_models.query',AsyncMock(return_value=json.dumps(payload()))):
            models,_=await discover(self.root)
        self.assertEqual(len(models),2)
        path.unlink()
        with patch('harness.opencode_models.query',AsyncMock(side_effect=ValueError('sem modelos'))):
            with self.assertRaisesRegex(ValueError,'Não foi possível listar'):await discover(self.root)

    async def test_wizard_displays_discovered_models_then_selected_efforts(self):
        catalog=test_catalog();catalog.discovery={'opencode':True}
        app=HarnessApp(launch_dir=self.root,catalog=catalog)
        with patch('harness.opencode_models.query',AsyncMock(return_value=json.dumps(payload()))):
            async with app.run_test(size=(120,40)) as pilot:
                app.show_providers('writer')
                await pilot.pause()
                await pilot.press('down','down','enter')
                async with asyncio.timeout(10):
                    while app.screen.kind!='model':await pilot.pause()
                self.assertEqual(app.screen.query_one(OptionList).option_count,2)
                await pilot.press('enter')
                self.assertEqual(app.screen.kind,'effort')
                self.assertEqual(app.screen.query_one(OptionList).option_count,3)
                await pilot.press('down','enter')
                self.assertEqual(app.state.writer.model,'tenant-a/gpt-example')
                self.assertEqual(app.state.writer.effort,'low')
