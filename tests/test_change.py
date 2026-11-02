"""CHANGE direto: escrita real, verificação, cancelamento e retomada."""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from capivara2ptn.change_flow import ChangeRun
from capivara2ptn.change_patch import stage_patch, recover_patch
from capivara2ptn.change_protocol import render_plan, task_states, validate_plan
from capivara2ptn.change_prompts import change_prompt
from capivara2ptn.change_source import ChangeError, ChangeConflict, Secrets, app_path, fingerprint, sha
from capivara2ptn.change_state import ChangeStore
from capivara2ptn.change_verify import verify_change, verification_spec
from capivara2ptn.doc_center_artifacts import Cleaner, change_artifacts
from capivara2ptn.project_config import ExecutionOptions
from capivara2ptn.providers import CliRunner, ProviderError, Selection

REQUEST = 'Corrigir telefone: preservar todos os dígitos e remover espaços, mantendo o padrão existente.'
SOURCE = 'def normalize(phone):\n    return phone[:8]\n'
RESULT = {'status': 'sucesso', 'comandos': [{'comando': 'verificação', 'codigo_retorno': 0, 'saida': 'OK'}], 'motivo': None}


def specification():
    return {'commands': [{'directory': '.', 'argv': [sys.executable, '-B', '-c',
        "import app; assert app.normalize('11 987654321') == '11987654321', 'telefone incorreto'; print('telefone OK')"],
        'purpose': 'Confirmar normalização e importação.'}], 'limitation': None}


class FakeChangeRunner:
    selection = Selection('codex', 'fixture', 'Default')
    secrets = []
    def __init__(self, cwd, *, wrong_first=False, question=False):
        self.cwd, self.wrong_first, self.question = cwd, wrong_first, question
        self.calls = []
    async def invoke(self, instructions, data, **kwargs):
        self.calls.append((instructions, data, kwargs))
        assert kwargs['access'] == 'write'
        if self.question and not data['history']:
            return json.dumps({'kind': 'question', 'text': 'Remover somente espaços ASCII?', 'choices': ['Sim', 'Não']})
        path = self.cwd / 'app.py'
        source = path.read_text()
        if self.wrong_first and len(self.calls) == 1:
            source = source.replace('return phone[:8]', 'return phone')
        elif 'return phone.replace(" ", "")' not in source:
            source = source.replace('return phone[:8]', 'return phone.replace(" ", "")') if 'return phone[:8]' in source else source.replace('return phone', 'return phone.replace(" ", "")')
        path.write_text(source)
        return json.dumps({'kind': 'completed', 'summary': 'Normalização corrigida, preservando assinatura e padrões.', 'verification': specification()})


async def no_question(question):raise AssertionError('Não há decisão funcional ausente: ' + str(question))


class ChangeBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capivara-change-')
        self.root = Path(self.temp.name)
        self.project = self.root / 'project';self.project.mkdir()
        (self.project / 'app.py').write_text(SOURCE)
        (self.project / 'unrelated.txt').write_text('modificação do usuário')
        self.config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')});self.config.start()
        self.secrets = Secrets(self.project)
    def tearDown(self):
        self.config.stop();self.temp.cleanup()
    def store(self):
        store = ChangeStore(self.project);store.create(REQUEST);return store
    async def run_flow(self, store=None, runner=None, ask=no_question, verifier=None, **kwargs):
        store = store or self.store();runner = runner or FakeChangeRunner(self.project)
        run = ChangeRun(store, runner, ExecutionOptions(), self.secrets, kwargs.get('update', lambda *a:None), ask)
        if verifier is None:result = await run.run()
        else:
            with patch('capivara2ptn.change_flow.verify_change', new=verifier):result = await run.run()
        return result, store, runner


