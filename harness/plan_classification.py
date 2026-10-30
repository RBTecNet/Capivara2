"""Classificação semântica única, com partição literal e cobertura preservada."""

from .plan_models import Requirement


def source_activities(requirements):
    return [r for r in requirements.values() if r.kind == 'activity' and r.phase_number > 0]


def check_classification(classification, requirements):
    expected = {r.id: r for r in source_activities(requirements)}
    ids = [item.source_id for item in classification.activities]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('Classifique exatamente uma vez todos os source_id de atividades fornecidos, sem criar ou omitir IDs.')
    for item in classification.activities:
        text = expected[item.source_id].text
        cursor = 0
        for part in item.parts:
            index = text.find(part.text, cursor)
            if index < 0:
                raise ValueError(f'{item.source_id}: cada parte deve ser uma citação literal e ordenada da atividade, sem paráfrase.')
            if text[cursor:index].strip(' \t\r\n,;:.—-'):
                raise ValueError(f'{item.source_id}: a partição omitiu texto antes de {part.text!r}. Inclua conectivos numa das partes.')
            cursor = index + len(part.text)
        if text[cursor:].strip(' \t\r\n,;:.—-'):
            raise ValueError(f'{item.source_id}: a partição omitiu texto ao final da atividade.')


def classified_requirements(classification, requirements):
    check_classification(classification, requirements)
    result = {key: item.model_copy(deep=True) for key, item in requirements.items()}
    for entry in classification.activities:
        original = result[entry.source_id]
        if len(entry.parts) > 1:
            original.execution_owner = 'collective'
            original.delegation_reason = 'Atividade mista, preservada como referência; responsabilidades divididas em partes literais.'
        for index, part in enumerate(entry.parts, 1):
            identifier = original.id if len(entry.parts) == 1 else f'{original.id}.{index:02d}'
            result[identifier] = Requirement(id=identifier, phase_number=original.phase_number, kind='activity',
                text=part.text, execution_owner='build' if part.responsibility == 'build' else 'tasks',
                delegation_reason=part.reason, source_id=original.id)
    return result
