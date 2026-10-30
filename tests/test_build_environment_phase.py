"""Preparação: agente propõe JSON; controlador executa e mostra a fase real."""
import asyncio
import json
import sys
import unittest

from textual.app import App
from textual.widgets import Static,ProgressBar
from harness.build_engine import BuildEngine
from harness.build_artifacts import DependenciesBlocked
from harness.build_progress import BuildProgress
from harness.cli_protocols import agy_command
from harness.domain import AgentSelection
from build_fixture import Builders
import test_build


def dependency(root):
    marker=root/'dependency.ready';checks=root/'dependency.checks'
    code=f'from pathlib import Path; p=Path({str(checks)!r}); p.write_text(str(int(p.read_text())+1) if p.exists() else "1"); raise SystemExit(0 if Path({str(marker)!r}).exists() else 1)'
    return {'name':'pacote-controlado','purpose':'Dependência da aplicação.',
        'check':{'argv':[sys.executable,'-c',code],'purpose':'Verificar pacote.','timeout':10,'env':[]},
        'install':[{'argv':[sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).write_text("pronto")'],
                    'purpose':'Instalar pacote.','timeout':10,'env':[]}],
        'manual_instructions':'Instalar o pacote e retomar o build.'}


class ProgressApp(App):
    def compose(self):yield BuildProgress()


class EnvironmentPhaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    async def test_only_controller_checks_and_installs_and_phase_has_visible_progress(self):
        class AnalysisOnly(Builders):
            async def generate(inner,*args,**kwargs):
                request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1])
                if request['operation'] in ('environment','blueprint'):
                    self.assertFalse(kwargs['allow_tools'])
                if request['operation']=='write':self.assertTrue(kwargs['allow_tools'])
                return await super().generate(*args,**kwargs)
        agents=AnalysisOnly();agents.dependencies=[dependency(self.root)];events=[]
        engine=BuildEngine(self.state,agents,on_event=events.append);await engine.run()
        self.assertEqual((self.root/'dependency.checks').read_text(),'2')
        initial=next(e for e in events if e.environment_status=='running' and not e.environment_total)
        checking=next(e for e in events if any(i.kind=='preparation' and i.status=='running' and i.id.startswith('ENV-D') for i in e.build_items))
        self.assertEqual(next(i for i in events[-1].build_items if i.id=='ENV').status,'completed')
        self.assertEqual(engine.saved['environment']['dependencies'][0]['status'],'completed')
        self.assertEqual(events[-1].environment_ready,1)
        mapping=next(e for e in events if e.scopes_total and e.scopes_ready<e.scopes_total)
        app=ProgressApp()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.pause();tree=app.query_one(BuildProgress)
            tree.update_event(initial,True);await pilot.pause()
            self.assertIn('Preparação do ambiente',tree.query_one('#build-rows',Static).render().plain)
            tree.update_event(checking,True);await pilot.pause()
            text=tree.query_one('#build-rows',Static).render().plain
            for row in text.splitlines():
                if 'ENV' in row or 'pacote-controlado' in row:
                    self.assertEqual(row.count('○')+row.count('●'),1)
            self.assertEqual(tree.query_one(ProgressBar).total,1)
            tree.update_event(mapping,True);await pilot.pause()
            self.assertIn('Escopos preparados',str(tree.query_one('#build-summary',Static).render()))

    async def test_environment_block_preserves_phase_pending_installation(self):
        item=dependency(self.root);item['check']['argv']=[sys.executable,'-c','raise SystemExit(1)'];item['install']=[]
        agents=Builders();agents.dependencies=[item];events=[]
        engine=BuildEngine(self.state,agents,on_event=events.append)
        with self.assertRaises(DependenciesBlocked):await engine.run()
        saved=json.loads((self.root/'.harness/build/state.json').read_text())
        self.assertEqual(saved['environment']['status'],'rejected')
        self.assertEqual(saved['environment']['dependencies'][0]['step'],'blocked')
        self.assertEqual(saved['tasks'],{})
        self.assertIsNone(saved['blueprint'])
        self.assertEqual(next(i for i in events[-1].build_items if i.id=='ENV').status,'rejected')
        app=ProgressApp()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.pause();tree=app.query_one(BuildProgress);tree.update_event(events[-1],False)
            await pilot.pause()
            self.assertIn('instalação pendente',str(tree.query_one('#build-summary',Static).render()))

    async def test_resume_during_preparation_keeps_completed_application_tasks(self):
        await BuildEngine(self.state,Builders()).run()
        before=json.loads((self.root/'.harness/build/state.json').read_text())['tasks']
        second=Builders();engine=BuildEngine(self.state,second);await engine.run()
        self.assertEqual({k:p['gates'] for k,p in engine.progress.items()},{k:p['gates'] for k,p in before.items()})
        self.assertFalse(any(op=='write' for _,op,_ in second.calls))
        self.assertEqual(engine.saved['environment']['status'],'completed')

    async def test_cancelled_inventory_is_resumable_without_an_empty_blueprint(self):
        first=Builders();first.cancel=True;engine=BuildEngine(self.state,first)
        with self.assertRaises(asyncio.CancelledError):await engine.run()
        saved=json.loads((self.root/'.harness/build/state.json').read_text())
        self.assertIsNone(saved['blueprint']);self.assertEqual(saved['environment']['status'],'waiting')
        second=BuildEngine(self.state,Builders());await second.run()
        self.assertTrue(all(p['completed'] for p in second.progress.values()))


class AGYAnalysisPolicyTests(unittest.TestCase):
    def test_analysis_denies_observed_tool_names_and_keeps_structured_finish(self):
        selection=AgentSelection('agy','gemini-test','default')
        command,_=agy_command(selection,{},allow_tools=False)
        settings=json.loads(command[command.index('--settings')+1]);deny=settings['permissions']['deny']
        for name in ('command','run_command','manage_task'):self.assertIn(name+'(*)',deny)
        self.assertNotIn('finish(*)',deny)
        self.assertNotIn('--dangerously-skip-permissions',command)
        executable,_=agy_command(selection,{},allow_tools=True)
        self.assertIn('--dangerously-skip-permissions',executable)
