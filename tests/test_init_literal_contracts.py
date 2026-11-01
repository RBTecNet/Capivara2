"""Regressão real e opt-in da geração do PRD, sem dados de projetos do usuário.

CAPIVARA_CONTRACT_TEST_MODEL=<modelo> CAPIVARA_CONTRACT_TESTS=1 \
python -m unittest discover -s tests -p 'test_init_literal_contracts.py' -v

CAPIVARA_CONTRACT_TEST_PROMPT permite conferir uma proposta antes de instalá-la.
"""

import os
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path

from capivara2ptn.init_flow import g0_decision
from capivara2ptn.providers import CliRunner, Selection


@unittest.skipUnless(os.environ.get('CAPIVARA_CONTRACT_TESTS') == '1', 'Teste real de modelo requer execução opt-in.')
class InitLiteralContractsTests(unittest.IsolatedAsyncioTestCase):
    async def generate_and_audit(self, history: str, expected: list[str], case: str) -> None:
        model = os.environ.get('CAPIVARA_CONTRACT_TEST_MODEL')
        self.assertTrue(model, 'Informe CAPIVARA_CONTRACT_TEST_MODEL para usar a CLI já autenticada.')
        selection = Selection(os.environ.get('CAPIVARA_CONTRACT_TEST_PROVIDER', 'codex'), model,
                              os.environ.get('CAPIVARA_CONTRACT_TEST_EFFORT', 'Low'))
        proposal = os.environ.get('CAPIVARA_CONTRACT_TEST_PROMPT')
        prompt = Path(proposal).read_text() if proposal else files('capivara2ptn').joinpath('prompts/init-final.md').read_text()
        gate = files('capivara2ptn').joinpath('prompts/gates/g0-init.md').read_text()
        original = (
            'Especificar uma aplicação local para revisão de propostas, sem login. '
            'A API usa o endpoint /api/review-items. Cada proposta tem id, titulo, estado e responsavel_id. '
            'Os valores contratuais transmitidos e persistidos para estado são exatamente '
            '`em_revisao`, `aprovada` e `nao_aprovada`. Os respectivos rótulos visuais são '
            '“Em revisão”, “Aprovada” e “Não aprovada”. A interface permite criar e listar '
            'propostas e alterar o estado para qualquer valor permitido. O padrão é em_revisao. '
            'Preservar os contratos de dados; não inventar outros módulos ou permissões.'
        )
        with tempfile.TemporaryDirectory(prefix='capivara-literal-contract-') as temporary:
            runner = CliRunner(selection, Path(temporary), lambda text: None)
            inputs = {'pedido_original': original, 'historico_entrevista': history}
            prd = await runner.invoke(prompt, inputs)
            for value in expected:
                self.assertIn(value, prd, f'Valor contratual omitido ou normalizado: {value}')
            for name in ('responsavel_id', '/api/review-items'):
                self.assertIn(name, prd)
            verdict = await runner.invoke(gate, {**inputs, 'prd_final': prd})
            output = os.environ.get('CAPIVARA_CONTRACT_TEST_REPORT_DIR')
            if output:
                directory = Path(output)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f'{case}-prd.md').write_text(prd)
                (directory / f'{case}-g0.txt').write_text(verdict)
            approved, _ = g0_decision(verdict)
            self.assertTrue(approved, verdict)

    async def test_machine_values_remain_distinct_from_display_labels(self):
        await self.generate_and_audit(
            'Pergunta: Manter os contratos da API?\nResposta: Sim, manter os valores exatamente; os rótulos da tela podem conter acentos.\n',
            ['em_revisao', 'aprovada', 'nao_aprovada'], 'literais-preservados')

    async def test_explicit_user_decision_can_change_a_contract(self):
        await self.generate_and_audit(
            'Pergunta: Como tratar o estado de proposta não aprovada?\n'
            'Resposta: Quero substituir o valor contratual nao_aprovada por recusada na nova API e na persistência. '
            'Use o rótulo Recusada. Os valores em_revisao e aprovada, o endpoint e os nomes dos campos permanecem iguais.\n',
            ['em_revisao', 'aprovada', 'recusada'], 'decisao-explicita')

    async def test_rejected_init_resumes_with_real_writer_and_auditor(self):
        from unittest.mock import patch
        from capivara2ptn.init_flow import RunFiles, run_init
        from capivara2ptn.prompt_files import install_defaults
        model = os.environ.get('CAPIVARA_CONTRACT_TEST_MODEL')
        self.assertTrue(model)
        selection = Selection(os.environ.get('CAPIVARA_CONTRACT_TEST_PROVIDER', 'codex'), model,
                              os.environ.get('CAPIVARA_CONTRACT_TEST_EFFORT', 'Low'))
        original = ('Aplicação local sem login para revisar propostas. Permite criar, listar e alterar '
                    'o estado de propostas. A API /api/review-items usa os campos id, titulo e estado. '
                    'Os valores transmitidos e persistidos para estado são exatamente em_revisao, aprovada '
                    'e nao_aprovada. Os rótulos são Em revisão, Aprovada e Não aprovada. O padrão é em_revisao. '
                    'Preserve estes contratos; não crie funcionalidades adicionais.')
        with tempfile.TemporaryDirectory(prefix='capivara-resume-real-') as temporary:
            root = Path(temporary)
            cwd = root / 'project'
            directory = cwd / '.harness/dados'
            directory.mkdir(parents=True)
            history = 'Pergunta: Manter os valores da API?\nResposta: Sim, manter os valores exatos.\n\n'
            rejected = '# PRD\nPropostas com id, titulo e estado. Os estados são em revisão, aprovada e não aprovada.\n'
            artifacts = {'prompt-original.txt': original, 'entrevista.txt': history, 'resposta.txt': 'CONCLUIDO',
                         'prompt-final.md': rejected, 'g0-init.txt': 'G0 REPROVADO\n- Preserve os valores contratuais em_revisao e nao_aprovada; os rótulos visuais podem conter acentos.',
                         'erro-init.txt': 'G0 REPROVADO'}
            for name, text in artifacts.items():
                (directory / name).write_text(text)
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}):
                install_defaults()
                runner = CliRunner(selection, cwd, lambda text: None)
                async def forbidden_question(question):
                    self.fail('Retomada repetiu a entrevista já concluída.')
                result = await run_init(original, RunFiles(cwd), runner, forbidden_question, lambda state, text: None)
                prd = result.read_text()
                for literal in ('em_revisao', 'aprovada', 'nao_aprovada', '/api/review-items'):
                    self.assertIn(literal, prd)
                self.assertEqual((directory / 'g0-init.txt').read_text().strip(), 'G0 APROVADO')
                self.assertEqual((directory / 'entrevista.txt').read_text(), history)
                self.assertFalse((directory / 'erro-init.txt').exists())
