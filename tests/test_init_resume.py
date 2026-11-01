"""Retomada do INIT por operação, incluindo arquivos criados antes dos checkpoints."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capivara2ptn.init_flow import InitCancelled, InitError, Question, RunFiles, run_init
from capivara2ptn.plan_state import read_init_prd, record_init_completion
from capivara2ptn.prompt_files import install_defaults
from capivara2ptn.providers import ProviderError, Selection
from capivara2ptn.workspace import reset_stage

ORIGINAL = 'Aplicação de revisão; estado da API: em_revisao ou aprovada; rótulo visual Em revisão.'
QUESTION = json.dumps({'pergunta': 'Quem pode revisar?', 'alternativas': ['Somente o responsável', 'Todos']})
PRD = '# PRD\nAPI preserva `em_revisao` e `aprovada`. Rótulo: Em revisão.\n'
REJECTED = 'G0 REPROVADO\n- O valor contratual em_revisao foi alterado no PRD.'


class ScriptRunner:
    selection = Selection('codex', 'fixture', 'Default')
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
    async def invoke(self, instructions, data, **kwargs):
        self.calls.append((instructions, data))
        if not self.outputs:
            raise AssertionError('Chamada de modelo não esperada.')
        result = self.outputs.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
    def safe(self, text):
        return text


async def no_question(question):
    raise AssertionError('A entrevista concluída não deve ser repetida.')


class InitResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capivara-init-resume-')
        self.root = Path(self.temp.name)
        self.cwd = self.root / 'project'
        self.cwd.mkdir()
        self.config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.config.start()
        install_defaults()
        self.events = []
    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()
    def stage(self, name, description):
        self.events.append((name, description))
    def files(self):
        return RunFiles(self.cwd)
    def directory(self):
        return self.cwd / '.harness/dados'
    async def run_flow(self, writer, ask=no_question, auditor=None, gates=True, original=ORIGINAL):
        return await run_init(original, self.files(), writer, ask, self.stage, auditor=auditor, gates_enabled=gates)
    def legacy(self, *, response='CONCLUIDO', prd=PRD, g0=REJECTED, error='G0 REPROVADO', history=''):
        directory = self.directory()
        directory.mkdir(parents=True)
        values = {'prompt-original.txt': ORIGINAL, 'entrevista.txt': history, 'resposta.txt': response,
                  'prompt-final.md': prd, 'g0-init.txt': g0, 'erro-init.txt': error}
        for name, text in values.items():
            if text is not None:
                (directory / name).write_text(text)
        return directory

    async def test_fresh_init_keeps_interview_writer_gate_sequence(self):
        writer = ScriptRunner(QUESTION, 'CONCLUIDO', PRD, 'G0 APROVADO')
        async def answer(question):
            self.assertEqual(question.text, 'Quem pode revisar?')
            return 'Todos'
        path = await self.run_flow(writer, ask=answer)
        self.assertEqual(path.read_text(), PRD)
        self.assertEqual(len(writer.calls), 4)
        self.assertIn('Resposta: Todos', (self.directory() / 'entrevista.txt').read_text())
        state = json.loads((self.directory() / 'init-estado.json').read_text())
        self.assertEqual(state['phase'], 'done')
        self.assertEqual(state['questions_answered'], 1)
        record_init_completion(self.cwd, path, True)
        self.assertEqual(read_init_prd(self.cwd), PRD)

    async def test_cancelled_question_is_reopened_without_another_model_call(self):
        async def cancel(question):return None
        with self.assertRaises(InitCancelled):
            await self.run_flow(ScriptRunner(QUESTION), ask=cancel)
        asked = []
        async def answer(question):
            asked.append(question.text)
            return 'Todos'
        writer = ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO')
        await self.run_flow(writer, ask=answer)
        self.assertEqual(asked, ['Quem pode revisar?'])
        self.assertEqual(len(writer.calls), 3)
        self.assertEqual((self.directory() / 'entrevista.txt').read_text().count('Resposta: Todos'), 1)

    async def test_confirmed_answer_is_not_asked_again_after_provider_failure(self):
        async def answer(question):return 'Todos'
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner(QUESTION, ProviderError('offline')), ask=answer)
        before = (self.directory() / 'entrevista.txt').read_bytes()
        writer = ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO')
        await self.run_flow(writer)
        self.assertEqual((self.directory() / 'entrevista.txt').read_bytes(), before)
        self.assertIn('Resposta: Todos', writer.calls[0][1]['historico_entrevista'])

    async def test_failure_generating_prd_preserves_interview_and_partial_separately(self):
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner('CONCLUIDO', ProviderError('offline', 'PRD parcial')))
        self.assertFalse((self.directory() / 'prompt-final.md').exists())
        self.assertTrue(list((self.directory() / 'init/parciais').glob('*prompt-final.md')))
        writer = ScriptRunner(PRD, 'G0 APROVADO')
        await self.run_flow(writer)
        self.assertEqual(len(writer.calls), 2)
        self.assertNotIn('PRD parcial', writer.calls[0][1]['historico_entrevista'])

    async def test_gate_connection_failure_retries_only_auditor(self):
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner('CONCLUIDO', PRD), auditor=ScriptRunner(ProviderError('offline', 'G0 APROVADO')))
        self.assertFalse((self.directory() / 'g0-init.txt').exists())
        before = (self.directory() / 'prompt-final.md').read_bytes()
        writer, auditor = ScriptRunner(), ScriptRunner('G0 APROVADO')
        await self.run_flow(writer, auditor=auditor)
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(auditor.calls), 1)
        self.assertEqual(auditor.calls[0][1]['prd_final'], PRD)
        self.assertEqual((self.directory() / 'prompt-final.md').read_bytes(), before)

    async def test_rejected_gate_resumes_with_writer_feedback_and_new_gate(self):
        bad = '# PRD\nEstado em revisão.\n'
        files = self.files()
        with self.assertRaisesRegex(InitError, 'G0 REPROVADO'):
            await run_init(ORIGINAL, files, ScriptRunner('CONCLUIDO', bad, REJECTED), no_question, self.stage)
        files.write('erro-init.txt', REJECTED)
        history = (self.directory() / 'entrevista.txt').read_bytes()
        writer, auditor = ScriptRunner(PRD), ScriptRunner('G0 APROVADO')
        path = await self.run_flow(writer, auditor=auditor)
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(writer.calls[0][1]['prd_anterior'], bad)
        self.assertIn('em_revisao', writer.calls[0][1]['reprovacao_g0'])
        self.assertIn('grafia exata', writer.calls[0][0])
        self.assertEqual(auditor.calls[0][1]['prd_final'], PRD)
        self.assertEqual(history, (self.directory() / 'entrevista.txt').read_bytes())
        self.assertFalse((self.directory() / 'erro-init.txt').exists())
        archives = list((self.directory() / 'init/historico').glob('*prompt-final.md'))
        self.assertEqual(archives[0].read_text(), bad)
        self.assertEqual(path.read_text(), PRD)

    async def test_legacy_rejected_init_is_imported_without_repeating_interview(self):
        question = Question('Quem pode revisar?', ('Somente o responsável', 'Todos'))
        history = f'Pergunta: {question.transcript()}\nResposta: Todos\n\n'
        self.legacy(prd='Estado em revisão.', history=history)
        writer = ScriptRunner(PRD, 'G0 APROVADO')
        await self.run_flow(writer)
        self.assertEqual(len(writer.calls), 2)
        self.assertIn('reprovacao_g0', writer.calls[0][1])
        self.assertEqual((self.directory() / 'entrevista.txt').read_text(), history)

    async def test_legacy_answered_question_is_not_repeated(self):
        question = Question('Quem pode revisar?', ('Somente o responsável', 'Todos'))
        history = f'Pergunta: {question.transcript()}\nResposta: Todos\n\n'
        self.legacy(response=QUESTION, prd=None, g0=None, error='offline', history=history)
        writer = ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO')
        await self.run_flow(writer)
        self.assertEqual(writer.calls[0][1]['historico_entrevista'], history)
        self.assertEqual((self.directory() / 'entrevista.txt').read_text(), history)

    async def test_legacy_unanswered_question_is_restored(self):
        self.legacy(response=QUESTION, prd=None, g0=None, error='Cancelado')
        asked = []
        async def answer(question):
            asked.append(question.text)
            return 'Todos'
        writer = ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO')
        await self.run_flow(writer, ask=answer)
        self.assertEqual(asked, ['Quem pode revisar?'])
        self.assertEqual(len(writer.calls), 3)

    async def test_legacy_ambiguous_partial_prd_is_regenerated_without_interview(self):
        self.legacy(prd='Saída parcial', g0=None, error='Falha do provedor')
        writer = ScriptRunner(PRD, 'G0 APROVADO')
        await self.run_flow(writer)
        self.assertEqual(len(writer.calls), 2)
        self.assertNotIn('reprovacao_g0', writer.calls[0][1])
        self.assertEqual((self.directory() / 'prompt-final.md').read_text(), PRD)

    async def test_changed_original_and_history_are_refused_without_overwrite(self):
        async def cancel(question):return None
        with self.assertRaises(InitCancelled):
            await self.run_flow(ScriptRunner(QUESTION), ask=cancel)
        with self.assertRaisesRegex(InitError, 'pedido mudou'):
            await self.run_flow(ScriptRunner(), original='Pedido novo')
        history = self.directory() / 'entrevista.txt'
        history.write_text('Edição externa')
        before = {p.name: p.read_bytes() for p in self.directory().iterdir() if p.is_file()}
        with self.assertRaisesRegex(InitError, 'mudou fora'):
            await self.run_flow(ScriptRunner())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory().iterdir() if p.is_file()})

    async def test_source_symlink_or_corrupt_checkpoint_is_refused(self):
        files = self.files()
        files.begin(ORIGINAL)
        state = self.directory() / 'init-estado.json'
        state.write_text('{invalid')
        with self.assertRaisesRegex(InitError, 'Checkpoint.*inválido'):
            await self.run_flow(ScriptRunner())
        state.unlink()
        target = self.directory() / 'prompt-original.txt'
        target.unlink()
        external = self.root / 'external.txt'
        external.write_text(ORIGINAL)
        target.symlink_to(external)
        with self.assertRaisesRegex(InitError, 'links'):
            await self.run_flow(ScriptRunner())
        self.assertEqual(external.read_text(), ORIGINAL)

    async def test_answer_transaction_recovers_before_artifact_write(self):
        files = self.files()
        files.begin(ORIGINAL)
        files.write('resposta.txt', QUESTION)
        question = files.session.accept_interview_response(QUESTION)
        with patch.object(files.session, 'recover', side_effect=InitCancelled('Interrompido')):
            with self.assertRaises(InitCancelled):
                files.session.answer(question, 'Todos')
        await self.run_flow(ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO'))
        self.assertEqual((self.directory() / 'entrevista.txt').read_text().count('Resposta: Todos'), 1)
        state = json.loads((self.directory() / 'init-estado.json').read_text())
        self.assertEqual(state['questions_answered'], 1)
        self.assertNotIn('transaction', state)

    async def test_answer_transaction_recovers_after_artifact_write(self):
        files = self.files()
        files.begin(ORIGINAL)
        files.write('resposta.txt', QUESTION)
        question = files.session.accept_interview_response(QUESTION)
        original_save = files.session.save
        saves = 0
        def interrupted_save():
            nonlocal saves
            saves += 1
            if saves == 2:
                raise InitCancelled('Interrompido depois de salvar a resposta')
            original_save()
        with patch.object(files.session, 'save', side_effect=interrupted_save):
            with self.assertRaises(InitCancelled):
                files.session.answer(question, 'Todos')
        await self.run_flow(ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO'))
        self.assertEqual((self.directory() / 'entrevista.txt').read_text().count('Resposta: Todos'), 1)

    async def test_approved_gate_before_receipt_does_not_repeat_models(self):
        await self.run_flow(ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO'))
        writer = ScriptRunner()
        path = await self.run_flow(writer)
        self.assertEqual(writer.calls, [])
        record_init_completion(self.cwd, path, True)
        with self.assertRaisesRegex(InitError, 'já concluído'):
            await self.run_flow(ScriptRunner())

    async def test_gate_option_and_plan_restart_preserve_init_checkpoints(self):
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner('CONCLUIDO', PRD), auditor=ScriptRunner(ProviderError('offline')))
        writer = ScriptRunner()
        await self.run_flow(writer, gates=False)
        self.assertEqual(writer.calls, [])
        checkpoint = (self.directory() / 'init-estado.json').read_bytes()
        archive = self.directory() / 'init/historico/keep.txt'
        archive.parent.mkdir(parents=True)
        archive.write_text('histórico')
        (self.directory() / 'plano.md').write_text('plano')
        reset_stage(self.cwd, 'plan')
        self.assertEqual((self.directory() / 'init-estado.json').read_bytes(), checkpoint)
        self.assertEqual(archive.read_text(), 'histórico')
        self.assertFalse((self.directory() / 'plano.md').exists())

    async def test_crlf_is_preserved_across_resume(self):
        original = 'Pedido original\r\n' + ORIGINAL
        prd = PRD.replace('\n', '\r\n')
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner('CONCLUIDO', prd), auditor=ScriptRunner(ProviderError('offline')), original=original)
        await self.run_flow(ScriptRunner(), auditor=ScriptRunner('G0 APROVADO'), original=original)
        self.assertEqual((self.directory() / 'prompt-original.txt').read_bytes(), original.encode())
        self.assertEqual((self.directory() / 'prompt-final.md').read_bytes(), prd.encode())

    async def test_pending_checkpoint_cannot_be_consumed_by_plan_without_completion_receipt(self):
        from capivara2ptn.plan_state import PlanError
        with self.assertRaises(ProviderError):
            await self.run_flow(ScriptRunner('CONCLUIDO', PRD), auditor=ScriptRunner(ProviderError('offline')))
        self.assertFalse((self.directory() / 'erro-init.txt').exists())
        with self.assertRaises(PlanError):
            read_init_prd(self.cwd)
        path = await self.run_flow(ScriptRunner(), auditor=ScriptRunner('G0 APROVADO'))
        record_init_completion(self.cwd, path, True)
        self.assertEqual(read_init_prd(self.cwd), PRD)

    async def test_checkpoint_cannot_turn_a_rejected_gate_into_approval(self):
        with self.assertRaises(InitError):
            await self.run_flow(ScriptRunner('CONCLUIDO', PRD, REJECTED))
        checkpoint = self.directory() / 'init-estado.json'
        state = json.loads(checkpoint.read_text())
        state.update(phase='done', g0_status='approved')
        checkpoint.write_text(json.dumps(state))
        writer = ScriptRunner()
        with self.assertRaisesRegex(InitError, 'parecer G0 aprovado'):
            await self.run_flow(writer)
        self.assertEqual(writer.calls, [])
        self.assertFalse((self.directory() / 'init-concluido.json').exists())

    async def test_error_logging_does_not_replace_a_pending_confirmed_answer(self):
        files = self.files()
        files.begin(ORIGINAL)
        files.write('resposta.txt', QUESTION)
        question = files.session.accept_interview_response(QUESTION)
        original_atomic = files.session.atomic
        def unavailable_history(name, text):
            if name == 'entrevista.txt':
                raise InitError('Falha de escrita do histórico')
            original_atomic(name, text)
        with patch.object(files.session, 'atomic', side_effect=unavailable_history):
            with self.assertRaises(InitError):
                files.session.answer(question, 'Todos')
            with self.assertRaises(InitError):
                files.write('erro-init.txt', 'Falha de escrita')
        pending = json.loads((self.directory() / 'init-estado.json').read_text())['transaction']
        self.assertEqual(pending['name'], 'entrevista.txt')
        self.assertIn('Resposta: Todos', pending['text'])
        await self.run_flow(ScriptRunner('CONCLUIDO', PRD, 'G0 APROVADO'))
        self.assertEqual((self.directory() / 'entrevista.txt').read_text().count('Resposta: Todos'), 1)
