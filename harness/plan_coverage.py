"""Requisitos estáveis e responsabilidade de cada recorte, derivados do init."""

import re

from .plan_models import Requirement


def normalized(text):
    return ' '.join(text.casefold().split())


def delegate_validations(requirements, delegations, number):
    if delegations:
        raise ValueError('Responsabilidades são definidas na classificação semântica aprovada; a revisão não pode alterá-las.')
    return requirements


def build_requirements(document):
    requirements = [Requirement(id=f"R{i:03d}", phase_number=-1, kind="criterion", text=text,
                                execution_owner="collective") for i, text in enumerate(document['requirements'], 1)]
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
                requirements.append(Requirement(id=f'P{number}-{prefix}{index:03d}', phase_number=number,
                    kind=kind, text=text, execution_owner='tasks' if kind == 'activity' else 'collective'))
        requirements.append(Requirement(id=f'P{number}-G001', phase_number=number, kind='goal', text=phase['goal'],
                                        execution_owner='collective'))
    return {item.id: item for item in requirements}


def check_seed(seed, number, origin, requirements):
    if number == 0 and len({c.requirement_id for c in seed.coverage}) > 3:
        raise ValueError('Fase 0: divida o catálogo em seeds pequenos, com no máximo três IDs de stack/ferramentas por task. '
                         'Não agrupe toda a stack e todas as ferramentas numa unidade.')
    # Requisitos globais e stack/ferramentas são contexto fornecido e origem válida.
    # Isso não muda coverage: a responsabilidade continua limitada aos IDs desta fase.
    texts = [origin, *(r.text for r in requirements.values()
                      if r.phase_number in (number, -1) or r.kind in ('technology','tool'))]
    invalid = [quote for quote in seed.source_quotes if not quote.strip() or not any(quote in text for text in texts)]
    if invalid:
        raise ValueError('Citação ausente das origens fornecidas (fase, requisitos globais e stack/ferramentas): ' + repr(invalid[0][:180]) +
                         '. Use um trecho da origem fornecida, sem inventar ou concatenar frases.')
    if not seed.coverage:
        raise ValueError('Cada seed precisa de coverage com requirement_id e excerpt literal da responsabilidade atribuída.')
    for claim in seed.coverage:
        item = requirements.get(claim.requirement_id)
        if item is None or item.phase_number != number or claim.excerpt not in item.text:
            raise ValueError('Coverage deve apontar um ID desta fase e um excerpt literal de seu requisito.')
        if item.execution_owner == 'build':
            raise ValueError('Atividade de validação já delegada ao build: não a converta em uma task de testes no plan.')
        if item.execution_owner == 'collective':
            raise ValueError(f'{item.id} é referência coletiva. Atribua somente partes com execution_owner=tasks, não o parágrafo misto ou critério inteiro.')


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