class ChangeTests(ChangeBase):
    async def test_request_goes_directly_to_writer_and_real_verification(self):
        path, store, writer = await self.run_flow()
        self.assertEqual(len(writer.calls), 1)
        instructions, data, kwargs = writer.calls[0]
        self.assertIn('solicitação direta', instructions)
        self.assertEqual(data['request'], REQUEST)
        self.assertEqual(data['project'], str(self.project))
        self.assertEqual(kwargs, {'access': 'write'})
        self.assertIsNone(store.state['plan']);self.assertEqual(store.state['tasks'], [])
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(store.state['verification']['comandos'][0]['codigo_retorno'], 0)
        self.assertIn('telefone OK', store.state['verification']['comandos'][0]['saida'])
        self.assertEqual(store.state['direct']['changes'], [{'arquivo': 'app.py', 'tipo': 'modificado'}])
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'modificação do usuário')
        self.assertGreater(store.elapsed(), 0);self.assertIsNone(store.started)
        self.assertTrue(path.exists());self.assertFalse((self.project / '.harness/dados').exists())
        self.assertFalse(store.path('plano.md').exists())

    async def test_real_failure_returns_actual_output_to_same_writer(self):
        _, store, writer = await self.run_flow(runner=FakeChangeRunner(self.project, wrong_first=True))
        self.assertEqual(len(writer.calls), 2)
        feedback = writer.calls[1][1]['verification_feedback']
        self.assertEqual(feedback['comandos'][0]['codigo_retorno'], 1)
        self.assertIn('AssertionError: telefone incorreto', feedback['comandos'][0]['saida'])
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(store.state['selection'], {'provider': 'codex', 'model': 'fixture', 'effort': 'Default'})

    async def test_feedback_has_no_attempt_limit_or_intermediate_approval(self):
        fail = {'status': 'falha', 'comandos': [{'codigo_retorno': 1, 'saida': 'defeito real', 'comando': 'python3'}]}
        _, store, writer = await self.run_flow(verifier=AsyncMock(side_effect=[fail] * 8 + [RESULT]))
        self.assertEqual(len(writer.calls), 9)
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(all('gate' not in data and 'task' not in data for _, data, _ in writer.calls))
        self.assertFalse(any(event['kind'] == 'approved' for event in store.state['events']))

    async def test_cancel_keeps_partial_edits_and_resume_reads_current_project(self):
        ready = asyncio.Event()
        class Waiting(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                await super(Waiting, inner).invoke(*args, **kwargs)
                ready.set();await asyncio.Event().wait()
        store = self.store()
        task = asyncio.create_task(self.run_flow(store, Waiting(self.project)))
        await asyncio.wait_for(ready.wait(), 5);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(store.state['status'], 'interrupted');self.assertIsNone(store.started)
        self.assertIn('phone.replace', (self.project / 'app.py').read_text())
        original_elapsed = store.elapsed()
        reopened = ChangeStore(self.project);reopened.select(store.identifier)
        _, reopened, writer = await self.run_flow(reopened)
        self.assertTrue(writer.calls[0][1]['resuming'])
        self.assertEqual(len(writer.calls), 1);self.assertGreater(reopened.elapsed(), original_elapsed)
        self.assertFalse(any(e['kind'] == 'source_changed' for e in reopened.state['events']))

    async def test_cancel_verification_resumes_without_calling_writer(self):
        ready = asyncio.Event()
        async def wait(*args):ready.set();await asyncio.Event().wait()
        store = self.store()
        task = asyncio.create_task(self.run_flow(store, verifier=wait))
        await asyncio.wait_for(ready.wait(), 5);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(store.state['phase'], 'verification')
        _, store, writer = await self.run_flow(store)
        self.assertEqual(writer.calls, []);self.assertEqual(store.state['status'], 'done')

    async def test_essential_question_is_persisted_without_full_interview(self):
        async def pause(question):return None
        store = self.store()
        result, store, writer = await self.run_flow(store, FakeChangeRunner(self.project, question=True), ask=pause)
        self.assertIsNone(result);self.assertEqual(len(writer.calls), 1)
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
        self.assertTrue(store.state['question']);self.assertIsNone(store.started)
        async def answer(question):
            elapsed = store.elapsed();await asyncio.sleep(.05)
            self.assertEqual(store.elapsed(), elapsed)
            return 'Somente espaços ASCII'
        _, store, writer = await self.run_flow(store, ask=answer)
        self.assertEqual(len(writer.calls), 1);self.assertIsNone(store.state['question'])
        self.assertEqual(writer.calls[0][1]['history'][0]['answer'], 'Somente espaços ASCII')

    async def test_finished_request_does_not_reexecute(self):
        _, store, _ = await self.run_flow()
        elapsed = store.elapsed()
        _, store, writer = await self.run_flow(store)
        self.assertEqual(writer.calls, []);self.assertEqual(store.elapsed(), elapsed)

    async def test_old_request_is_resumed_directly_and_history_is_preserved(self):
        store = self.store()
        store.state.update(phase='approval', status='approval', plan={'objective': 'pedido anterior'}, investigation={'summary': 'já investigado'})
        store.write_json('baseline.json', {})  # arquivos criados por uma execução antiga também contam
        store.write('plano.md', 'plano anterior preservado');store.save()
        _, store, writer = await self.run_flow(store)
        self.assertEqual(len(writer.calls), 1)
        self.assertTrue(store.path('historico-fluxo-anterior.json').exists())
        self.assertEqual(store.path('plano.md').read_text(), 'plano anterior preservado')
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(store.state['direct']['changes'][0], {'arquivo': 'app.py', 'tipo': 'criado'})

    async def test_external_edits_are_preserved_and_not_attributed_to_change(self):
        store = self.store()
        async def stop(*args):raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):await self.run_flow(store, verifier=stop)
        (self.project / 'unrelated.txt').write_text('edição externa posterior')
        (self.project / 'app.py').write_text((self.project / 'app.py').read_text() + '# preservar comentário\n')
        _, store, writer = await self.run_flow(store)
        self.assertIn('mudou desde o checkpoint', writer.calls[0][1]['feedback'])
        self.assertIn('# preservar comentário', (self.project / 'app.py').read_text())
        self.assertEqual(store.state['direct']['changes'], [{'arquivo': 'app.py', 'tipo': 'modificado'}])

    async def test_failed_conclusion_write_does_not_repeat_writer_or_verification(self):
        store = self.store();write = store.write
        def failure(name, text):
            if name == 'conclusao.md':raise OSError('disco indisponível')
            write(name, text)
        with patch.object(store, 'write', side_effect=failure):
            with self.assertRaises(OSError):await self.run_flow(store)
        check = AsyncMock(side_effect=AssertionError('Não repetir verificação válida'))
        _, store, writer = await self.run_flow(store, verifier=check)
        self.assertEqual(writer.calls, []);self.assertEqual(store.state['status'], 'done')

    async def test_unavailable_check_is_never_reported_as_verified_success(self):
        class NoCheck(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                value = json.loads(await super(NoCheck, inner).invoke(*args, **kwargs))
                value['verification'] = {'commands': [], 'limitation': 'Sem serviço local seguro disponível.'}
                return json.dumps(value)
        _, store, _ = await self.run_flow(runner=NoCheck(self.project))
        self.assertEqual(store.state['status'], 'done_with_limits')
        self.assertEqual(store.state['verification']['status'], 'indisponivel')
        self.assertIn('Sem serviço', store.path('conclusao.md').read_text())

    async def test_response_format_retry_preserves_edits_already_made(self):
        class BadResponse(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                result = await super(BadResponse, inner).invoke(*args, **kwargs)
                return 'Resposta incompleta' if len(inner.calls) == 1 else result
        _, store, writer = await self.run_flow(runner=BadResponse(self.project))
        self.assertEqual(len(writer.calls), 2)
        self.assertIn('não as repita', writer.calls[1][1]['feedback'])
        self.assertEqual(store.state['status'], 'done')

    async def test_provider_failure_keeps_work_and_same_selection(self):
        class OfflineOnce(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                value = await super(OfflineOnce, inner).invoke(*args, **kwargs)
                if len(inner.calls) == 1:raise ProviderError('Falha de conexão')
                return value
        _, store, writer = await self.run_flow(runner=OfflineOnce(self.project))
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(sum(e['kind'] == 'provider_retry' for e in store.state['events']), 1)

    async def test_credentials_are_masked_in_feedback_report_and_real_check_logs(self):
        (self.project / '.env').write_text('API_TOKEN=local-env-canary\n')
        self.secrets = Secrets(self.project)
        class Sensitive(FakeChangeRunner):
            secrets = ['registry-token-canary']
            async def invoke(inner, *args, **kwargs):
                value = json.loads(await super(Sensitive, inner).invoke(*args, **kwargs))
                value['summary'] += ' local-env-canary registry-token-canary'
                value['verification']['commands'][0]['argv'] = [sys.executable, '-c', "import os; print(os.environ['CAPIVARA_QA_TOKEN'])"]
                return json.dumps(value)
        with patch.dict(os.environ, {'CAPIVARA_QA_TOKEN': 'registry-token-canary'}):
            _, store, _ = await self.run_flow(runner=Sensitive(self.project))
        combined = '\n'.join(p.read_text() for p in store.path('').rglob('*') if p.is_file() and p.suffix in {'.json', '.md', '.log'})
        self.assertNotIn('local-env-canary', combined);self.assertNotIn('registry-token-canary', combined)
        self.assertTrue((self.project / '.env').exists())

    async def test_changed_files_omit_control_dependencies_and_build_outputs(self):
        class WithOutput(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                value = await super(WithOutput, inner).invoke(*args, **kwargs)
                for directory in ('dist','build','node_modules','vendor'):
                    (inner.cwd / directory).mkdir(exist_ok=True)
                    (inner.cwd / directory / 'out.txt').write_text('gerado')
                return value
        _, store, _ = await self.run_flow(runner=WithOutput(self.project))
        self.assertEqual(store.state['direct']['changes'], [{'arquivo': 'app.py', 'tipo': 'modificado'}])

    async def test_doc_center_exports_direct_changes_without_tasks(self):
        _, store, _ = await self.run_flow()
        artifacts = change_artifacts(self.project, Cleaner(self.project), store.identifier)
        self.assertEqual(len(artifacts), 2)
        self.assertIn('app.py: modificado', artifacts[0]['content'])
        self.assertIn('Normalização corrigida', artifacts[0]['content'])
        self.assertIn('app.py', artifacts[1]['content'])
        self.assertNotIn('"saida":', artifacts[0]['content'])  # não exporta stdout

    async def test_new_direct_prompt_installs_without_overwriting_edited_old_prompts(self):
        from capivara2ptn.prompt_files import prompt_directory
        old = change_prompt('change-write.md')
        path = prompt_directory() / 'change-write.md';path.write_text('customizado')
        self.assertIn('solicitação direta', change_prompt('change-direct.md'))
        self.assertEqual(path.read_text(), 'customizado')
        self.assertTrue(old)

    async def test_real_cli_adapter_runs_in_project_with_write_access(self):
        cli = self.root / 'codex-stub'
        cli.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
payload=sys.stdin.read().split('DADOS DE ENTRADA (JSON)\\n',1)[1]
data=json.loads(payload)
assert os.getcwd()==data['project']==os.environ['PWD']
assert sys.argv[sys.argv.index('--sandbox')+1]=='workspace-write'
p=pathlib.Path('app.py');p.write_text(p.read_text().replace('return phone[:8]', 'return phone.replace(" ", "")'))
answer={'kind':'completed','summary':'Correção direta pela CLI.','verification':{'commands':[{'directory':'.','argv':[sys.executable,'-B','-c',"import app; assert app.normalize('11 987654321')=='11987654321'"],'purpose':'Validar correção.'}],'limitation':None}}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(answer)}}))
''')
        cli.chmod(0o700)
        which = shutil.which
        with patch('capivara2ptn.providers.shutil.which', side_effect=lambda value,*a,**k: str(cli) if value == 'codex' else which(value,*a,**k)):
            writer = CliRunner(Selection('codex','fixture','High'), self.project, lambda value:None)
            _, store, _ = await self.run_flow(runner=writer)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(store.state['selection']['effort'], 'High')

    async def test_verification_cancellation_terminates_real_child_and_resumes_check(self):
        ready = asyncio.Event();pid = []
        class SlowCheck(FakeChangeRunner):
            async def invoke(inner, *args, **kwargs):
                value = json.loads(await super(SlowCheck, inner).invoke(*args, **kwargs))
                value['verification']['commands'][0]['argv'] = [sys.executable,'-u','-c','import os,time; print("CHILD_PID="+str(os.getpid())); time.sleep(60)']
                return json.dumps(value)
        def update(state,message):
            if message.startswith('CHILD_PID='):
                pid.append(int(message.split('=',1)[1]));ready.set()
        store = self.store()
        task = asyncio.create_task(self.run_flow(store, SlowCheck(self.project), update=update))
        await asyncio.wait_for(ready.wait(), 5);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        with self.assertRaises(ProcessLookupError):os.kill(pid[0], 0)
        self.assertEqual(store.state['status'], 'interrupted')
        _, store, writer = await self.run_flow(store, verifier=AsyncMock(return_value=RESULT))
        self.assertEqual(writer.calls, []);self.assertEqual(store.state['status'], 'done')

    async def test_verification_paths_cannot_escape_project_or_enter_harness(self):
        for directory in ('../', str(self.root), '.harness', 'app.py'):
            with self.assertRaises(ChangeError):verification_spec({'commands':[{'directory':directory, 'argv':['true']}], 'limitation':None},self.project)
        (self.project/'external').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ChangeError):verification_spec({'commands':[{'directory':'external','argv':['true']}]},self.project)
        with self.assertRaises(ChangeError):verification_spec({'commands':[]},self.project)

    async def test_multiple_requests_share_execution_lock(self):
        first = self.store()
        with first.execution():
            second = self.store()
            self.assertNotEqual(first.identifier, second.identifier)
            with self.assertRaises(ChangeError):
                with second.execution():pass


    async def test_git_reference_and_unrelated_dirty_file_are_preserved(self):
        import shutil,subprocess
        if not shutil.which('git'):self.skipTest('Git não disponível.')
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.project), *args], stderr=subprocess.DEVNULL)
        git('init', '-q')
        git('-c', 'user.name=Teste', '-c', 'user.email=teste@example.invalid', 'add', 'app.py', 'unrelated.txt')
        git('-c', 'user.name=Teste', '-c', 'user.email=teste@example.invalid', 'commit', '-qm', 'ponto inicial')
        head = git('rev-parse', 'HEAD').decode().strip()
        (self.project / 'unrelated.txt').write_text('edição não relacionada anterior')
        _, store, _ = await self.run_flow()
        self.assertTrue(store.state['recovery']['available'])
        self.assertEqual(store.state['recovery']['head'], head)
        self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), head)
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'edição não relacionada anterior')


def plan():
    return {'kind': 'plan', 'objective': 'Corrigir normalização de telefone', 'current_behavior': 'Trunca telefone.',
            'desired_behavior': 'Preservar dígitos e remover espaços.', 'preserve': ['Assinatura normalize(phone).'], 'limitations': [],
            'tasks': [{'id': '1', 'title': 'Corrigir normalização', 'description': 'Alterar somente a normalização de telefone.',
                       'files': ['app.py'], 'dependencies': [], 'inspection_only': False}],
            'verification': {'commands': [{'directory': '.', 'argv': ['python3', '-c', "import app; assert app.normalize('11 987654321') == '11987654321'"],
                                           'purpose': 'Confirmar correção do telefone e importação do componente.'}], 'limitation': None}}


class LegacyPatchTests(ChangeBase):
    """Recuperação de journals antigos preservada, fora do fluxo direto."""

    async def test_sensitive_files_and_path_escapes_are_refused(self):
        for path in ('.harness/state.json', '../outside.py', '/tmp/outside.py', 'src/../app.py', 'src//app.py', '.env', 'node_modules/a.js'):
            with self.assertRaises(ChangeError):app_path(self.project, path, writable=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (self.project / 'link').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ChangeError):app_path(self.project, 'link/file.py', writable=True)

    def approved_store(self, paths):
        store = self.store()
        value = plan()
        value['tasks'][0]['files'] = paths
        value = validate_plan(value, self.project)
        store.state['plan'] = value
        store.state['tasks'] = task_states(value)
        markdown = render_plan(value)
        store.write('plano.md', markdown)
        store.state['approval'] = {'sha256': sha(json.dumps(value, ensure_ascii=False, sort_keys=True)), 'markdown_sha256': sha(markdown)}
        store.state['expected'] = {p: fingerprint(self.project, p) for p in paths}
        store.state.update(phase='execution', status='execution')
        store.save()
        return store

    async def test_patch_cannot_modify_unapproved_or_ambiguous_content(self):
        store = self.approved_store(['app.py'])
        task = store.state['tasks'][0]
        outside = stage_patch(store, task, {'changes': [{'path': 'unrelated.txt', 'action': 'delete'}]}, self.secrets)
        self.assertIn('scope_expansion', outside)
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'modificação do usuário')
        bad = {'changes': [{'path': 'app.py', 'action': 'edit', 'base_fingerprint': fingerprint(self.project, 'app.py'),
                             'edits': [{'old': 'não existe', 'new': 'troca'}]}]}
        with self.assertRaises(ChangeError):stage_patch(store, task, bad, self.secrets)
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
        self.assertNotIn('pending_apply', store.state)

    async def test_patch_create_edit_delete_and_modes_are_recorded(self):
        (self.project / 'obsolete.txt').write_text('antigo')
        store = self.approved_store(['app.py', 'new.py', 'obsolete.txt'])
        task = store.state['tasks'][0]
        patch = {'changes': [
            {'path': 'app.py', 'action': 'edit', 'base_fingerprint': fingerprint(self.project, 'app.py'),
             'edits': [{'old': 'return phone[:8]', 'new': 'return phone'}]},
            {'path': 'new.py', 'action': 'create', 'content': 'print("ok")\n', 'executable': True},
            {'path': 'obsolete.txt', 'action': 'delete', 'base_fingerprint': fingerprint(self.project, 'obsolete.txt')}]}
        stage_patch(store, task, patch, self.secrets)
        self.assertFalse((self.project / 'obsolete.txt').exists())
        self.assertTrue((self.project / 'new.py').stat().st_mode & 0o111)
        self.assertEqual(task['operation'], 'g1')
        self.assertEqual(store.state['expected']['new.py'], fingerprint(self.project, 'new.py'))
        self.assertTrue(list(store.path('transacoes').rglob('*.antes.bin')))

    async def test_interrupted_multi_file_apply_recovers_without_duplicate_edits(self):
        from capivara2ptn.change_patch import atomic_app
        store = self.approved_store(['app.py', 'new.py'])
        task = store.state['tasks'][0]
        value = {'changes': [
            {'path': 'app.py', 'action': 'edit', 'base_fingerprint': fingerprint(self.project, 'app.py'),
             'edits': [{'old': 'return phone[:8]', 'new': 'return phone # corrigido'}]},
            {'path': 'new.py', 'action': 'create', 'content': 'ok = True\n'}]}
        calls = 0
        def stop_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            atomic_app(*args, **kwargs)
            if calls == 1:raise asyncio.CancelledError
        with patch('capivara2ptn.change_patch.atomic_app', side_effect=stop_after_first):
            with self.assertRaises(asyncio.CancelledError):stage_patch(store, task, value, self.secrets)
        loaded = ChangeStore(self.project)
        loaded.select(store.identifier)
        recover_patch(loaded)
        self.assertEqual((self.project / 'app.py').read_text().count('# corrigido'), 1)
        self.assertTrue((self.project / 'new.py').exists())
        self.assertNotIn('pending_apply', loaded.state)
        self.assertEqual(loaded.state['tasks'][0]['operation'], 'g1')

    async def test_interrupted_apply_never_overwrites_concurrent_user_changes(self):
        from capivara2ptn.change_patch import atomic_app
        store = self.approved_store(['app.py', 'new.py'])
        value = {'changes': [
            {'path': 'app.py', 'action': 'edit', 'base_fingerprint': fingerprint(self.project, 'app.py'),
             'edits': [{'old': 'return phone[:8]', 'new': 'return phone'}]},
            {'path': 'new.py', 'action': 'create', 'content': 'generated = True\n'}]}
        def interrupted(*args, **kwargs):
            atomic_app(*args, **kwargs)
            raise asyncio.CancelledError
        with patch('capivara2ptn.change_patch.atomic_app', side_effect=interrupted):
            with self.assertRaises(asyncio.CancelledError):stage_patch(store, store.state['tasks'][0], value, self.secrets)
        (self.project / 'new.py').write_text('user = True\n')
        loaded = ChangeStore(self.project)
        loaded.select(store.identifier)
        with self.assertRaises(ChangeConflict):recover_patch(loaded)
        self.assertEqual((self.project / 'new.py').read_text(), 'user = True\n')
        loaded.refresh_conflicts(['new.py'])
        self.assertNotIn('pending_apply', loaded.state)
        self.assertEqual(loaded.state['phase'], 'investigation')
        self.assertIsNone(loaded.state['approval'])
        self.assertEqual((self.project / 'new.py').read_text(), 'user = True\n')

    async def test_approved_tasks_cannot_be_replaced_by_editing_only_state(self):
        store = self.approved_store(['app.py'])
        store.state['tasks'][0]['files'].append('unrelated.txt')
        with self.assertRaises(ChangeError):
            stage_patch(store, store.state['tasks'][0], {'changes': []}, self.secrets)
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'modificação do usuário')

    async def test_configuration_and_request_secret_scalars_are_sanitized(self):
        from capivara2ptn.change_source import read_chunk, search, catalog, MASK
        (self.project / 'settings.yaml').write_text('api_token: opaque-configuration-canary\nsenha: segredo-de-configuracao\nport: 8080\n')
        snippet = read_chunk(self.project, 'settings.yaml', 1, self.secrets)
        self.assertNotIn('opaque-configuration-canary', snippet['text'])
        self.assertNotIn('segredo-de-configuracao', snippet['text'])
        self.assertIn('8080', snippet['text'])
        hits, _ = search(self.project, catalog(self.project), 'api_token', self.secrets)
        self.assertNotIn('opaque-configuration-canary', json.dumps(hits))
        self.assertIn(MASK, self.secrets.prose('API_TOKEN=opaque-request-canary'))
        self.assertEqual(self.secrets.safe('password = request.password'), 'password = request.password')

    async def test_revised_pending_task_keeps_distinct_before_snapshots(self):
        from capivara2ptn.change_patch import capture_before
        store = self.approved_store(['app.py'])
        task = store.state['tasks'][0]
        capture_before(store, task)
        old_copy = task['before_copies']['app.py']
        stage_patch(store, task, {'changes': [{'path': 'app.py', 'action': 'edit',
            'base_fingerprint': fingerprint(self.project, 'app.py'),
            'edits': [{'old': 'return phone[:8]', 'new': 'return phone'}]}]}, self.secrets)
        store.state['plan']['tasks'][0]['description'] += ' Revisão aprovada.'
        store.state['tasks'] = task_states(store.state['plan'], store.state['tasks'])
        current = store.state['tasks'][0]
        capture_before(store, current)
        self.assertNotEqual(old_copy, current['before_copies']['app.py'])
        self.assertEqual(store.path(old_copy).read_text(), SOURCE)
        self.assertIn('return phone', store.path(current['before_copies']['app.py']).read_text())

    async def test_file_changed_immediately_before_replace_is_not_overwritten(self):
        from capivara2ptn.change_patch import atomic_app
        import tempfile as temp_module
        before = fingerprint(self.project, 'app.py')
        original_mkstemp = temp_module.mkstemp
        external = SOURCE + '# edição concorrente\n'
        def changed_before_replace(*args, **kwargs):
            (self.project / 'app.py').write_text(external)
            return original_mkstemp(*args, **kwargs)
        with patch('capivara2ptn.change_patch.tempfile.mkstemp', side_effect=changed_before_replace):
            with self.assertRaises(ChangeConflict):
                atomic_app(self.project, 'app.py', b'different = True\n', 0o644, before)
        self.assertEqual((self.project / 'app.py').read_text(), external)
        self.assertFalse(list(self.project.glob('.capivara-change-*')))

    async def test_empty_existing_file_can_be_filled_but_empty_anchor_cannot_replace_nonempty_file(self):
        (self.project / 'empty.py').write_text('')
        store = self.approved_store(['empty.py', 'app.py'])
        task = store.state['tasks'][0]
        stage_patch(store, task, {'changes': [{'path': 'empty.py', 'action': 'edit',
            'base_fingerprint': fingerprint(self.project, 'empty.py'), 'edits': [{'old': '', 'new': 'value = True\n'}]}]}, self.secrets)
        self.assertEqual((self.project / 'empty.py').read_text(), 'value = True\n')
        with self.assertRaises(ChangeError):
            stage_patch(store, task, {'changes': [{'path': 'app.py', 'action': 'edit',
                'base_fingerprint': fingerprint(self.project, 'app.py'), 'edits': [{'old': '', 'new': 'new code'}]}]}, self.secrets)
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
