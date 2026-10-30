"""Provas operacionais de entrega sem reutilizar ambientes já instalados."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from textual.widgets import Button,Static

from harness.artifacts import read_status
from harness.build_artifacts import DependenciesBlocked
from harness.build_engine import BuildEngine
from harness.build_workspace import Workspace
from harness.delivery_models import StartupProbe
from harness.delivery_run import startup
from harness.build_models import CommandSpec
from harness.build_artifacts import BuildStore
from harness.app import HarnessApp
from harness.init_ui import InitExecutionStep
from test_wizard import test_catalog
from build_fixture import Builders
import test_build


def parsed(args):
    request=json.loads(args[2].rsplit('\n\nREQUEST:\n',1)[1]);return request['operation'],request['data']


def dependency(name,argv,manual):
    return {'name':name,'purpose':'Pré-requisito para instalar e executar a aplicação.',
        'check':{'argv':argv,'purpose':'Conferir '+name,'timeout':10,'env':[]},'install':[],
        'manual_instructions':'Instalar o pacote necessário pela plataforma.',
        'manual_commands':[{'argv':manual,'purpose':'Instalar '+name,'timeout':60,'env':[]}]}


class NodeAgents(Builders):
    def __init__(self):super().__init__();self.delivery_failures=0
    async def generate(self,*args,**kwargs):
        op,data=parsed(args)
        if op=='delivery_diagnosis':
            self.calls.append((args[1],op,data));self.delivery_failures+=1
            return json.dumps({'kind':'project','reason':'Pacote de tipos exigido e ausente.',
                'evidence':'TS2688','dependencies':[],'repair_files':['package.json','package-lock.json'],
                'instruction':'Adicionar @types/node ao desenvolvimento, preservando scripts e dependências.'})
        if op=='delivery_repair':
            self.calls.append((args[1],op,data));root=Path(kwargs['cwd'])
            package=json.loads((root/'package.json').read_text());package['devDependencies']['@types/node']='^24.0.0'
            (root/'package.json').write_text(json.dumps(package))
            (root/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{'':package}}))
            return json.dumps({'summary':'Tipos Node declarados no manifesto e lock.','files':['package.json','package-lock.json'],'scope_requests':[]})
        if op=='delivery_repair_review':
            self.calls.append((args[1],op,data))
            return json.dumps({'approved':True,'preserves_functionality':True,'preserves_validation':True,'reason':'Preserva o projeto e adiciona o pacote necessário.'})
        result=await super().generate(*args,**kwargs)
        if op=='delivery_plan':
            value=json.loads(result)
            value['prerequisites']+=[dependency('Node.js',['node','--version'],['sudo','apt-get','install','-y','nodejs']),
                                     dependency('npm',['npm','--version'],['sudo','apt-get','install','-y','npm'])]
            return json.dumps(value)
        return result


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    def engine(self,agents):
        e=BuildEngine(self.state,agents);agents.delay=.001
        async def recover(stage,message,attempt=1,task_id=None):
            e.announce(stage,message,attempt=attempt,task_id=task_id);await asyncio.sleep(0)
        e.recover=recover
        return e

    def node_project(self,types=False):
        package={'name':'fixture','type':'module','scripts':{'build':'tsc --noEmit && vite build','typecheck':'tsc --noEmit'},
            'dependencies':{'react':'^19.0.0'},'devDependencies':{'typescript':'^7.0.0'}}
        if types:package['devDependencies']['@types/node']='^24.0.0'
        (self.root/'package.json').write_text(json.dumps(package))
        (self.root/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{'':package}}))
        (self.root/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'types':['node']},'include':['src','server']}))
        (self.root/'node_modules').mkdir();(self.root/'node_modules/sentinel').write_text('Não copiar este runtime.')

    def npm_runner(self,calls,*,cancel_compile=False):
        async def run(spec,cwd,store,prefix,**kwargs):
            calls.append((spec.argv,str(cwd)))
            self.assertFalse(Path(cwd).is_relative_to(self.root))
            self.assertTrue((Path(cwd)/'.harness/delivery-owner.json').exists())
            self.assertFalse((Path(cwd)/'node_modules/sentinel').exists())
            if spec.argv[1]=='ci':
                (Path(cwd)/'node_modules').mkdir(exist_ok=True)
                return {'exit_code':0,'stdout':'Instalação limpa executada.','stderr':'','timeout':False}
            if cancel_compile:raise asyncio.CancelledError()
            package=json.loads((Path(cwd)/'package.json').read_text())
            if (spec.argv[-1]=='typecheck' or spec.argv[-1]=='build' and 'tsc' in package['scripts']['build']) and '@types/node' not in package['devDependencies']:
                return {'exit_code':1,'stdout':'','stderr':"TS2688: Cannot find type definition file for 'node'.",'timeout':False}
            return {'exit_code':0,'stdout':'Compilação verificada.','stderr':'','timeout':False}
        return run

    async def test_clean_install_and_missing_types_are_repaired_before_completed(self):
        self.node_project();agents=NodeAgents();e=self.engine(agents);calls=[]
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):
            await e.run()
        self.assertEqual(agents.delivery_failures,1)
        self.assertEqual(len([a for a,_ in calls if a[1]=='ci']),2)
        self.assertIn('@types/node',json.loads((self.root/'package.json').read_text())['devDependencies'])
        self.assertEqual(e.progress['BUILD-INSTALLATION']['gates'],['completed']*4)
        self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')
        doc=(self.root/'README.md').read_text()
        for value in ('react','^19.0.0','typescript','@types/node','npm install','sudo apt-get install -y nodejs','npm ci --no-audit --no-fund'):
            self.assertIn(value,doc)
        self.assertTrue((self.root/'.harness/build/delivery/approval.json').exists())
        # Reexecução preserva o reparo e reutiliza apenas a prova compatível salva.
        calls.clear();second=self.engine(NodeAgents())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await second.run()
        self.assertEqual(calls,[])
        self.assertEqual(second.progress['BUILD-INSTALLATION']['gates'],['completed']*4)

    async def test_code_is_completed_but_dependency_installation_is_explicitly_pending(self):
        class Missing(Builders):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_plan':
                    value=json.loads(result)
                    value['prerequisites'].append(dependency('make', [sys.executable,'-c','raise SystemExit(1)'],['sudo','apt-get','install','-y','make']))
                    return json.dumps(value)
                return result
        e=self.engine(Missing())
        await e.run()
        status=read_status(self.root)['modules']['build']
        self.assertEqual(status['status'],'completed')
        self.assertEqual(status['delivery_status'],'blocked')
        self.assertFalse(status['ready_to_run'])
        self.assertIn('sudo apt-get install -y make',(self.root/'README.md').read_text())
        self.assertIn('make',(self.root/'.harness/build/install-manual.sh').read_text())
        self.assertEqual(e.saved['delivery']['status'],'blocked')
        self.assertIn('Build concluído — instalação pendente',(self.root/'.harness/build/report.md').read_text())
        self.assertTrue((self.root/'dobro.py').exists())
        self.assertTrue(all(p['completed'] for p in e.progress.values()))

    async def test_missing_only_dependencies_shows_completed_and_revalidate_button(self):
        class Missing(Builders):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_plan':
                    value=json.loads(result)
                    value['prerequisites'].append(dependency('make',[sys.executable,'-c','raise SystemExit(1)'],['sudo','apt-get','install','-y','make']))
                    return json.dumps(value)
                return result
        app=HarnessApp(launch_dir=self.root,catalog=test_catalog(),agents=Missing());app.state=self.state
        app.build_agents=app.agents
        async with app.run_test(size=(150,54)) as pilot:
            await app.push_screen(InitExecutionStep(module='build'))
            async with asyncio.timeout(15):
                while app.screen.engine is None or app.screen.busy:await pilot.pause()
            self.assertEqual(read_status(self.root)['modules']['build']['status'],'completed')
            self.assertIn('instalação pendente',str(app.screen.query_one('#dash-status .dash-field-value',Static).render()).lower())
            self.assertEqual(str(app.screen.query_one('#init-retry',Button).label),'Validar instalação')

    async def test_unfinished_code_or_configuration_is_not_reported_completed(self):
        self.node_project(types=True);calls=[];failures=[0]
        runner=self.npm_runner(calls)
        async def broken(spec,cwd,store,prefix,**kwargs):
            result=await runner(spec,cwd,store,prefix,**kwargs)
            if spec.argv[-1]=='build':
                failures[0]+=1
                return dict(result,exit_code=1,stderr='TS2688: configuração continua incorreta.')
            return result
        e=self.engine(NodeAgents())
        with patch('harness.build_delivery.run_command',side_effect=broken):
            task=asyncio.create_task(e.run())
            async with asyncio.timeout(10):
                while failures[0]<3:await asyncio.sleep(.01)
            self.assertFalse(task.done())
            self.assertEqual(read_status(self.root)['modules']['build']['status'],'running')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task

    async def test_node_cannot_resolve_a_cached_package_from_the_original_project(self):
        self.node_project(types=True)
        package=self.root/'node_modules/cached-only-package';package.mkdir()
        (package/'package.json').write_text('{"name":"cached-only-package","type":"module","main":"index.js"}')
        (package/'index.js').write_text('export default 1;')
        (self.root/'server.js').write_text("import value from 'cached-only-package'; if(value!==1) process.exit(1);\n")
        failures=[0];calls=[]
        class Cached(NodeAgents):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args)
                if op=='delivery_diagnosis':
                    inner.calls.append((args[1],op,data));failures[0]+=1
                    return json.dumps({'kind':'transient','reason':'Diagnóstico de controle: importação não pode usar o projeto pai.',
                        'evidence':'ERR_MODULE_NOT_FOUND','dependencies':[],'repair_files':[],'instruction':'Revalidar isoladamente.'})
                result=await super().generate(*args,**kwargs)
                if op=='delivery_plan':
                    value=json.loads(result);value['startup'][0]['command']['argv']=['node','server.js'];return json.dumps(value)
                return result
        e=self.engine(Cached())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):
            task=asyncio.create_task(e.run())
            async with asyncio.timeout(10):
                while failures[0]<2:await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertEqual(e.saved['delivery']['status'],'repairing')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue((package/'index.js').exists())

    async def test_real_npm_clean_install_build_and_http_startup_pass(self):
        package={'name':'delivery-smoke','version':'1.0.0','private':True,'type':'module',
            'scripts':{'build':'node --check server.js','start':'node server.js'}}
        (self.root/'package.json').write_text(json.dumps(package))
        (self.root/'package-lock.json').write_text(json.dumps({'name':'delivery-smoke','version':'1.0.0','lockfileVersion':3,
            'requires':True,'packages':{'':{'name':'delivery-smoke','version':'1.0.0'}}}))
        (self.root/'server.js').write_text("import http from 'node:http'; http.createServer((req,res)=>res.end('ready')).listen(Number(process.env.PORT),'127.0.0.1');\n")
        class Actual(NodeAgents):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_plan':
                    value=json.loads(result);value['startup'][0].update(mode='http',path='/',expected_text='ready')
                    value['startup'][0]['command']['argv']=['node','server.js']
                    return json.dumps(value)
                return result
        e=self.engine(Actual());await e.run()
        proof=json.loads((self.root/'.harness/build/delivery/approval.json').read_text())
        self.assertEqual(proof['status'],'approved')
        self.assertEqual(proof['clean']['results']['install-0']['exit_code'],0)
        self.assertEqual(proof['clean']['results']['compile-0']['exit_code'],0)
        self.assertEqual(proof['clean']['results']['startup-0']['exit_code'],0)
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])

    async def test_resume_keeps_successful_clean_install_without_redoing_tasks(self):
        self.node_project(types=True);calls=[];first=self.engine(NodeAgents())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls,cancel_compile=True)):
            with self.assertRaises(asyncio.CancelledError):await first.run()
        agents=NodeAgents();second=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await second.run()
        self.assertEqual(len([a for a,_ in calls if a[1]=='ci']),1)
        self.assertFalse(any(op=='write' for _,op,_ in agents.calls))
        self.assertEqual(second.saved['delivery']['status'],'approved')

    async def test_configuration_method_does_not_block_a_working_repair(self):
        self.node_project();calls=[]
        class Configure(NodeAgents):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_repair':
                    path=Path(kwargs['cwd'])/'tsconfig.json';config=json.loads(path.read_text())
                    config['compilerOptions']['strict']=False;path.write_text(json.dumps(config))
                return result
        agents=Configure();e=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])
        self.assertFalse(any(op=='delivery_repair_review' for _,op,_ in agents.calls))

    async def test_failed_execution_of_a_repair_returns_to_writer_instead_of_reusing_candidate(self):
        self.node_project();calls=[]
        class TwoRepairs(NodeAgents):
            writes=0
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_repair':
                    inner.writes+=1
                    if inner.writes>1:
                        path=Path(kwargs['cwd'])/'package.json';package=json.loads(path.read_text())
                        package['devDependencies']['@types/react']='^19.0.0';path.write_text(json.dumps(package))
                        (path.parent/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{'':package}}))
                return result
        normal=self.npm_runner(calls)
        async def execution(spec,cwd,store,prefix,**kwargs):
            if len(calls)>20:raise AssertionError('Reprovou e reutilizou indefinidamente a mesma candidata.')
            result=await normal(spec,cwd,store,prefix,**kwargs)
            package=json.loads((Path(cwd)/'package.json').read_text())
            if result['exit_code']==0 and spec.argv[-1]=='build' and '@types/react' not in package['devDependencies']:
                result.update(exit_code=1,stderr='TS2688: React types missing in build.')
            return result
        agents=TwoRepairs();e=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=execution):await e.run()
        self.assertEqual(agents.writes,2)
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])

    async def test_separate_typecheck_is_diagnostic_when_application_builds_and_starts(self):
        self.node_project();calls=[]
        package=json.loads((self.root/'package.json').read_text());package['scripts']['build']='vite build'
        (self.root/'package.json').write_text(json.dumps(package))
        e=self.engine(NodeAgents())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])
        self.assertEqual(len(e.saved['delivery']['diagnostics']),1)
        self.assertIn('Diagnósticos adicionais',(self.root/'README.md').read_text())
        self.assertNotIn('BUILD-INSTALLATION',e.tasks)

    async def test_explicit_installation_recipe_is_executed_and_documented(self):
        self.node_project(types=True);calls=[]
        class Recipe(NodeAgents):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_plan':
                    value=json.loads(result);value['install']=[{'argv':['npm','ci','--ignore-scripts'],
                        'purpose':'Receita explícita cuja funcionalidade será conferida.','timeout':60,'env':[]}]
                    return json.dumps(value)
                return result
        e=self.engine(Recipe())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertTrue(any(argv==['npm','ci','--ignore-scripts'] for argv,_ in calls))
        self.assertIn('npm ci --ignore-scripts',(self.root/'README.md').read_text())

    async def test_production_repair_review_gets_files_and_requests_context_without_rewriting(self):
        self.node_project();(self.root/'extra-info.md').write_text('Contexto adicional da aplicação.')
        calls=[]
        class Context(NodeAgents):
            reviews=0;writes=0
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args)
                if op=='delivery_repair':
                    inner.writes+=1;result=await super().generate(*args,**kwargs)
                    path=Path(kwargs['cwd'])/'dobro.py';path.write_text(path.read_text()+'# Unidade preservada.\n')
                    value=json.loads(result);value['files'].append('dobro.py');return json.dumps(value)
                if op=='delivery_repair_review':
                    inner.calls.append((args[1],op,data));inner.reviews+=1
                    entries={f['file']:f for f in data['file_evidence']['files']}
                    entry=entries['dobro.py']
                    self.assertIn('def dobrar',entry['before']['content']);self.assertIn('Unidade preservada',entry['after']['content'])
                    self.assertIn('+# Unidade preservada.',entry['diff'])
                    self.assertFalse(data['failure_context']['applies_to_candidate'])
                    if inner.reviews==1:
                        return json.dumps({'approved':False,'needs_context':True,'context_files':['extra-info.md'],
                            'preserves_functionality':False,'preserves_validation':False,'reason':'Solicitar apenas um arquivo adicional de referência.'})
                    self.assertEqual(entries['extra-info.md']['after']['content'],'Contexto adicional da aplicação.')
                    return json.dumps({'approved':True,'preserves_functionality':True,'preserves_validation':True,
                        'reason':'A evidência confirma o reparo funcional sem perda de código.'})
                return await super().generate(*args,**kwargs)
        agents=Context();e=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertEqual(agents.writes,1);self.assertEqual(agents.reviews,2)

    async def test_operational_repair_is_decided_by_execution_without_extra_textual_review(self):
        self.node_project();calls=[]
        class Reconsider(NodeAgents):
            reviews=0
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_repair_review':
                    inner.reviews+=1
                    if inner.reviews<=2:
                        return json.dumps({'approved':False,'preserves_functionality':True,'preserves_validation':True,
                            'reason':'Reavaliar a configuração contra o manifesto atual.'})
                return result
        agents=Reconsider();e=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertEqual(agents.reviews,0)
        self.assertEqual(agents.delivery_failures,1)
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])

    async def test_unreviewed_delivery_repair_survives_cancellation_without_being_published(self):
        self.node_project();calls=[]
        runtime_names=(':memory:',':memory:-wal',':memory:-shm')
        for name in runtime_names:(self.root/name).write_bytes(b'dados locais preservados')
        class Interrupted(NodeAgents):
            async def generate(inner,*args,**kwargs):
                op,data=parsed(args);result=await super().generate(*args,**kwargs)
                if op=='delivery_repair':raise asyncio.CancelledError()
                return result
        first=self.engine(Interrupted())
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):
            with self.assertRaises(asyncio.CancelledError):await first.run()
        self.assertNotIn('@types/node',json.loads((self.root/'package.json').read_text())['devDependencies'])
        # Recibo legado registrava como removidos bancos não copiados ao reparo.
        workspace=Path(first.saved['delivery']['repair_workspace'])/'.harness/workspace.json'
        saved=json.loads(workspace.read_text())
        for name in runtime_names:saved['base'][name]=saved['base_root'][name]
        workspace.write_text(json.dumps(saved))
        agents=NodeAgents();second=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await second.run()
        self.assertFalse(any(op=='delivery_repair_review' for _,op,_ in agents.calls))
        self.assertFalse(any(op=='write' for _,op,_ in agents.calls))
        for name in runtime_names:self.assertEqual((self.root/name).read_bytes(),b'dados locais preservados')
        self.assertIn('@types/node',json.loads((self.root/'package.json').read_text())['devDependencies'])

    async def test_interrupted_installation_repair_publication_does_not_repeat_application_tasks(self):
        self.node_project();calls=[];original=Workspace.publish
        def interrupted(workspace,scope,tree):
            result=original(workspace,scope,tree)
            if scope.task_id=='BUILD-INSTALLATION':raise RuntimeError('Interrupção após publicar o reparo')
            return result
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)),patch.object(Workspace,'publish',interrupted):
            with self.assertRaisesRegex(RuntimeError,'publicar o reparo'):await self.engine(NodeAgents()).run()
        agents=NodeAgents();e=self.engine(agents)
        with patch('harness.build_delivery.run_command',side_effect=self.npm_runner(calls)):await e.run()
        self.assertFalse(any(op in ('write','delivery_repair') for _,op,_ in agents.calls))
        self.assertTrue(read_status(self.root)['modules']['build']['ready_to_run'])

    async def test_http_smoke_executes_and_terminates_only_its_own_server(self):
        store=BuildStore(self.root);store.root.mkdir(parents=True,exist_ok=True)
        script=self.root/'health.py';script.write_text('from http.server import HTTPServer,BaseHTTPRequestHandler\nimport os\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"ready")\nHTTPServer(("127.0.0.1",int(os.environ["PORT"])),H).serve_forever()\n')
        probe=StartupProbe(command=CommandSpec(argv=['python3','health.py'],purpose='Iniciar API.',timeout=5),mode='http',path='/health',expected_text='ready')
        result=await startup(probe,self.root,store,'log/smoke')
        self.assertEqual(result['exit_code'],0)
        self.assertTrue(store.path('log/smoke.result.json').exists())
