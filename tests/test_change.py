"""Aceite do CHANGE em projetos temporários, sem modificar módulos anteriores."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from capivara2ptn.change_flow import ChangeRun
from capivara2ptn.change_patch import stage_patch, recover_patch
from capivara2ptn.change_protocol import render_plan, task_states, validate_plan
from capivara2ptn.change_source import ChangeError, ChangeConflict, Secrets, app_path, fingerprint, sha
from capivara2ptn.change_state import ChangeStore
from capivara2ptn.change_verify import verify_change
from capivara2ptn.project_config import ExecutionOptions
from capivara2ptn.providers import ProviderError, Selection

REQUEST = 'Corrigir telefone: preservar todos os dígitos e remover espaços, mantendo o padrão existente.'
SOURCE = 'def normalize(phone):\n    return phone[:8]\n'
RESULT = {'status': 'sucesso', 'comandos': [{'comando': 'verificação', 'codigo_retorno': 0, 'saida': 'OK'}], 'motivo': None}


def plan():
    return {'kind': 'plan', 'objective': 'Corrigir normalização de telefone', 'current_behavior': 'Trunca telefone.',
            'desired_behavior': 'Preservar dígitos e remover espaços.', 'preserve': ['Assinatura normalize(phone).'], 'limitations': [],
            'tasks': [{'id': '1', 'title': 'Corrigir normalização', 'description': 'Alterar somente a normalização de telefone.',
                       'files': ['app.py'], 'dependencies': [], 'inspection_only': False}],
            'verification': {'commands': [{'directory': '.', 'argv': ['python3', '-c', "import app; assert app.normalize('11 987654321') == '11987654321'"],
                                           'purpose': 'Confirmar correção do telefone e importação do componente.'}], 'limitation': None}}


class FakeChangeRunner:
    selection = Selection('codex', 'fixture', 'Default')
    def __init__(self, *, question=False, reject_g0=False, reject_g2=False, wrong_first=False, stop_writer=False):
        self.calls = []
        self.read = False
        self.question = question
        self.reject_g0, self.reject_g2 = reject_g0, reject_g2
        self.wrong_first, self.stop_writer = wrong_first, stop_writer
        self.writes = 0
    async def invoke(self, instructions, data, **kwargs):
        self.calls.append((instructions, data, kwargs))
        assert kwargs.get('access') == 'text'
        if 'CHANGE — investigação' in instructions:
            if not self.read:
                self.read = True
                return json.dumps({'kind': 'read', 'files': [{'path': 'app.py', 'start': 1}]})
            return json.dumps({'kind': 'investigation', 'request_type': 'correcao', 'summary': 'Normalização trunca o telefone.',
                'current_behavior': 'Retorna só oito caracteres.', 'desired_behavior': REQUEST,
                'patterns': ['Função Python pura, sem dependências externas.'], 'files': ['app.py'],
                'evidence': [{'path': 'app.py', 'line': 2, 'description': 'Fatiamento de oito caracteres.'}], 'limitations': []})
        if 'CHANGE — entrevista' in instructions:
            if self.question and not data['history']:
                return json.dumps({'kind': 'question', 'text': 'Remover somente espaços?', 'choices': ['Sim', 'Preservar espaços']})
            return json.dumps({'kind': 'done'})
        if 'CHANGE — plano' in instructions:
            return json.dumps(plan())
        if data.get('gate') == 'G0':
            if self.reject_g0:
                self.reject_g0 = False
                return json.dumps({'kind': 'review', 'approved': False, 'issues': ['Esclareça o uso dos padrões existentes.']})
            return json.dumps({'kind': 'review', 'approved': True, 'issues': []})
        if data.get('gate') == 'G2':
            if self.reject_g2:
                self.reject_g2 = False
                return json.dumps({'kind': 'review', 'approved': False, 'issues': ['Ainda falta remover espaços.'], 'repair_task': '1'})
            return json.dumps({'kind': 'review', 'approved': True, 'issues': [], 'repair_task': '1'})
        if data.get('operation') == 'preparation':
            return json.dumps({'kind': 'preparation', 'summary': 'Manter assinatura e o padrão de função pura existente.'})
        if data.get('operation') == 'writer':
            if self.stop_writer:
                raise asyncio.CancelledError
            self.writes += 1
            item = next(c for c in reversed(data['context']) if c.get('path') == 'app.py')
            old = 'return phone[:8]' if 'return phone[:8]' in item['text'] else 'return phone'
            new = 'return phone' if self.wrong_first and self.writes == 1 else 'return phone.replace(" ", "")'
            return json.dumps({'kind': 'patch', 'summary': 'Correção localizada do telefone.', 'changes': [
                {'path': 'app.py', 'action': 'edit', 'base_fingerprint': item['fingerprint'], 'edits': [{'old': old, 'new': new}]}]})
        raise AssertionError('Chamada inesperada.')


async def approve(markdown, recovery):return {'action': 'approve'}
async def no_question(question):raise AssertionError('Entrevista desnecessária.')
async def decline_scope(request):return 'decline'
async def stop_conflict(paths):return False


class ChangeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capivara-change-')
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'app.py').write_text(SOURCE)
        (self.project / 'unrelated.txt').write_text('modificação do usuário')
        self.config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.config.start()
        self.secrets = Secrets(self.project)
    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()
    def store(self):
        store = ChangeStore(self.project)
        store.create(REQUEST)
        return store
    async def run_flow(self, store=None, runner=None, **kwargs):
        store = store or self.store()
        runner = runner or FakeChangeRunner()
        engine = ChangeRun(store, runner, kwargs.pop('auditor', runner), kwargs.pop('options', ExecutionOptions()), self.secrets,
                           kwargs.pop('update', lambda state, message: None), kwargs.pop('ask', no_question), kwargs.pop('approve', approve),
                           kwargs.pop('scope', decline_scope), kwargs.pop('conflict', stop_conflict))
        with patch('capivara2ptn.change_flow.verify_change', new=kwargs.pop('verifier', AsyncMock(return_value=RESULT))):
            path = await engine.run()
        return path, store, runner

    async def test_full_change_requires_no_previous_harness_modules(self):
        before = (self.project / 'unrelated.txt').read_bytes()
        approvals = []
        async def review(markdown, recovery):
            self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
            self.assertFalse((self.project / '.harness/dados').exists())
            self.assertIn('app.py', markdown)
            approvals.append(markdown)
            return {'action': 'approve'}
        path, store, runner = await self.run_flow(approve=review)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(len(approvals), 1)
        self.assertIn('phone.replace', (self.project / 'app.py').read_text())
        self.assertEqual((self.project / 'unrelated.txt').read_bytes(), before)
        self.assertTrue(path.exists())
        self.assertEqual(store.state['tasks'][0]['g1'], 'done')
        self.assertEqual(store.state['tasks'][0]['changes'], [{'arquivo': 'app.py', 'tipo': 'modificado'}])
        self.assertTrue(all(kwargs['access'] == 'text' for _, _, kwargs in runner.calls))

    async def test_plan_pause_and_resume_do_not_repeat_investigation(self):
        async def pause(markdown, recovery):return {'action': 'pause'}
        path, store, runner = await self.run_flow(approve=pause)
        self.assertIsNone(path)
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
        self.assertIsNone(store.state['approval'])
        loaded = ChangeStore(self.project)
        loaded.select(store.identifier)
        _, loaded, resumed = await self.run_flow(store=loaded)
        self.assertEqual(loaded.state['status'], 'done')
        self.assertFalse(any('investigação' in instructions or 'entrevista' in instructions or 'plano localizado' in instructions for instructions, _, _ in resumed.calls))

    async def test_cancel_plan_never_writes_application(self):
        async def cancel(markdown, recovery):return {'action': 'cancel'}
        _, store, runner = await self.run_flow(approve=cancel)
        self.assertEqual(store.state['status'], 'cancelled')
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
        self.assertFalse(any(data.get('operation') == 'writer' for _, data, _ in runner.calls))

    async def test_pending_interview_resumes_without_reasking_model(self):
        async def interrupt(question):return None
        _, store, _ = await self.run_flow(runner=FakeChangeRunner(question=True), ask=interrupt)
        self.assertEqual(store.state['phase'], 'interview')
        self.assertEqual(store.state['question']['text'], 'Remover somente espaços?')
        answers = []
        async def answer(question):
            answers.append(question['text'])
            return 'Sim'
        _, store, runner = await self.run_flow(store=store, ask=answer)
        self.assertEqual(answers, ['Remover somente espaços?'])
        self.assertEqual(len(store.state['history']), 1)
        self.assertEqual(store.state['status'], 'done')
        self.assertFalse(runner.calls[0][0].startswith('# CHANGE — investigação'))

    async def test_g0_and_g2_feedback_reuses_one_approval(self):
        approvals = []
        async def review(markdown, recovery):
            approvals.append(markdown)
            return {'action': 'approve'}
        _, store, runner = await self.run_flow(runner=FakeChangeRunner(reject_g0=True, reject_g2=True, wrong_first=True), approve=review)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(runner.writes, 2)
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(any('G2 REPROVADO' in data.get('feedback', '') for _, data, _ in runner.calls))
        self.assertTrue(any(data.get('operation') == 'preparation' for _, data, _ in runner.calls))

    async def test_actual_verification_failure_returns_output_to_writer(self):
        failed = {'status': 'falha', 'comandos': [{'codigo_retorno': 1, 'saida': 'AssertionError telefone truncado', 'comando': 'python3'}]}
        verifier = AsyncMock(side_effect=[failed, RESULT])
        _, store, runner = await self.run_flow(runner=FakeChangeRunner(wrong_first=True), verifier=verifier)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(verifier.await_count, 2)
        self.assertEqual(runner.writes, 2)
        self.assertTrue(any(data.get('verification', {}).get('status') == 'falha' for _, data, _ in runner.calls if isinstance(data.get('verification'), dict)))

    async def test_writer_interruption_preserves_plan_and_request(self):
        store = self.store()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(store=store, runner=FakeChangeRunner(stop_writer=True))
        self.assertEqual(store.state['status'], 'interrupted')
        self.assertTrue(store.state['approval'])
        loaded = ChangeStore(self.project)
        loaded.select(store.identifier)
        _, loaded, runner = await self.run_flow(store=loaded)
        self.assertEqual(loaded.state['status'], 'done')
        self.assertFalse(any('investigação' in instructions for instructions, _, _ in runner.calls))

    async def test_external_change_blocks_resume_without_overwriting(self):
        store = self.store()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(store=store, runner=FakeChangeRunner(stop_writer=True))
        external = SOURCE + '\n# Alteração do usuário\n'
        (self.project / 'app.py').write_text(external)
        _, store, runner = await self.run_flow(store=store)
        self.assertEqual(store.state['status'], 'conflict')
        self.assertEqual((self.project / 'app.py').read_text(), external)
        self.assertEqual(runner.calls, [])

    async def test_context_conflict_during_model_call_is_not_silently_rebased(self):
        outer = self
        class Changing(FakeChangeRunner):
            async def invoke(self, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'CHANGE — entrevista' in instructions:
                    (outer.project / 'app.py').write_text(SOURCE + '# externo\n')
                return result
        _, store, _ = await self.run_flow(runner=Changing())
        self.assertEqual(store.state['status'], 'conflict')
        self.assertIsNone(store.state['approval'])

    async def test_multiple_requests_and_project_lock(self):
        first = self.store()
        second = self.store()
        self.assertEqual(first.identifier, 'change-001')
        self.assertEqual(second.identifier, 'change-002')
        self.assertEqual(len(first.requests()), 2)
        with first.execution():
            with self.assertRaisesRegex(ChangeError, 'Já existe'):
                with second.execution():pass
        with second.execution():pass
        self.assertEqual(first.read_json('estado.json')['id'], 'change-001')

    async def test_gate_off_keeps_file_evidence_and_real_verification(self):
        _, store, runner = await self.run_flow(options=ExecutionOptions(gates_enabled=False), auditor=None)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(store.state['tasks'][0]['g1'], 'done')
        self.assertFalse(any(data.get('gate') for _, data, _ in runner.calls))

    async def test_unavailable_safe_verification_is_explicit_limitation(self):
        result = {'status': 'indisponivel', 'comandos': [], 'motivo': 'Sem ambiente isolado disponível.'}
        path, store, _ = await self.run_flow(verifier=AsyncMock(return_value=result))
        self.assertEqual(store.state['status'], 'done_with_limits')
        self.assertIn('Sem ambiente isolado', path.read_text())
        self.assertNotIn('verificação técnica aprovada', path.read_text())

    async def test_missing_provider_output_retries_same_selection(self):
        class OfflineOnce(FakeChangeRunner):
            async def invoke(self, instructions, data, **kwargs):
                if not getattr(self, 'failed_once', False):
                    self.failed_once = True
                    raise ProviderError('Falha temporária')
                return await super().invoke(instructions, data, **kwargs)
        with patch('capivara2ptn.change_flow.asyncio.sleep', new=AsyncMock()):
            _, store, runner = await self.run_flow(runner=OfflineOnce())
        self.assertEqual(store.state['selection']['model'], 'fixture')
        self.assertEqual(store.state['status'], 'done')

    async def test_safe_verification_uses_copy_and_cannot_see_original_project(self):
        _, store, _ = await self.run_flow()
        (self.project / 'data').mkdir()
        (self.project / 'data/real.sqlite').write_bytes(b'dados reais preservados')
        code = f"import pathlib,socket; assert not pathlib.Path({str(self.project)!r}).exists(); assert pathlib.Path('data').is_dir(); assert not pathlib.Path('data/real.sqlite').exists(); pathlib.Path('sandbox-marker').write_text('ok'); import app; assert app.normalize('11 987654321') == '11987654321'"
        store.state['plan']['verification']['commands'] = [{'directory': '.', 'argv': ['python3', '-c', code], 'purpose': 'Verificação isolada'}]
        result = await verify_change(store, lambda message: None, self.secrets)
        self.assertIn(result['status'], {'sucesso', 'indisponivel'})
        self.assertFalse((self.project / 'sandbox-marker').exists())
        self.assertEqual((self.project / 'data/real.sqlite').read_bytes(), b'dados reais preservados')
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'modificação do usuário')

    async def test_sensitive_files_and_path_escapes_are_refused(self):
        for path in ('.harness/state.json', '../outside.py', '/tmp/outside.py', 'src/../app.py', 'src//app.py', '.env', 'node_modules/a.js'):
            with self.assertRaises(ChangeError):app_path(self.project, path, writable=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (self.project / 'link').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ChangeError):app_path(self.project, 'link/file.py', writable=True)

    async def test_secret_values_do_not_enter_prompts_logs_or_documents(self):
        (self.project / '.env').write_text('API_TOKEN=secret-canary-change-93\n')
        (self.project / 'app.py').write_text(SOURCE + '\nAPI_TOKEN="secret-canary-change-93"\n')
        self.secrets = Secrets(self.project)
        _, store, runner = await self.run_flow()
        self.assertNotIn('secret-canary-change-93', json.dumps(runner.calls))
        for path in store.path('').rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.md', '.log'}:
                self.assertNotIn('secret-canary-change-93', path.read_text())
        self.assertIn('secret-canary-change-93', (self.project / 'app.py').read_text())

    async def test_approved_plan_tampering_refuses_writes(self):
        store = self.store()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(store=store, runner=FakeChangeRunner(stop_writer=True))
        store.path('plano.md').write_text('Plano adulterado')
        with self.assertRaises(ChangeError):
            await self.run_flow(store=store)
        self.assertEqual((self.project / 'app.py').read_text(), SOURCE)


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

    async def test_scope_expansion_requires_explicit_additional_approval(self):
        outer = self
        class Expanding(FakeChangeRunner):
            async def invoke(self, instructions, data, **kwargs):
                if data.get('operation') == 'writer' and 'new.py' not in data['task']['files']:
                    self.calls.append((instructions, data, kwargs))
                    return json.dumps({'kind': 'scope', 'reason': 'Precisa acrescentar helper relacionado.', 'files': ['new.py']})
                text = await super().invoke(instructions, data, **kwargs)
                if data.get('operation') == 'writer' and 'new.py' in data['task']['files']:
                    value = json.loads(text)
                    value['changes'].append({'path': 'new.py', 'action': 'create', 'content': 'helper = True\n'})
                    return json.dumps(value)
                return text
        requests = []
        async def scope(request):
            self.assertFalse((self.project / 'new.py').exists())
            self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
            requests.append(request)
            return 'approve'
        _, store, _ = await self.run_flow(runner=Expanding(), scope=scope)
        self.assertEqual(len(requests), 1)
        self.assertTrue((self.project / 'new.py').exists())
        self.assertEqual(store.state['status'], 'done')
        store.check_approval()

    async def test_request_can_be_registered_while_another_holds_execution_lock(self):
        first = self.store()
        with first.execution():
            second = self.store()
            self.assertNotEqual(first.identifier, second.identifier)
            with self.assertRaises(ChangeError):
                with second.execution():pass

    async def test_conflict_refresh_reinvestigates_and_reapproves_only_after_user_choice(self):
        store = self.store()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(store=store, runner=FakeChangeRunner(stop_writer=True))
        external = SOURCE + '# modificação externa preservada\n'
        (self.project / 'app.py').write_text(external)
        async def refresh(paths):
            self.assertEqual(paths, ['app.py'])
            return True
        approvals = []
        async def approve_again(markdown, recovery):
            approvals.append(markdown)
            return {'action': 'approve'}
        _, store, _ = await self.run_flow(store=store, conflict=refresh, approve=approve_again)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(len(approvals), 1)
        self.assertIn('# modificação externa preservada', (self.project / 'app.py').read_text())

    async def test_approved_tasks_cannot_be_replaced_by_editing_only_state(self):
        store = self.approved_store(['app.py'])
        store.state['tasks'][0]['files'].append('unrelated.txt')
        with self.assertRaises(ChangeError):
            stage_patch(store, store.state['tasks'][0], {'changes': []}, self.secrets)
        self.assertEqual((self.project / 'unrelated.txt').read_text(), 'modificação do usuário')


    async def test_finished_valid_task_is_not_reexecuted_after_later_interruption(self):
        class TwoTasks(FakeChangeRunner):
            def __init__(self, stop_second=False):
                super().__init__();self.stop_second = stop_second
            async def invoke(self, instructions, data, **kwargs):
                if 'CHANGE — plano' in instructions:
                    self.calls.append((instructions, data, kwargs))
                    value = plan()
                    value['tasks'].append({'id': '2', 'title': 'Acrescentar helper', 'description': 'Criar helper simples relacionado.',
                                           'files': ['helper.py'], 'dependencies': ['1'], 'inspection_only': False})
                    return json.dumps(value)
                if data.get('operation') == 'writer' and data['task']['id'] == '2':
                    self.calls.append((instructions, data, kwargs))
                    if self.stop_second:raise asyncio.CancelledError
                    return json.dumps({'kind': 'patch', 'summary': 'Helper criado.', 'changes': [{'path': 'helper.py', 'action': 'create', 'content': 'helper = True\n'}]})
                return await super().invoke(instructions, data, **kwargs)
        store = self.store()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(store=store, runner=TwoTasks(stop_second=True))
        self.assertEqual(store.state['tasks'][0]['status'], 'done')
        preserved = (self.project / 'app.py').read_bytes()
        _, store, runner = await self.run_flow(store=store, runner=TwoTasks())
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual((self.project / 'app.py').read_bytes(), preserved)
        self.assertTrue((self.project / 'helper.py').exists())
        self.assertFalse(any(data.get('task', {}).get('id') == '1' for _, data, _ in runner.calls))

    async def test_requested_plan_adjustment_keeps_code_untouched_until_approval(self):
        reviews = 0
        async def review(markdown, recovery):
            nonlocal reviews
            reviews += 1
            self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
            if reviews == 1:return {'action': 'adjust', 'text': 'Ressaltar preservação da assinatura existente.'}
            return {'action': 'approve'}
        _, store, runner = await self.run_flow(approve=review)
        self.assertEqual(reviews, 2)
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(len(store.state['plan_adjustments']), 1)
        self.assertEqual(sum(e['kind'] == 'approved' for e in store.state['events']), 1)

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

    async def test_safe_verification_has_no_host_credentials_or_network(self):
        _, store, _ = await self.run_flow()
        code = "import os,socket; assert 'CAPIVARA_HOST_CANARY' not in os.environ; s=socket.socket(); s.settimeout(.2)\ntry:\n s.connect(('1.1.1.1',53)); raise AssertionError('rede liberada')\nexcept OSError:\n print('isolated')"
        store.state['plan']['verification']['commands'] = [{'directory': '.', 'argv': ['python3', '-c', code], 'purpose': 'Confirmar isolamento.'}]
        with patch.dict(os.environ, {'CAPIVARA_HOST_CANARY': 'credencial-host'}):
            result = await verify_change(store, lambda message: None, self.secrets)
        self.assertIn(result['status'], {'sucesso', 'indisponivel'})
        if result['status'] == 'sucesso':
            self.assertIn('isolated', result['comandos'][0]['saida'])
            self.assertNotIn('credencial-host', result['comandos'][0]['saida'])


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


    async def test_verification_repair_inside_approved_plan_does_not_ask_scope_approval(self):
        class TwoTasks(FakeChangeRunner):
            def __init__(self):
                super().__init__(wrong_first=True)
                self.helper_writes = 0
            async def invoke(self, instructions, data, **kwargs):
                if 'CHANGE — plano' in instructions:
                    self.calls.append((instructions, data, kwargs))
                    value = plan()
                    value['tasks'].append({'id': '2', 'title': 'Criar helper', 'description': 'Criar helper relacionado.',
                                           'files': ['helper.py'], 'dependencies': ['1'], 'inspection_only': False})
                    return json.dumps(value)
                if data.get('operation') == 'writer' and data['task']['id'] == '2':
                    self.calls.append((instructions, data, kwargs))
                    if (data.get('verification') or {}).get('status') == 'falha':
                        return json.dumps({'kind': 'scope', 'reason': 'Corrigir telefone na task que o implementou.', 'files': ['app.py']})
                    self.helper_writes += 1
                    return json.dumps({'kind': 'patch', 'summary': 'Helper criado.', 'changes': [{'path': 'helper.py', 'action': 'create', 'content': 'helper = True\n'}]})
                return await super().invoke(instructions, data, **kwargs)
        async def forbidden_scope(request):self.fail('Arquivos já aprovados não exigem ampliar escopo.')
        failed = {'status': 'falha', 'comandos': [{'codigo_retorno': 1, 'saida': 'Telefone mantém espaços.', 'comando': 'python3'}]}
        runner = TwoTasks()
        _, store, _ = await self.run_flow(runner=runner, scope=forbidden_scope,
            options=ExecutionOptions(gates_enabled=False), auditor=None, verifier=AsyncMock(side_effect=[failed, RESULT]))
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(runner.helper_writes, 1)
        self.assertEqual(sum(e['kind'] == 'approved' for e in store.state['events']), 1)


    async def test_g2_receives_actual_before_after_content_not_only_writer_report(self):
        _, store, runner = await self.run_flow()
        review = next(data for _, data, _ in runner.calls if data.get('gate') == 'G2')
        self.assertIn('-    return phone[:8]', review['actual_diff']['text'])
        self.assertIn('+    return phone.replace', review['actual_diff']['text'])
        self.assertTrue(store.state['tasks'][0]['before_copies'])


    async def test_functional_expansion_requires_revised_plan_approval(self):
        class Functional(FakeChangeRunner):
            async def invoke(self, instructions, data, **kwargs):
                if 'CHANGE — plano' in instructions and data.get('scope_proposal'):
                    self.calls.append((instructions, data, kwargs))
                    value = plan()
                    value['tasks'][0]['description'] += ' Ampliação proposta: explicar a normalização na docstring.'
                    return json.dumps(value)
                if data.get('operation') == 'writer' and not getattr(self, 'proposed', False):
                    self.proposed = True
                    return json.dumps({'kind': 'scope', 'scope_type': 'functional', 'reason': 'Adicionar documentação do novo comportamento.', 'files': []})
                return await super().invoke(instructions, data, **kwargs)
        reviews = []
        async def review(markdown, recovery):
            reviews.append(markdown)
            self.assertEqual((self.project / 'app.py').read_text(), SOURCE)
            return {'action': 'approve'}
        async def no_extra_dialog(request):self.fail('A ampliação funcional deve ser aprovada no plano revisado.')
        _, store, _ = await self.run_flow(runner=Functional(), approve=review, scope=no_extra_dialog)
        self.assertEqual(len(reviews), 2)
        self.assertIn('Ampliação proposta', reviews[-1])
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(any('Ampliação funcional' in h['question'] for h in store.state['history']))


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


    async def test_transient_apply_failure_recovers_original_patch_without_reinvoking_writer(self):
        from capivara2ptn.change_patch import atomic_app
        class WithHelper(FakeChangeRunner):
            async def invoke(self, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'CHANGE — plano' in instructions:
                    value = json.loads(result)
                    value['tasks'][0]['files'].append('helper.py')
                    return json.dumps(value)
                if data.get('operation') == 'writer':
                    value = json.loads(result)
                    value['changes'].append({'path': 'helper.py', 'action': 'create', 'content': 'helper = True\n'})
                    return json.dumps(value)
                return result
        failed = False
        def transient(root, path, *args):
            nonlocal failed
            if path == 'helper.py' and not failed:
                failed = True
                raise OSError('Escrita temporariamente indisponível')
            return atomic_app(root, path, *args)
        with patch('capivara2ptn.change_patch.atomic_app', side_effect=transient):
            _, store, runner = await self.run_flow(runner=WithHelper())
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(runner.writes, 1)
        self.assertTrue((self.project / 'helper.py').exists())
        self.assertNotIn('pending_apply', store.state)

    async def test_failed_conclusion_write_resumes_without_repeating_completed_tasks(self):
        store = self.store()
        write = store.write
        failed = False
        def fail_conclusion(name, text):
            nonlocal failed
            if name == 'conclusao.md' and not failed:
                failed = True
                raise OSError('Falha ao salvar conclusão')
            return write(name, text)
        with patch.object(store, 'write', side_effect=fail_conclusion):
            with self.assertRaises(OSError):await self.run_flow(store=store)
        self.assertEqual(store.state['phase'], 'verification')
        self.assertTrue(all(t['status'] == 'done' for t in store.state['tasks']))
        _, store, runner = await self.run_flow(store=store)
        self.assertEqual(runner.calls, [])
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(store.path('conclusao.md').exists())


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
