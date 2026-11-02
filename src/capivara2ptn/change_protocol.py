"""Contratos exclusivos do CHANGE; não altera parsers de INIT, PLAN ou BUILD."""

import json
import re
from pathlib import Path

from capivara2ptn.change_source import ChangeError, app_path


def response(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        value = json.loads(text)
    except (ValueError, IndexError):
        raise ChangeError('Resposta do modelo não é um objeto JSON válido.') from None
    if not isinstance(value, dict):
        raise ChangeError('O modelo deve responder com um objeto JSON.')
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ChangeError('Texto obrigatório ausente: ' + label)
    return value.strip()


def strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ChangeError('Lista inválida: ' + label)
    return value


def validate_question(value):
    question = text(value.get('text'), 'pergunta')
    choices = strings(value.get('choices', []), 'alternativas')
    if len(choices) != len(set(choices)):
        raise ChangeError('Alternativas da entrevista estão duplicadas.')
    return {'text': question, 'choices': choices}


def validate_investigation(value, known_paths):
    if value.get('request_type') not in {'correcao', 'modificacao', 'nova_funcionalidade'}:
        raise ChangeError('A investigação deve classificar o tipo de solicitação.')
    result = {key: text(value.get(key), key) for key in ('summary', 'current_behavior', 'desired_behavior')}
    result.update(request_type=value['request_type'], patterns=strings(value.get('patterns'), 'padrões'),
                  files=strings(value.get('files'), 'arquivos investigados'),
                  limitations=strings(value.get('limitations', []), 'limitações'))
    if not set(result['files']) <= set(known_paths):
        raise ChangeError('Investigação cita arquivos ainda não lidos. Solicite o contexto necessário.')
    evidence = value.get('evidence', [])
    if not isinstance(evidence, list):
        raise ChangeError('Evidências devem ser uma lista.')
    for ref in evidence:
        if not isinstance(ref, dict) or ref.get('path') not in known_paths or type(ref.get('line')) is not int or ref['line'] < 1:
            raise ChangeError('Evidência sem arquivo lido ou linha válida.')
    result['evidence'] = evidence
    return result


def validate_plan(value, root, forbid_tests=False):
    plan = {key: text(value.get(key), key) for key in ('objective', 'current_behavior', 'desired_behavior')}
    plan['preserve'] = strings(value.get('preserve'), 'funcionalidades a preservar')
    plan['limitations'] = strings(value.get('limitations', []), 'limitações')
    tasks = value.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise ChangeError('O plano precisa conter tasks pequenas e executáveis.')
    seen, planned = set(), []
    for task in tasks:
        if not isinstance(task, dict):
            raise ChangeError('Task inválida.')
        identifier = text(task.get('id'), 'id da task')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', identifier) or identifier in seen:
            raise ChangeError('IDs de task inválidos ou duplicados.')
        files = strings(task.get('files'), 'arquivos autorizados da task')
        if type(task.get('inspection_only', False)) is not bool:
            raise ChangeError('inspection_only deve ser um booleano.')
        if not files and not task.get('inspection_only'):
            raise ChangeError('Uma task de alteração precisa declarar arquivos concretos.')
        for path in files:
            target = app_path(root, path, writable=True)
            if target.exists() and not target.is_file():
                raise ChangeError('Declare arquivos concretos, não diretórios: ' + path)
        dependencies = strings(task.get('dependencies', []), 'dependências da task')
        if not set(dependencies) <= seen:
            raise ChangeError('As dependências devem ser tasks anteriores do plano.')
        seen.add(identifier)
        planned.append({'id': identifier, 'title': text(task.get('title'), 'título da task'),
                        'description': text(task.get('description'), 'descrição da task'),
                        'files': sorted(set(files)), 'dependencies': dependencies, 'inspection_only': task.get('inspection_only') is True})
    verification = value.get('verification')
    if not isinstance(verification, dict) or not isinstance(verification.get('commands'), list):
        raise ChangeError('O plano precisa declarar a verificação técnica aplicável.')
    commands = []
    for command in verification['commands']:
        if not isinstance(command, dict):
            raise ChangeError('Comando de verificação inválido.')
        directory = text(command.get('directory'), 'diretório de verificação')
        if directory != '.':
            target = app_path(root, directory)
            if target.exists() and not target.is_dir():
                raise ChangeError('O diretório de verificação não é uma pasta.')
        argv = strings(command.get('argv'), 'argumentos da verificação')
        if not argv or any('\x00' in item for item in argv):
            raise ChangeError('A verificação exige executável e argumentos explícitos.')
        commands.append({'directory': directory, 'argv': argv, 'purpose': text(command.get('purpose'), 'objetivo da verificação')})
    limitation = verification.get('limitation')
    if not commands:
        text(limitation, 'limitação de verificação sem comandos seguros')
    elif limitation is not None and not isinstance(limitation, str):
        raise ChangeError('Limitação de verificação inválida.')
    plan.update(tasks=planned, verification={'commands': commands, 'limitation': limitation}, forbid_tests=bool(forbid_tests))
    return plan


def render_plan(plan):
    lines = ['# Plano CHANGE', '', '## Objetivo', plan['objective'], '', '## Comportamento atual', plan['current_behavior'],
             '', '## Comportamento desejado', plan['desired_behavior'], '', '## Preservar',
             *['- ' + item for item in plan['preserve']], '', '## Tasks']
    for task in plan['tasks']:
        lines.extend(['', f"### {task['id']} — {task['title']}", task['description'],
                      'Arquivos autorizados: ' + (', '.join('`' + p + '`' for p in task['files']) or 'Nenhum; somente inspeção.'),
                      'Dependências: ' + (', '.join(task['dependencies']) or 'Nenhuma.')])
    lines += ['', '## Verificação técnica — cópia isolada, sem rede']
    for command in plan['verification']['commands']:
        lines += ['- ' + command['purpose'] + '\n  Diretório: ' + command['directory'] + '\n  Argumentos: `' + json.dumps(command['argv'], ensure_ascii=False) + '`']
    if plan['verification']['limitation']:
        lines += ['Limitação: ' + plan['verification']['limitation']]
    if plan['limitations']:
        lines += ['', '## Limitações', *['- ' + item for item in plan['limitations']]]
    return '\n'.join(lines) + '\n'


def task_states(plan, previous=None):
    previous = {task['id']: task for task in (previous or [])}
    result = []
    for definition in plan['tasks']:
        old = previous.get(definition['id'])
        if old and all(old.get(k) == v for k, v in definition.items()) and old.get('status') == 'done':
            result.append(old)
        else:
            result.append({**definition, 'status': 'pending', 'operation': 'g0', 'writer': 'pending',
                           'g0': 'pending', 'g1': 'pending', 'g2': 'pending', 'error': '', 'attempts': {}, 'changes': []})
    return result


def validate_gate(value):
    if type(value.get('approved')) is not bool:
        raise ChangeError('Parecer técnico sem decisão booleana.')
    issues = strings(value.get('issues'), 'problemas técnicos')
    if not value['approved'] and not issues:
        raise ChangeError('Reprovação sem motivo concreto.')
    return value
