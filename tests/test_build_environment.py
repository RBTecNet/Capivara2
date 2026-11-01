"""Resultados do executor chegam ao G0 sem confundir host com o sandbox da CLI."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capivara2ptn.build_environment import dependency_context, environment_context
from capivara2ptn.build_flow import BuildStore, run_build
from capivara2ptn.project_config import ExecutionOptions
from capivara2ptn.providers import Selection


def preparation(result='sucesso'):
    return {'id': '1.1', 'title': 'Preparar ambiente de desenvolvimento', 'description': 'Verificar ferramentas.',
            'dependencies': [], 'writer_report': 'Manifests preparados; executor verificará dependências.',
            'environment_check': {'verificacoes': [{'diretorio': '.', 'comando': 'runtime --version'}],
                                  'instalacoes': [], 'nao_aplicavel': None},
            'environment_result': {'status': result, 'comandos': [{'diretorio': '.', 'comando': 'runtime --version',
                                  'codigo_retorno': 0 if result == 'sucesso' else 1, 'saida': 'Runtime 1.0',
                                  'arquivo_saida': '.harness/dados/build/environment.log'}], 'motivo': None},
            'operation': 'done'}


class CapturedGate(BaseException):
    pass


class BuildEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    def test_dependency_keeps_actual_results_and_provenance(self):
        task = preparation()
        data = dependency_context(task)
        self.assertEqual(data['resultado_ambiente'], task['environment_result'])
        self.assertEqual(data['resultado_ambiente']['comandos'][0]['codigo_retorno'], 0)
        self.assertEqual(data['operacao_executor'], 'done')
        self.assertIn('fora do processo isolado', data['origem_resultado_ambiente'])
        data['resultado_ambiente']['comandos'][0]['codigo_retorno'] = 9
        self.assertEqual(task['environment_result']['comandos'][0]['codigo_retorno'], 0)

    def test_environment_is_available_through_indirect_dependencies(self):
        prep = preparation()
        intermediate = {'id': '1.2', 'title': 'Banco', 'description': 'Criar banco', 'dependencies': ['1.1'], 'operation': 'done'}
        context = environment_context({'1.1': prep, '1.2': intermediate})
        self.assertEqual(len(context['preparacoes_concluidas']), 1)
        self.assertEqual(context['preparacoes_concluidas'][0]['id'], '1.1')
        self.assertIn('próprio isolamento', context['escopo_da_cli'])
        self.assertNotIn('preparacao_ambiente', context)
        self.assertEqual(dependency_context(intermediate)['id'], '1.2')

    def test_failed_and_not_applicable_results_are_not_promoted_to_success(self):
        failed = preparation('falha')
        context = environment_context({'1.1': failed})
        self.assertEqual(context['preparacoes_concluidas'][0]['resultado_ambiente']['status'], 'falha')
        self.assertEqual(context['preparacoes_concluidas'][0]['resultado_ambiente']['comandos'][0]['codigo_retorno'], 1)
        failed['environment_result'] = {'status': 'nao_aplicavel', 'comandos': [], 'motivo': 'Somente arquivos estáticos.'}
        context = environment_context({'1.1': failed})
        self.assertEqual(context['preparacoes_concluidas'][0]['resultado_ambiente']['motivo'], 'Somente arquivos estáticos.')
        self.assertIsNone(environment_context({}))

    async def test_build_passes_execution_evidence_to_indirect_task_gate(self):
        with tempfile.TemporaryDirectory(prefix='capivara-build-context-') as temporary:
            cwd = Path(temporary)
            data = cwd / '.harness/dados'
            data.mkdir(parents=True)
            prep = preparation()
            prep.pop('operation')
            prep.pop('environment_result')
            tasks = [prep,
                     {'id': '1.2', 'title': 'Banco', 'description': 'Criar banco', 'dependencies': ['1.1']},
                     {'id': '1.3', 'title': 'API', 'description': 'Criar API', 'dependencies': ['1.2']}]
            phases = [{'id': '1', 'title': 'Fundação', 'description': 'Preparar aplicação', 'threads': tasks}]
            store = BuildStore(cwd)
            selection = Selection('codex', 'fixture', 'Default')
            options = ExecutionOptions()
            state = store.begin('prd', 'plan', phases, selection, options)
            for task in state['phases'][0]['threads'][:2]:
                task.update(operation='done', writer='done', g0='done', g1='done', g2='done')
            state['phases'][0]['threads'][0]['environment_result'] = preparation()['environment_result']
            store.save()
            captured = []
            class Runner:
                def __init__(self):self.selection = selection
                async def invoke(self, instructions, payload, **kwargs):
                    captured.append((payload, kwargs))
                    raise CapturedGate
                def safe(self, text):return text
            with patch('capivara2ptn.build_flow.read_build_input', return_value=('prd', 'plan', 'Python', phases)), \
                 patch('capivara2ptn.build_flow.load_prompt', return_value='G0 original'):
                with self.assertRaises(CapturedGate):
                    await run_build(BuildStore(cwd), Runner(), options, lambda state, text: None)
            payload, kwargs = captured[0]
            self.assertEqual(payload['task_atual']['id'], '1.3')
            self.assertEqual(payload['dependencias'][0]['id'], '1.2')
            actual = payload['evidencias_ambiente_execucao']['preparacoes_concluidas'][0]
            self.assertEqual(actual['resultado_ambiente']['comandos'][0]['codigo_retorno'], 0)
            self.assertEqual(kwargs['access'], 'read')
            self.assertNotIn('preparacao_ambiente', payload)
            self.assertFalse((cwd / 'server').exists())

    async def test_model_approval_cannot_bypass_failed_executor_preparation(self):
        from capivara2ptn.build_flow import BuildError
        with tempfile.TemporaryDirectory(prefix='capivara-build-failed-env-') as temporary:
            cwd = Path(temporary)
            (cwd / '.harness/dados').mkdir(parents=True)
            prep = preparation()
            prep.pop('operation')
            prep.pop('environment_result')
            phases = [{'id': '1', 'title': 'Fundação', 'description': 'Preparar aplicação', 'threads': [prep,
                       {'id': '1.2', 'title': 'Banco', 'description': 'Criar banco', 'dependencies': ['1.1']}]}]
            selection, options = Selection('codex', 'fixture', 'Default'), ExecutionOptions()
            store = BuildStore(cwd)
            state = store.begin('prd', 'plan', phases, selection, options)
            task = state['phases'][0]['threads'][0]
            task.update(operation='done', writer='done', g0='done', g1='done', g2='done',
                        environment_result=preparation('falha')['environment_result'])
            store.save()
            calls = []
            class Runner:
                def __init__(self):self.selection = selection
                async def invoke(self, instructions, payload, **kwargs):
                    calls.append(payload['operacao'])
                    self_operation = payload['operacao']
                    if self_operation != 'g0':
                        raise AssertionError('Produção não pode começar com verificação real falhando.')
                    return 'G0 APROVADO'
                def safe(self, text):return text
            with patch('capivara2ptn.build_flow.read_build_input', return_value=('prd', 'plan', 'Runtime', phases)), \
                 patch('capivara2ptn.build_flow.load_prompt', return_value='G0 original'):
                with self.assertRaisesRegex(BuildError, 'dependências.*não foram satisfeitas'):
                    await run_build(BuildStore(cwd), Runner(), options, lambda state, text: None)
            self.assertEqual(calls, ['g0'])
            self.assertFalse((cwd / 'server').exists())
