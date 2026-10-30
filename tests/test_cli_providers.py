import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from textual.widgets import OptionList
from harness.agents import AgentError,CLIAgents
from harness.app import HarnessApp
from harness.artifacts import ArtifactStore,read_status
from harness.build_agents import build_command
from harness.build_commands import run_command
from harness.build_models import CommandSpec
from harness.cli_protocols import native_result,native_error
from harness.domain import AgentSelection
from harness.provider_models import discover,parse_agy_models,parse_claude_models
from harness.opencode_models import query
from harness.init_engine import InitEngine
from harness.plan_engine import PlanEngine
from init_fixture import ScriptedAgents
from plan_fixture import PlanAgents
from test_wizard import test_catalog
import test_plan


def claude_metadata():
    return json.dumps({'type':'control_response','response':{'subtype':'success','request_id':'capivara-models',
        'response':{'account':{'api_key':'NEVER_PERSIST_THIS'},'models':[
            {'value':'default','resolvedModel':'claude-current','displayName':'Default'},
            {'value':'current','resolvedModel':'claude-current','displayName':'Current',
             'supportsEffort':True,'supportedEffortLevels':['low','high']},
            {'value':'quick','displayName':'Quick','supportsEffort':False,'supportedEffortLevels':['max']}]}}})


class ProviderModelsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        env=patch.dict(os.environ,{'XDG_CACHE_HOME':str(self.root/'cache')});env.start();self.addCleanup(env.stop)

    def test_metadata_models_and_efforts_are_discovered_without_fixed_aliases(self):
        models=parse_claude_models(claude_metadata())
        self.assertEqual([(m.id,m.efforts) for m in models],[('claude-current',('default','low','high')),('quick',('default',))])
        models=parse_agy_models('Fetching available models...\ngemini-example-high\tExample (High)\ngemini-example-low\tExample (Low)\nlocal-custom\tCustom\n')
        self.assertEqual([(m.id,m.name,m.efforts) for m in models],[('gemini-example','Example',('default','low','high')),('local-custom','Custom',('default',))])

    async def test_claude_metadata_query_never_sends_user_prompt_and_cache_is_sanitized(self):
        query=AsyncMock(return_value=claude_metadata())
        with patch('harness.provider_models.query',query):
            models,_=await discover('claude',self.root)
            await discover('claude',self.root)
        self.assertEqual(query.await_count,1)
        command=query.await_args.args[0]
        self.assertIn('--tools',command);self.assertIn('--no-session-persistence',command)
        control=json.loads(query.await_args.kwargs['input_text'])
        self.assertEqual(control['request']['subtype'],'initialize')
        self.assertEqual(control['type'],'control_request')
        cache=next((self.root/'cache/capivara2').glob('claude-models*.json'))
        self.assertNotIn('NEVER_PERSIST_THIS',cache.read_text())
        self.assertEqual(cache.stat().st_mode & 0o777,0o600)

    async def test_agy_discovery_then_offline_cache_preserves_actual_efforts(self):
        query=AsyncMock(return_value='custom-medium\tCustom (Medium)\ncustom-high\tCustom (High)\n')
        with patch('harness.provider_models.query',query):models,_=await discover('agy',self.root)
        self.assertEqual(models[0].efforts,('default','medium','high'))
        cache=next((self.root/'cache/capivara2').glob('agy-models*.json'))
        value=json.loads(cache.read_text());value['saved_at']=0;cache.write_text(json.dumps(value))
        with patch('harness.provider_models.query',AsyncMock(side_effect=OSError('offline'))):models,source=await discover('agy',self.root)
        self.assertIn('indisponível',source);self.assertEqual(models[0].id,'custom')

    async def test_wizard_uses_real_claude_and_agy_catalogs_for_both_roles(self):
        for provider,steps,payload in [('claude',1,claude_metadata()),('agy',3,'example-low\tExample (Low)\nexample-high\tExample (High)\n')]:
            with self.subTest(provider=provider):
                catalog=test_catalog();catalog.discovery={provider:True}
                app=HarnessApp(launch_dir=self.root,catalog=catalog)
                with patch('harness.provider_models.query',AsyncMock(return_value=payload)):
                    async with app.run_test(size=(120,42)) as pilot:
                        app.show_providers('reviewer');await pilot.pause()
                        await pilot.press(*(['down']*steps),'enter')
                        async with asyncio.timeout(10):
                            while app.screen.kind!='model':await pilot.pause()
                        self.assertNotEqual(app.screen.query_one(OptionList).get_option_at_index(0).id,'default')
                        await pilot.press('enter')
                        self.assertEqual(app.screen.kind,'effort')
                        self.assertEqual(app.screen.query_one(OptionList).option_count,3)


class ProviderProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.store=ArtifactStore(self.root)

    def test_opencode_configuration_is_injected_into_child_even_in_ignored_git_directory(self):
        env={'CAPIVARA2_EXECUTION':'document','OPENCODE_CONFIG_CONTENT':'{"providers":{"custom":{"settings":{"apiKey":"NEVER_WRITE_THIS"}}}, // comment\n"agents":{"existing":{"mode":"primary"}},}'}
        command,mode=CLIAgents().command(AgentSelection('opencode','vendor/model','high'),{},self.root/'out',self.root/'schema',self.store,env)
        config=json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertIn('capivara-documentation',config['agents']);self.assertIn('existing',config['agents'])
        self.assertEqual(config['agents']['capivara-documentation']['permissions'][0]['effect'],'deny')
        self.assertEqual(config['policies'][-1],{'action':'tool.use','resource':'*','effect':'deny'})
        self.assertEqual(command[command.index('--model')+1],'vendor/model#high')
        self.assertNotIn('NEVER_WRITE_THIS',self.store.path('control/work/.opencode/opencode.json').read_text())
        self.assertEqual(config['providers']['custom']['settings']['apiKey'],'NEVER_WRITE_THIS')

    def test_undocumented_fallbacks_never_run_tools_or_reuse_sessions(self):
        for provider in ('claude','agy','opencode'):
            selection=AgentSelection(provider,'default','default')
            env={'CAPIVARA2_EXECUTION':'document'}
            command,_=CLIAgents().command(selection,{},self.root/'out',self.root/'schema',self.store,env)
            self.assertNotIn('--dangerously-skip-permissions',command)
            self.assertNotIn('--continue',command)
            self.assertNotIn('--session',command)
            if provider=='agy':
                settings=json.loads(command[command.index('--settings')+1])
                self.assertIn('command(*)',settings['permissions']['deny'])
                self.assertEqual(command[command.index('--input-format')+1],'stream-json')
            build,_=build_command(selection,{},self.root/'out',self.root/'schema',self.root)
            self.assertIn('--auto' if provider=='opencode' else '--dangerously-skip-permissions',build)
            if provider=='opencode':self.assertEqual(build[build.index('--agent')+1],'build')

    def test_agy_structured_output_wins_over_repeated_text(self):
        stream=json.dumps({'event':'result','result':{'status':'SUCCESS','response':'{"ok":true}\n{"ok":true}',
            'structured_output':{'ok':True}}})
        self.assertEqual(json.loads(native_result('agy',stream,allow_tools=False)),{'ok':True})
        with self.assertRaises(ValueError):native_result('agy',json.dumps({'event':'result','result':{'status':'WAITING','response':'aguardando'}}),allow_tools=True)

    def test_errors_and_tool_calls_are_never_treated_as_valid_documentation(self):
        error={'type':'error','error':{'type':'unknown','message':'Agent not found: "capivara-documentation"'}}
        stream=json.dumps({'type':'text','part':{'text':'{"ok":true}'}})+'\n'+json.dumps(error)
        self.assertIn('Agent not found',native_error('opencode',stream))
        with self.assertRaisesRegex(ValueError,'Agent not found'):native_result('opencode',stream,allow_tools=True)
        tool=json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash'}]}})
        result=json.dumps({'type':'result','subtype':'success','result':'{"ok":true}'})
        with self.assertRaisesRegex(ValueError,'ferramentas'):native_result('claude',tool+'\n'+result,allow_tools=False)
        self.assertEqual(json.loads(native_result('claude',result,allow_tools=True)),{'ok':True})
        self.assertIsNone(native_error('claude',result))
        step=json.dumps({'event':'step_update','step_update':{'step_type':'tool','tool_name':'write_file','state':'ACTIVE'}})
        finish=json.dumps({'event':'result','result':{'status':'SUCCESS','structured_output':{'ok':True}}})
        with self.assertRaisesRegex(ValueError,'ferramentas'):native_result('agy',step+'\n'+finish,allow_tools=False)
        self.assertEqual(json.loads(native_result('agy',step+'\n'+finish,allow_tools=True)),{'ok':True})

    async def test_cli_and_catalog_children_receive_matching_physical_and_environment_directories(self):
        script=self.root/'directory.py'
        script.write_text('import os,json\nprint(json.dumps({"cwd":os.getcwd(),"pwd":os.environ.get("PWD")}))\n')
        work=self.root/'workspace with spaces';work.mkdir()
        with patch.dict(os.environ,{'PWD':'/a/stale/directory'}):
            with patch.object(CLIAgents,'command',return_value=([sys.executable,str(script)],'custom')):
                output=await CLIAgents().generate(AgentSelection('opencode','default','default'),'writer','prompt',{},self.store,12,cwd=work,allow_tools=True)
            catalog_output=await query([sys.executable,str(script)],work)
            command_output=await run_command(CommandSpec(argv=[sys.executable,str(script)],purpose='Conferir a pasta do comando.',timeout=10),work,self.store,'log/directory')
        expected={'cwd':str(work),'pwd':str(work)}
        self.assertEqual(json.loads(output),expected)
        self.assertEqual(json.loads(catalog_output),expected)
        self.assertEqual(json.loads(command_output['stdout']),expected)

    async def test_real_subprocess_nonzero_and_zero_error_frames_surface_redacted_reason(self):
        for exit_code in (0,1):
            script=self.root/'fake.py'
            script.write_text('import json,sys\nprint(json.dumps({"type":"error","error":{"type":"auth","message":"Authorization: Bearer synthetic-secret"}}))\nsys.exit('+str(exit_code)+')\n')
            with patch.object(CLIAgents,'command',return_value=([sys.executable,str(script)],'opencode')):
                with self.assertRaises(AgentError) as caught:
                    await CLIAgents().generate(AgentSelection('opencode','default','default'),'writer','prompt',{},self.store,exit_code+1)
            self.assertNotIn('synthetic-secret',str(caught.exception))
            self.assertIn('auth',str(caught.exception))


class ProviderRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_plan.PlanTests.asyncSetUp

    async def test_plan_transport_errors_do_not_consume_contract_attempts(self):
        class Flaky(PlanAgents):
            failures=0
            async def generate(self,*args,**kwargs):
                if self.failures<3:
                    self.failures+=1;raise AgentError('CLI temporariamente indisponível')
                return await super().generate(*args,**kwargs)
        agents=Flaky();await PlanEngine(self.state,agents).run()
        self.assertEqual(agents.failures,3)
        self.assertEqual(read_status(self.root)['modules']['plan']['status'],'completed')

    async def test_init_retries_cli_failure_without_restarting_interview_context(self):
        class Flaky(ScriptedAgents):
            failures=0
            async def generate(self,*args,**kwargs):
                if self.failures<2:
                    self.failures+=1;raise AgentError('CLI reiniciando')
                return await super().generate(*args,**kwargs)
        agents=Flaky()
        async def answer(q,index,total):
            from harness.init_models import Answer
            return Answer(question_id=q.id,option_id=q.recommended_option_id,label='Local',text='')
        self.state.module='init';self.state.prompt='Aplicação de tarefas com persistência local.'
        await InitEngine(self.state,agents,interview=answer).run()
        self.assertEqual(agents.failures,2)
        self.assertEqual(read_status(self.root)['modules']['init']['status'],'completed')
