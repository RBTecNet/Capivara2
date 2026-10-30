import json
from pathlib import Path
import sys
import unittest
from harness.build_engine import BuildEngine
from build_fixture import Builders
import test_build


class FunctionalChecksTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    async def test_direct_functional_command_needs_no_custom_wrapper_or_literal_quote(self):
        class ExistingRunner(Builders):
            async def generate(self,*args,**kwargs):
                request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1]);op=request['operation'];data=request['data']
                result=await super().generate(*args,**kwargs)
                if op=='verification' and data['scope']['kind']=='code':
                    name='dobro' if data['task']['id']=='P1-T001' else 'triplo'
                    function='dobrar' if name=='dobro' else 'triplicar';expected=6 if name=='dobro' else 9
                    return json.dumps({'summary':'Exercitar diretamente a função pedida.','dependencies':[],
                        'checks':[{'command':{'argv':[sys.executable,'-c',f'from {name} import {function}; assert {function}(3)=={expected}'],
                            'purpose':'Conferir o resultado funcional, sem wrapper adicional.','timeout':10,'env':[]},
                            'target_files':[name+'.py'],'requirement_quote':'O resultado funcional solicitado é preservado.',
                            'assertion_file':None,'expected_stdout':None}]})
                return result
        e=BuildEngine(self.state,ExistingRunner());await e.run()
        self.assertTrue(all(p['completed'] for p in e.progress.values()))
        self.assertEqual(e.contract_errors,0)
