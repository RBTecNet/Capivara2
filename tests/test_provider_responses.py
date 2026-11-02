"""Reprodução do NDJSON observado no OpenCode 2.2.2 com Grok 4.7."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capivara2ptn.build_flow import gate_decision
from capivara2ptn.providers import CliRunner, ProviderError, Selection


def event(kind, message='final', **values):
    part = {'id': message + '-text', 'messageID': message, 'type': kind.replace('_', '-')}
    part.update(values)
    return {'type': kind, 'sessionID': 'fixture', 'part': part}


class ProviderResponseTests(unittest.IsolatedAsyncioTestCase):
    async def replay(self, events, access='read', provider='opencode'):
        with tempfile.TemporaryDirectory(prefix='capivara-provider-response-') as temporary:
            root = Path(temporary)
            fixture = root / 'events.jsonl'
            fixture.write_text(''.join(json.dumps(item) + '\n' for item in events))
            script = 'import pathlib,sys; sys.stdin.read(); print(pathlib.Path(sys.argv[1]).read_text(), end="")'
            logs = []
            runner = CliRunner(Selection(provider, 'fixture', 'Default'), root, logs.append)
            with patch('capivara2ptn.providers.command', return_value=([sys.executable, '-c', script, str(fixture)], dict(os.environ))):
                result = await runner.invoke('Instruções da operação.', {}, access=access)
            return result, logs

    async def test_grok_progress_before_tools_is_not_part_of_final_gate_response(self):
        result, logs = await self.replay([
            event('step_start', 'before'),
            event('text', 'before', text='Vou conferir somente o estado do repositório.'),
            event('tool_use', 'before', type='tool', tool='read'),
            event('step_finish', 'before', reason='tool-calls'),
            event('step_start', 'during'),
            event('text', 'during', text='Vou ler o plano e a definição da task 1.1.'),
            event('tool_use', 'during', type='tool', tool='read'),
            event('step_finish', 'during', reason='tool-calls'),
            event('step_start'),
            event('text', text='G0 APROVADO'),
            # A CLI observada encerrou sem step_finish final.
        ])
        self.assertEqual(result, 'G0 APROVADO')
        self.assertEqual(gate_decision(result, 'G0'), (True, ''))
        self.assertTrue(any('estado do repositório' in log for log in logs))
        self.assertTrue(any('definição da task' in log for log in logs))

    async def test_g2_uses_final_message_without_special_case_for_g0_or_model(self):
        result, _ = await self.replay([event('text', 'before', text='Vou conferir o diff.'),
                                      event('text', text='G2 APROVADO')])
        self.assertEqual(gate_decision(result, 'G2'), (True, ''))

    async def test_rejection_preserves_all_parts_and_objective_reason(self):
        result, _ = await self.replay([event('text', 'before', text='Avaliando...'),
            event('text', text='G0 REPROVADO', id='verdict'),
            event('text', text='- Contrato da API diverge do PRD.', id='reason'),
            event('step_finish', reason='stop')])
        self.assertEqual(gate_decision(result, 'G0'), (False, '- Contrato da API diverge do PRD.'))

    async def test_updated_part_is_replaced_without_repeating_content(self):
        result, _ = await self.replay([event('text', text='G0 APRO'), event('text', text='G0 APROVADO')])
        self.assertEqual(result, 'G0 APROVADO')

    async def test_progress_in_same_final_message_is_still_invalid(self):
        result, _ = await self.replay([event('text', text='Vou analisar.', id='progress'),
                                      event('text', text='G0 APROVADO', id='verdict')])
        with self.assertRaises(ProviderError):gate_decision(result, 'G0')

    async def test_ambiguous_final_verdict_is_not_accepted(self):
        result, _ = await self.replay([event('text', 'before', text='Comentário'),
                                      event('text', text='G0 APROVADO\nG0 REPROVADO\nHá um bloqueio.')])
        with self.assertRaises(ProviderError):gate_decision(result, 'G0')

    async def test_later_message_without_text_does_not_reuse_old_approval(self):
        with self.assertRaises(ProviderError):
            await self.replay([event('step_start', 'before'), event('text', 'before', text='G0 APROVADO'),
                               event('step_finish', 'before', reason='tool-calls'), event('step_start')])

    async def test_error_after_text_never_becomes_approval(self):
        with self.assertRaises(ProviderError):
            await self.replay([event('text', text='G0 APROVADO'), {'type': 'error', 'error': 'Falha do provedor'}])

    async def test_text_mode_continues_refusing_tools(self):
        with self.assertRaises(ProviderError):
            await self.replay([event('tool_use', type='tool', tool='read')], access='text')

    async def test_legacy_steps_without_message_id_still_separate_turns(self):
        values = [event('step_start', 'before'), event('text', 'before', text='Vou ler.'),
                  event('step_start'), event('text', text='G0 APROVADO')]
        for item in values:item['part'].pop('messageID')
        result, _ = await self.replay(values)
        self.assertEqual(result, 'G0 APROVADO')

    async def test_codex_final_message_selection_is_unchanged(self):
        result, _ = await self.replay([
            {'type':'item.completed','item':{'type':'agent_message','text':'Conferindo...'}},
            {'type':'item.completed','item':{'type':'agent_message','text':'G0 APROVADO'}},
        ], provider='codex')
        self.assertEqual(result, 'G0 APROVADO')

    async def test_claude_final_message_selection_is_unchanged(self):
        result, _ = await self.replay([
            {'type':'assistant','message':{'content':[{'type':'text','text':'Conferindo...'}]}},
            {'type':'assistant','message':{'content':[{'type':'text','text':'G0 APROVADO'}]}},
            {'type':'result','result':'G0 APROVADO'},
        ], provider='claude')
        self.assertEqual(result, 'G0 APROVADO')

    async def test_agy_final_message_selection_is_unchanged(self):
        result, _ = await self.replay([
            {'event':'step_update','step_update':{'step_index':0,'step_type':'agent_response','text_delta':'Conferindo...'}},
            {'event':'result','result':{'status':'SUCCESS','response':'G0 APROVADO'}},
        ], provider='agy')
        self.assertEqual(result, 'G0 APROVADO')
