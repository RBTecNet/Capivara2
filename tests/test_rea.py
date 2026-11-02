"""Contratos da integração REA, sem motores copiados nem serviços externos."""

import asyncio
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from capivara2ptn.providers import Selection
from capivara2ptn.rea_common import ReaError,Safe,read_json,write_json,run
from capivara2ptn.rea_flow import ReaRun
from capivara2ptn.rea_mcp import ReaMcp,normalized
from capivara2ptn.rea_setup import ReaSetup,node_supported,unpack
from capivara2ptn.rea_state import ReaStore,fingerprint
from capivara2ptn.workspace import WorkspaceError


async def yes(plan):return True


class Setup:
    command=['rea-fixture']
    privilege=None
    def __init__(self):self.approve=yes;self.installs=[];self.prepared=0
    async def ensure(self):self.prepared+=1;return self
    def environment(self):return dict(os.environ)
    async def cli_json(self,*a,**k):return {'healthy':False,'checks':[{'name':'optional-hopper','ok':False}],'providerInspections':[]}
    async def dependency(self,name):self.installs.append(name)


class Client:
    instances=[]
    tools={name:{'name':name,'inputSchema':{'type':'object'},'description':name,'annotations':{}} for name in (
        'binary_session','get_evidence_bundle','export_evidence_bundle','import_evidence_bundle','inspect_fixture','close_binary','capture_process_scenario','open_binary')}
    identity={'name':'rea','version':'6.5.0'}
    def __init__(self,*args):self.calls=[];self.closed=False;self.records=[];Client.instances.append(self)
    async def start(self):return self.tools
    async def call(self,name,args):
        self.calls.append((name,args))
        if name=='binary_session':return {'structuredContent':{'result':{'open':False,'tool_availability':{'groups':[
            {'names':['inspect_fixture','open_binary'],'effects':['mutatesSession'],'available':True,'reason':'available'},
            {'names':['capture_process_scenario'],'effects':['executesForeignCode','writesFilesystem'],'available':True,'reason':'available'}]}}}}
        if name=='export_evidence_bundle':
            write_json(Path(args['path']),{'records':self.records});return {'structuredContent':{'result':{'path':args['path']}}}
        if name=='import_evidence_bundle':self.records=read_json(Path(args['path']))['records'];return {'structuredContent':{'result':{'imported':len(self.records)}}}
        if name=='inspect_fixture':
            result={'structuredContent':{'evidence_id':'ev_fixture','normalized_result':{'fact':'calcular(v) retorna v * .9','secret':'credential-canary'}}}
            self.records.append(result);return result
        if name=='capture_process_scenario':raise AssertionError('Operação recusada não deve atingir o servidor.')
        return {'structuredContent':{'result':{}}}
    async def close(self):self.closed=True


class Writer:
    selection=Selection('codex','fixture','High')
    def __init__(self,actions):self.actions=iter(actions);self.calls=[]
    async def invoke(self,prompt,data,**kwargs):
        self.calls.append((data,kwargs))
        value=next(self.actions)
        if isinstance(value,BaseException):raise value
        return json.dumps(value)


CALL={'action':'call','tool':'inspect_fixture','arguments':{}}
FINISH={'action':'finish','documentation':'# Análise REA\n\nCálculo confirmado por ev_fixture.','limitations':[]}


class ReaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='capivara-rea-test-');self.root=Path(self.temp.name)
        self.project=self.root/'project';self.project.mkdir();self.target=self.root/'artifact.js';self.target.write_text('export const answer=42;')
        self.config=patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config')});self.config.start()
        self.safe=Safe(self.project,self.target,['credential-canary']);Client.instances=[]
    def tearDown(self):self.config.stop();self.temp.cleanup()
    async def flow(self,writer,store=None,**kwargs):
        store=store or ReaStore(self.project);setup=kwargs.pop('setup',Setup())
        flow=ReaRun(store,writer,setup,self.safe,lambda *a:None,kwargs.pop('approve',yes),ask=kwargs.pop('ask',None),client_factory=kwargs.pop('client_factory',Client))
        result=await flow.run(self.target,'Compreender o cálculo.');return result,store,setup
    async def test_direct_mcp_calls_checkpoint_and_documentation(self):
        path,store,setup=await self.flow(Writer([CALL,FINISH]))
        self.assertTrue(path.is_file());self.assertEqual(store.state['status'],'done')
        self.assertEqual(len(store.state['calls']),1);self.assertTrue(store.path(store.state['bundle']['path']).is_file())
        self.assertTrue(Client.instances[0].closed)
        self.assertFalse((self.project/'.harness/dados').exists())
        self.assertEqual(self.target.read_text(),'export const answer=42;')
        self.assertGreater(store.elapsed(),0);self.assertIsNone(store.started)
    async def test_confirmed_steps_are_imported_without_reexecution(self):
        store=ReaStore(self.project)
        with self.assertRaises(asyncio.CancelledError):await self.flow(Writer([CALL,asyncio.CancelledError()]),store)
        self.assertEqual(store.state['status'],'interrupted');self.assertEqual(len(store.state['calls']),1)
        reopened=ReaStore(self.project);reopened.load()
        _,reopened,_=await self.flow(Writer([FINISH]),reopened)
        self.assertEqual(reopened.state['status'],'done')
        self.assertIn('import_evidence_bundle',[n for n,a in Client.instances[-1].calls])
        self.assertNotIn('inspect_fixture',[n for n,a in Client.instances[-1].calls])
    async def test_canonical_evidence_is_unchanged_but_model_context_is_sanitized(self):
        writer=Writer([CALL,FINISH]);_,store,_=await self.flow(writer)
        raw=store.result(store.state['calls'][0])
        self.assertIn('credential-canary',json.dumps(raw))
        self.assertNotIn('credential-canary',json.dumps(writer.calls[-1][0]))
        self.assertEqual(store.path(store.state['calls'][0]['artifact']).stat().st_mode&0o777,0o600)
    async def test_missing_optional_engines_does_not_block_static_analysis(self):
        _,store,_=await self.flow(Writer([CALL,FINISH]));self.assertEqual(store.state['status'],'done')
    async def test_unknown_tool_and_invalid_json_get_feedback_without_fake_success(self):
        writer=Writer([{'action':'call','tool':'invented_tool','arguments':{}},CALL,FINISH])
        _,store,_=await self.flow(writer)
        self.assertIn('catálogo',writer.calls[1][0]['feedback'])
        self.assertEqual(len(store.state['calls']),1)
    async def test_risky_operation_is_not_sent_without_authorization(self):
        async def deny(plan):return False
        writer=Writer([{'action':'call','tool':'capture_process_scenario','arguments':{}},CALL,FINISH])
        _,store,_=await self.flow(writer,approve=deny)
        self.assertIn('não autorizada',writer.calls[1][0]['feedback'])
        self.assertNotIn('capture_process_scenario',[n for n,a in Client.instances[0].calls])
    async def test_dependency_not_installed_without_real_failure(self):
        setup=Setup();writer=Writer([{'action':'install','dependency':'ghidra','reason':'opcional'},CALL,FINISH])
        await self.flow(writer,setup=setup);self.assertEqual(setup.installs,[])
        self.assertIn('diagnóstico real',writer.calls[1][0]['feedback'])
    async def test_edited_evidence_blocks_resume(self):
        store=ReaStore(self.project)
        with self.assertRaises(asyncio.CancelledError):await self.flow(Writer([CALL,asyncio.CancelledError()]),store)
        store.path(store.state['calls'][0]['artifact']).write_text('{}')
        with self.assertRaises(ReaError):await self.flow(Writer([FINISH]),store)
        self.assertNotEqual(store.state['status'],'done')
    async def test_finished_request_does_not_start_server_again(self):
        _,store,_=await self.flow(Writer([CALL,FINISH]));before=len(Client.instances)
        await self.flow(Writer([]),store);self.assertEqual(len(Client.instances),before)
    async def test_changed_target_archives_old_results_before_new_analysis(self):
        _,store,_=await self.flow(Writer([CALL,FINISH]));self.target.write_text('changed bytes')
        await self.flow(Writer([CALL,FINISH]),store)
        self.assertEqual(len(list(store.path('historico').glob('*/documentacao.md'))),1)
    async def test_output_and_other_target_paths_are_refused(self):
        (self.root/'elsewhere').write_text('não analisar')
        writer=Writer([{'action':'call','tool':'open_binary','arguments':{'path':str(self.root/'elsewhere')}},CALL,FINISH])
        await self.flow(writer)
        self.assertIn('outro alvo',writer.calls[1][0]['feedback'])
        self.assertNotIn('open_binary',[name for name,args in Client.instances[0].calls])
    async def test_empty_analysis_is_never_claimed_verified(self):
        _,store,_=await self.flow(Writer([FINISH]));self.assertEqual(store.state['status'],'done_with_limits')
        self.assertTrue(store.state['limitations'])
    def test_node_contract_exact_version_ranges(self):
        for version in ('22.19.0','v22.22.1','24.11.0','24.22.1','26.0.0'):self.assertTrue(node_supported(version))
        for version in ('20.19.1','22.18.0','23.0.0','24.10.0','25.2.0','v24.11.0-rc.1'):self.assertFalse(node_supported(version))
    def test_extraction_refuses_traversal_without_partial_publication(self):
        archive=self.root/'unsafe.tar.gz';out=self.root/'installed'
        with tarfile.open(archive,'w:gz') as stream:
            for name in ('valid/first','../escape'):
                info=tarfile.TarInfo(name);info.size=3;stream.addfile(info,io.BytesIO(b'abc'))
        with self.assertRaises(ReaError):unpack(archive,out)
        self.assertFalse((self.root/'escape').exists());self.assertFalse((out/'valid').exists())
    def test_control_symlinks_are_refused(self):
        (self.project/'.harness').symlink_to(self.root/'outside',target_is_directory=True)
        with self.assertRaises((ReaError,WorkspaceError)):ReaStore(self.project).write('estado.json',{})
    async def test_preparation_reuses_verified_existing_installation(self):
        setup=ReaSetup(lambda t:None,self.safe.text,AsyncMock(side_effect=AssertionError('Não reinstalar')),base=self.root/'shared')
        setup.command=['rea-fixture']
        setup.config['command']=setup.command;setup.save()
        with patch.object(setup,'cli_json',new=AsyncMock(return_value={'healthy':True,'server':{'name':'rea'}})),patch.object(setup,'compatible',new=AsyncMock(return_value=True)):
            await setup.ensure()
        self.assertTrue(setup.config['ready'])
    async def test_preparation_denied_does_not_download_or_run_npm(self):
        setup=ReaSetup(lambda t:None,self.safe.text,AsyncMock(return_value=False),base=self.root/'shared')
        with patch('capivara2ptn.rea_setup.shutil.which',return_value=None),patch('capivara2ptn.rea_setup.fetch_json',side_effect=AssertionError('Não baixar')):
            with self.assertRaises(ReaError):await setup.ensure()
        self.assertFalse(setup.config.get('ready',False))
    async def test_json_diagnostics_are_not_mixed_with_stderr(self):
        logs=[]
        code,out=await run([sys.executable,'-c','import sys; print("diagnóstico",file=sys.stderr); print("{\\"healthy\\":true}")'],self.root,dict(os.environ),logs.append,self.safe.text,separate_stderr=True)
        self.assertEqual(code,0);self.assertTrue(json.loads(out)['healthy']);self.assertIn('diagnóstico',logs)
    def test_fingerprint_ignores_own_control_files(self):
        (self.project/'a.js').write_text('value')
        before=fingerprint(self.project,self.project);ReaStore(self.project).write('test.json',{})
        self.assertEqual(before,fingerprint(self.project,self.project))


class ReaRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp=ReaTests.setUp
    tearDown=ReaTests.tearDown
    flow=ReaTests.flow
    async def test_published_bundle_recovers_after_cancelled_receipt(self):
        class Published(Client):
            async def call(inner,name,args):
                result=await super(Published,inner).call(name,args)
                if name=='export_evidence_bundle':raise asyncio.CancelledError
                return result
        store=ReaStore(self.project)
        with self.assertRaises(asyncio.CancelledError):await self.flow(Writer([CALL]),store,client_factory=Published)
        self.assertEqual(store.state['calls'],[])
        self.assertTrue(store.state['pending']['result_artifact'])
        _,store,_=await self.flow(Writer([FINISH]),store)
        self.assertEqual(store.state['status'],'done')
        self.assertEqual(len(store.state['calls']),1)
        self.assertNotIn('inspect_fixture',[name for name,args in Client.instances[-1].calls])
    async def test_overlapping_directory_is_staged_without_own_outputs(self):
        from capivara2ptn.rea_input import prepare_input
        (self.project/'index.js').write_text('export const answer=42;')
        store=ReaStore(self.project)
        with store.execution():
            signature=fingerprint(self.project,self.project);store.begin(self.project,'Analisar diretório',signature)
            target=await prepare_input(store,lambda text:None)
        self.assertTrue((target/'index.js').exists())
        self.assertFalse((target/'.harness').exists())
        self.assertEqual(fingerprint(target,self.project),signature)
        self.assertEqual(store.state['target'],str(self.project))
    async def test_changed_target_during_analysis_does_not_publish_stale_document(self):
        outer=self
        class Changed(Client):
            async def call(inner,name,args):
                result=await super(Changed,inner).call(name,args)
                if name=='inspect_fixture':outer.target.write_text('another version')
                return result
        store=ReaStore(self.project)
        with self.assertRaises(ReaError):await self.flow(Writer([CALL,FINISH]),store,client_factory=Changed)
        self.assertEqual(store.state['status'],'failed')
        self.assertFalse(store.path('documentacao.md').exists())
    async def test_pending_question_resumes_without_reasking_model(self):
        question={'action':'question','text':'Qual arquitetura do binário sem cabeçalho?','choices':['x86','arm64']}
        async def pause(value):return None
        _,store,_=await self.flow(Writer([question]),ask=pause)
        self.assertEqual(store.state['status'],'interrupted');self.assertTrue(store.state['pending_question'])
        async def answer(value):return 'x86'
        writer=Writer([CALL,FINISH]);await self.flow(writer,store,ask=answer)
        self.assertEqual(writer.calls[0][0]['answers'][0]['answer'],'x86')
    async def test_sudo_password_goes_only_to_authentication_process(self):
        setup=ReaSetup(lambda text:None,self.safe.text,yes,AsyncMock(return_value='sudo-canary'),base=self.root/'shared')
        calls=[]
        async def execute(argv,**kwargs):
            calls.append((argv,kwargs))
            return (1,'') if argv==['sudo','-n','true'] else (0,'')
        with patch('capivara2ptn.rea_setup.os.geteuid',return_value=1000),patch('capivara2ptn.rea_setup.shutil.which',side_effect=lambda name,*a,**k:'/usr/bin/'+name if name in {'apt-get','sudo'} else None),patch.object(setup,'execute',side_effect=execute):
            await setup.system_packages(['procps'])
        auth=[(args,kwargs) for args,kwargs in calls if '-v' in args]
        self.assertEqual(auth[0][1]['input_text'],'sudo-canary\n')
        self.assertTrue(all('input_text' not in kwargs for args,kwargs in calls if 'apt-get' in args))

    async def test_official_hopper_bridge_cannot_mutate_model_clients(self):
        import shutil
        from capivara2ptn.rea_vendor_setup import HOPPER_SETUP
        node=shutil.which('node')
        if not node:self.skipTest('Node necessário somente a este teste local da ponte.')
        root=self.root/'vendor';(root/'dist/application').mkdir(parents=True)
        (root/'package.json').write_text('{"type":"module"}')
        (root/'dist/doctorRuntime.js').write_text('export function createSystemDoctorHost(){return {}}')
        (root/'dist/application/SetupHost.js').write_text('export function systemSetupHost(){return {configureClient(){throw new Error("client mutation escaped guard")}}}')
        (root/'dist/application/Setup.js').write_text('export async function runSetup(options,host){if(options.approved)host.configureClient();return {status:"planned",plannedActions:[{kind:"install_hopper"}]}}')
        logs=[]
        code,out=await run([node,'--input-type=module','--eval',HOPPER_SETUP,str(root),'apply'],root,dict(os.environ),logs.append,self.safe.text,separate_stderr=True)
        self.assertNotEqual(code,0)
        self.assertTrue(any('must not modify model clients' in line for line in logs))
    def test_nonfinite_model_values_are_not_sent_as_invalid_json(self):
        from capivara2ptn.rea_flow import json_response
        with self.assertRaises(ReaError):json_response('{"action":"call","arguments":{"limit":NaN}}')

    async def test_schemas_remain_available_together_instead_of_alternating(self):
        writer=Writer([{'action':'schema','tool':'inspect_fixture'},{'action':'schema','tool':'get_evidence_bundle'},CALL,FINISH])
        _,store,_=await self.flow(writer)
        known={item['name'] for item in writer.calls[2][0]['known_schemas']}
        self.assertEqual(known,{'inspect_fixture','get_evidence_bundle'})
        self.assertEqual(store.state['schemas_seen'],['inspect_fixture','get_evidence_bundle'])
