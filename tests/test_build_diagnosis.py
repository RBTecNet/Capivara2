import asyncio
import json
from pathlib import Path
import unittest

from harness.artifacts import read_status
from harness.build_engine import BuildEngine
from harness.cli_protocols import native_result
from build_fixture import Builders
import test_build


def request(args):
    value=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1]);return value['operation'],value['data']


def scope_diagnosis(data):
    return {'action':'expand_scope','root_cause':'O escritor respeita writes e a montagem necessária não está reservada.',
        'instruction':'Reservar app.py e montar a função real; não entregar apenas patch.',
        'requirement_quote':data['task']['content']['objective'],'required_files':['app.py'],
        'failure_evidence':data['recent_failures'][-1]['feedback']['issues'][0]['observed']}


class Wiring(Builders):
    async def generate(self,*args,**kwargs):
        op,data=request(args)
        if op=='diagnose':
            self.calls.append((args[1],op,data));return json.dumps(scope_diagnosis(data))
        result=await super().generate(*args,**kwargs)
        if data.get('task',{}).get('id')!='P1-T001':return result
        root=Path(kwargs['cwd'])
        if op=='write':
            if 'app.py' in data['scope']['writes']:
                (root/'app.py').write_text('from dobro import dobrar\ndef run(): return dobrar(3)\n')
            else:
                target=root/'dobro.py';target.write_text(target.read_text()+f'# tentativa {len(self.calls)}\n')
        if op=='fidelity' and not (root/'app.py').exists():
            return json.dumps({'approved':False,'summary':'Integração real ainda ausente.','issues':[{
                'file':'dobro.py','observed':'Montagem em app.py está fora de writes; patches não integram a aplicação.',
                'required_change':'Integrar a função em app.py preservando as entregas anteriores.',
                'acceptance':'O ponto de entrada chama a função real.',
                'requirement_quote':data['task']['content']['objective'],'evidence_quote':'def dobrar(n):','missing':False}]})
        return result


class BrokenProbe(Builders):
    async def generate(self,*args,**kwargs):
        op,data=request(args)
        if op=='diagnose' and data['task']['id']=='P1-T001':
            self.calls.append((args[1],op,data))
            return json.dumps({'action':'repair_verification','root_cause':'O script usa fixture_name inexistente.',
                'instruction':'Corrigir o nome da variável do check e preservar a assertiva 6.',
                'requirement_quote':data['task']['content']['objective'],'required_files':[],
                'failure_evidence':'NameError'})
        if op=='review_verification_repair':
            self.calls.append((args[1],op,data))
            return json.dumps({'approved':True,'preserves_requirements':True,'tests_only_task':True,
                'reason':'Corrige a fixture sem alterar o resultado que a função deve produzir.'})
        result=await super().generate(*args,**kwargs)
        if op=='verification' and data['task']['id']=='P1-T001' and not data.get('verification_repair'):
            path=Path(kwargs['cwd'])/'.harness/checks/check.py'
            path.write_text("import runpy\nmodule=runpy.run_path('dobro.py')\nassert fixture_name['dobrar'](3)==6\n")
        return result


class DiagnosisTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    def engine(self,agents):
        engine=BuildEngine(self.state,agents);agents.delay=.001
        async def recover(stage,message,attempt=1,task_id=None):
            engine.announce(stage,message,attempt=attempt,task_id=task_id);await asyncio.sleep(0)
        engine.recover=recover
        return engine

    async def test_compliant_writer_stops_repeating_and_gets_required_reservation_automatically(self):
        agents=Wiring();engine=self.engine(agents);await engine.run()
        diagnoses=[d for _,op,d in agents.calls if op=='diagnose']
        self.assertEqual(len(diagnoses),1)
        self.assertEqual(len(diagnoses[0]['recent_failures']),2)
        self.assertEqual(engine.progress['P1-T001']['attempts'],3)
        self.assertTrue(engine.progress['P1-T001']['force_serial'])
        self.assertIn('app.py',engine.scopes['P1-T001'].writes)
        self.assertTrue((self.root/'app.py').is_file())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_writer_can_request_scope_without_first_violating_it(self):
        class Request(Wiring):
            async def generate(self,*args,**kwargs):
                op,data=request(args)
                result=await super().generate(*args,**kwargs)
                if op=='write' and data['task']['id']=='P1-T001' and 'app.py' not in data['scope']['writes']:
                    assert not (Path(kwargs['cwd'])/'app.py').exists()
                    return json.dumps({'summary':'Preciso da montagem fora da reserva.','files':[],
                        'scope_requests':[{'file':'app.py','reason':'Montagem da unidade na entrada.',
                            'requirement_quote':data['task']['content']['objective']}]})
                return result
        agents=Request();engine=self.engine(agents);await engine.run()
        self.assertFalse(any(op=='diagnose' for _,op,_ in agents.calls))
        self.assertEqual(engine.progress['P1-T001']['attempts'],2)
        self.assertTrue((self.root/'app.py').is_file())

    async def test_diagnosis_survives_cancellation_and_does_not_repeat_completed_tasks(self):
        class Interrupted(Wiring):
            async def generate(self,*args,**kwargs):
                op,data=request(args)
                if op=='diagnose':raise asyncio.CancelledError()
                return await super().generate(*args,**kwargs)
        with self.assertRaises(asyncio.CancelledError):await self.engine(Interrupted()).run()
        state=json.loads((self.root/'.harness/build/state.json').read_text())
        self.assertTrue(state['tasks']['P1-T001']['diagnosis_pending'])
        agents=Wiring();await self.engine(agents).run()
        self.assertFalse(any(op=='write' and d['task']['phase_number']==0 for _,op,d in agents.calls))
        self.assertEqual(len([d for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001']),1)
        self.assertTrue((self.root/'app.py').is_file())

    async def test_broken_check_is_repaired_by_verifier_without_rewriting_working_application(self):
        agents=BrokenProbe();engine=self.engine(agents);await engine.run()
        self.assertEqual(len([d for _,op,d in agents.calls if op=='write' and d['task']['id']=='P1-T001']),1)
        self.assertEqual(len([d for _,op,d in agents.calls if op=='review_verification_repair']),1)
        results=[json.loads(p.read_text())['exit_code'] for p in (self.root/'.harness/build/log').glob('*P1-T001-g3*.result.json')]
        self.assertIn(1,results);self.assertIn(0,results)
        self.assertTrue((self.root/'.harness/build/tasks/P1-T001/verification-repairs/0001/review.json').exists())
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')

    async def test_reviewed_probe_repair_survives_interruption_before_state_commit(self):
        agents=BrokenProbe();engine=self.engine(agents)
        original=engine.store.json
        def crash(relative,data):
            if relative=='tasks/P1-T001/verification.json' and engine.progress.get('P1-T001',{}).get('verification_repair_ready'):
                raise RuntimeError('Interrupção após aprovação do reparo')
            return original(relative,data)
        engine.store.json=crash
        with self.assertRaisesRegex(RuntimeError,'aprovação do reparo'):await engine.run()
        agents=Builders();resumed=self.engine(agents);await resumed.run()
        self.assertFalse(any(op in ('write','fidelity','verification','review_verification_repair') and d.get('task',{}).get('id')=='P1-T001' for _,op,d in agents.calls))
        self.assertEqual(resumed.progress['P1-T001']['gates'],['completed']*4)
        self.assertNotIn('verification_repair_ready',resumed.progress['P1-T001'])

    async def test_auditor_can_request_mounting_path_instead_of_repeating_rejection(self):
        class AuditorRequest(Wiring):
            async def generate(self,*args,**kwargs):
                op,data=request(args);result=await super().generate(*args,**kwargs)
                if op=='fidelity' and data['task']['id']=='P1-T001' and not (Path(kwargs['cwd'])/'app.py').exists():
                    value=json.loads(result)
                    value['scope_requests']=[{'file':'app.py','reason':'Montagem real da função.',
                        'requirement_quote':data['task']['content']['objective']}]
                    return json.dumps(value)
                return result
        agents=AuditorRequest();engine=self.engine(agents);await engine.run()
        self.assertFalse(any(op=='diagnose' for _,op,_ in agents.calls))
        self.assertEqual(engine.progress['P1-T001']['attempts'],2)
        self.assertTrue((self.root/'app.py').exists())

    async def test_repair_cannot_drop_assertion_to_accept_wrong_code(self):
        class BadProposal(Builders):
            diagnoses=0;reviews=0
            async def generate(self,*args,**kwargs):
                op,data=request(args)
                if op=='diagnose' and data['task']['id']=='P1-T001':
                    self.calls.append((args[1],op,data));self.diagnoses+=1
                    return json.dumps({'action':'repair_verification' if self.diagnoses==1 else 'repair_code',
                        'root_cause':'Analisar a assertiva contra o pedido original.','instruction':'Preservar o resultado 6 solicitado.',
                        'requirement_quote':data['task']['content']['objective'],'required_files':[],
                        'failure_evidence':'AssertionError'})
                if op=='review_verification_repair':
                    self.calls.append((args[1],op,data));self.reviews+=1
                    approved=self.reviews>1
                    return json.dumps({'approved':approved,'preserves_requirements':approved,'tests_only_task':True,
                        'reason':'A saída exigida é 6; não adapte a assertiva para aceitar 0.' if not approved else 'A assertiva exige 6 e mantém o pedido.'})
                result=await super().generate(*args,**kwargs)
                if op=='verification' and data['task']['id']=='P1-T001' and data.get('verification_repair') and self.reviews==0:
                    path=Path(kwargs['cwd'])/'.harness/checks/check.py'
                    path.write_text("import runpy\nmodule=runpy.run_path('dobro.py')\nassert module['dobrar'](3)==0\n")
                return result
        agents=BadProposal();agents.g3_bad=True;engine=self.engine(agents);await engine.run()
        self.assertEqual(agents.reviews,2)
        self.assertIn('n * 2',(self.root/'dobro.py').read_text())
        p=engine.progress['P1-T001']
        script=Path(p['workspace'])/'.harness/checks/check.py'
        self.assertIn('==6',script.read_text())
        first=json.loads((self.root/'.harness/build/tasks/P1-T001/verification-repairs/0001/review.json').read_text())
        self.assertFalse(first['approved'])

    def test_agy_finish_is_structured_protocol_but_other_tools_are_still_blocked(self):
        value={'title':'documento','phases':[1,2]}
        finish={'event':'step_update','step_update':{'step_type':'tool','tool_name':'finish','state':'ACTIVE',
            'tool_info':{'name':'finish','parameters':value}}}
        result={'event':'result','result':{'status':'SUCCESS','structured_output':value,'response':'texto duplicado'}}
        payload=json.dumps(finish)+'\n'+json.dumps(result)
        self.assertEqual(json.loads(native_result('agy',payload,allow_tools=False)),value)
        unsafe={'event':'step_update','step_update':{'step_type':'tool','tool_name':'write_file','tool_info':{'name':'write_file','parameters':value}}}
        with self.assertRaisesRegex(ValueError,'ferramentas'):
            native_result('agy',json.dumps(unsafe)+'\n'+payload,allow_tools=False)
        finish['step_update']['tool_info']['parameters']={'outro':'conteúdo'}
        with self.assertRaisesRegex(ValueError,'ferramentas'):
            native_result('agy',json.dumps(finish)+'\n'+json.dumps(result),allow_tools=False)
