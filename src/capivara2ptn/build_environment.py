"""Proveniência das verificações reais do executor, compartilhada com os modelos."""

import copy

from capivara2ptn.delivery import host_context

ORIGIN = 'executor determinístico da CAPIVARA2PTN, fora do processo isolado da CLI do modelo'


def dependency_context(task: dict) -> dict:
    data = {key: task.get(key, '') for key in ('id', 'title', 'description', 'writer_report')}
    if task.get('environment_check'):
        data.update(operacao_executor=task.get('operation'),
                    verificacao_ambiente=copy.deepcopy(task['environment_check']),
                    resultado_ambiente=copy.deepcopy(task.get('environment_result')),
                    origem_resultado_ambiente=ORIGIN)
    return data


def environment_context(completed: dict) -> dict | None:
    preparations = [dependency_context(task) for task in completed.values() if task.get('environment_check')]
    if not preparations:
        return None
    return {
        'origem': ORIGIN,
        'ambiente_do_executor': host_context(),
        'escopo_dos_resultados': 'Os códigos de retorno e as saídas abaixo foram obtidos no host pelo executor, não relatados pelo escritor.',
        'escopo_da_cli': 'A CLI do modelo possui seu próprio isolamento e permissões. Uma falha nesse ambiente não demonstra, por si só, falha do mesmo comando no host do executor.',
        'preparacoes_concluidas': preparations,
    }
