"""Requisitos estáveis e responsabilidade de cada recorte, derivados do init."""

import re

from .plan_models import Requirement


def normalized(text):
    return ' '.join(text.casefold().split())


def is_build_validation(text):
    """Distingue QA explícito de validação funcional e preparação de infraestrutura."""
    return bool(re.match(
        r'^(?:(?:criar|escrever) testes\b|executar (?:verifica|teste|fluxo)|'
        r'(?:testar|demonstrar)\b|verificar (?:o |a |os |as )?(?:funcionamento|fluxos?|persistência|'
        r'continuidade|teclado|interface|integração|requisitos|contratos|comportamento)\b|'
        r'validar (?:os? )?(?:totais|fluxos|resultados)\b|'
        r'revisar (?:a )?interface\b|registrar (?:os? )?resultados\b)', normalized(text)))


def delegate_validations(requirements, delegations, number):
    result = {key: value.model_copy(deep=True) for key, value in requirements.items()}
    seen = set()
    for delegation in delegations:
        item = result.get(delegation.requirement_id)
        if number == 0:
            raise ValueError('A fase 0 cataloga stack e dependências. Finalidades de Vitest/Supertest/Playwright '
                             'descrevem para que servem as ferramentas, não mandam executar testes. '
                             'Mantenha sua identificação documental nas tasks; P0 não permite delegações de QA.')
        if (item is None or item.phase_number != number or item.kind != 'activity'
                or item.id in seen or not is_build_validation(item.text)):
            eligible = [r.id for r in result.values() if r.phase_number == number and r.kind == 'activity'
                        and is_build_validation(r.text)]
            raise ValueError(f'Delegação inválida: {delegation.requirement_id} '
                             f'(kind={item.kind if item else "inexistente"}). '
                             'Delegue apenas atividades explícitas de QA; nunca implementação, validação de campos '
                             'ou preparação da infraestrutura. Entregáveis/critérios/objetivos são referências coletivas: '
                             f'não precisam de task ou delegação própria. IDs delegáveis nesta fase: {eligible}.')
        seen.add(item.id)
        item.execution_owner = 'build'
        item.delegation_reason = delegation.reason
    return result


def build_requirements(document):
    requirements = []
    for index, item in enumerate(document['stack'], 1):
        requirements.append(Requirement(id=f'P0-S{index:03d}', phase_number=0, kind='technology',
            text=f"{item['component']}: {item['choice']}. {item['reason']}"))
    for index, item in enumerate(document['tools'], 1):
        requirements.append(Requirement(id=f'P0-T{index:03d}', phase_number=0, kind='tool',
            text=f"{item['name']}: {item['purpose']}"))
    for phase in document['phases']:
        number = phase['number']
        for key, prefix, kind in (('tasks', 'A', 'activity'), ('deliverables', 'D', 'deliverable'),
                                   ('acceptance_criteria', 'C', 'criterion')):
            for index, text in enumerate(phase[key], 1):
                explicit_qa = (kind == 'activity' and is_build_validation(text) and (
                    re.match(r'^(?:(?:criar|escrever) testes|executar (?:verifica|teste|fluxo)|demonstrar|'
                             r'revisar (?:a )?interface|registrar (?:os? )?resultados)', normalized(text))
                    or re.search(r'verifica|valida|teste', normalized(phase['title']))))
                requirements.append(Requirement(id=f'P{number}-{prefix}{index:03d}', phase_number=number,
                    kind=kind, text=text, execution_owner='build' if explicit_qa else 'tasks' if kind == 'activity' else 'collective',
                    delegation_reason='Atividade explícita de QA da origem; definição e execução de validações pertencem ao build.'
                    if explicit_qa else ''))
        requirements.append(Requirement(id=f'P{number}-G001', phase_number=number, kind='goal', text=phase['goal'],
                                        execution_owner='collective'))
    return {item.id: item for item in requirements}


def check_seed(seed, number, origin, requirements):
    if number == 0 and len({c.requirement_id for c in seed.coverage}) > 3:
        raise ValueError('Fase 0: divida o catálogo em seeds pequenos, com no máximo três IDs de stack/ferramentas por task. '
                         'Não agrupe toda a stack e todas as ferramentas numa unidade.')
    texts = [origin, *(r.text for r in requirements.values() if r.phase_number == number)]
    invalid = [quote for quote in seed.source_quotes if not quote.strip() or not any(quote in text for text in texts)]
    if invalid:
        raise ValueError('Citação ausente da fase e dos requisitos canônicos: ' + repr(invalid[0][:180]) +
                         '. Copie um trecho literal de requirements[].text ou phase_source.text, sem concatenar frases.')
    if not seed.coverage:
        raise ValueError('Cada seed precisa de coverage com requirement_id e excerpt literal da responsabilidade atribuída.')
    for claim in seed.coverage:
        item = requirements.get(claim.requirement_id)
        if item is None or item.phase_number != number or claim.excerpt not in item.text:
            raise ValueError('Coverage deve apontar um ID desta fase e um excerpt literal de seu requisito.')
        if item.execution_owner == 'build':
            raise ValueError('Atividade de validação já delegada ao build: não a converta em uma task de testes no plan.')


def check_assignments(phases, requirements):
    """Partes diferentes do mesmo requisito podem ter responsáveis diferentes."""
    for phase in phases:
        claims, scopes = {}, set()
        for task in phase.tasks:
            scope = normalized(task.seed.scope)
            if scope in scopes:
                raise ValueError('Há escopos idênticos. Corrija, divida ou consolide as tasks existentes em vez de adicionar duplicatas.')
            scopes.add(scope)
            for claim in task.seed.coverage:
                if requirements[claim.requirement_id].execution_owner == 'build':
                    raise ValueError('Remova a responsabilidade de QA delegada ao build das tasks de implementação.')
                key = (claim.requirement_id, normalized(claim.excerpt))
                if key in claims and claims[key] != task.id:
                    raise ValueError(f'O recorte {claim.requirement_id}/{claim.excerpt} já pertence a {claims[key]}. '
                                     'Use divisão/consolidação e atribuições distintas; não duplique a responsabilidade.')
                claims[key] = task.id
        assigned = {key[0] for key in claims}
        missing = [r.id for r in requirements.values() if r.phase_number == phase.number
                   and r.kind in ('activity', 'technology', 'tool') and r.execution_owner == 'tasks' and r.id not in assigned]
        if missing:
            raise ValueError('Atividades da origem sem responsável: ' + ', '.join(missing))


def coverage_map(phases, requirements):
    numbers = {phase.number for phase in phases}
    return [{'requirement': requirement.model_dump(), 'owners': [
        {'task_id': task.id, 'scope': task.seed.scope, 'excerpt': claim.excerpt}
        for phase in phases for task in phase.tasks for claim in task.seed.coverage
        if claim.requirement_id == requirement.id]}
        for requirement in requirements.values() if requirement.phase_number in numbers]


def requests_structural_operation(instruction):
    return bool(re.search(r'\b(remov[ae]|exclua|elimine)\s+(esta\s+|a\s+)?task\b', instruction, re.I))
