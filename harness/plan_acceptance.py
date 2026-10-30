"""Aceites dos trechos corrigidos, sem congelar o restante do documento."""
import difflib
import re


def text(value):
    return '\n'.join(value) if isinstance(value, list) else value


def change_conditions(field, before, after):
    old, new = text(before), text(after)
    a, b = re.findall(r'P\d+-T\d+|\w+|\W', old), re.findall(r'P\d+-T\d+|\w+|\W', new)
    conditions = []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        for margin in range(max(len(a), len(b)) + 1):
            removed = ''.join(a[max(0, i-margin):min(len(a), j+margin)]).strip()
            added = ''.join(b[max(0, k-margin):min(len(b), l+margin)]).strip()
            candidates = []
            if added and added not in old:
                candidates.append({'field':field, 'operator':'contains', 'value':added})
            elif removed and removed not in new:
                candidates.append({'field':field, 'operator':'excludes', 'value':removed})
            if candidates:
                for condition in candidates:
                    if condition not in conditions:
                        conditions.append(condition)
                break
    return conditions


def effective_conditions(spec):
    """Aceites explícitos são preservados; aceites antigos de campo inteiro viram deltas."""
    changes = {c.field:c for c in spec.changes}
    result = []
    for condition in spec.acceptance:
        change = changes[condition.field]
        whole = ((condition.operator == 'contains' and condition.value == text(change.after)) or
                 (condition.operator == 'excludes' and condition.value == text(change.before)))
        values = change_conditions(change.field, change.before, change.after) if whole else [condition.model_dump()]
        for value in values:
            if value not in result:
                result.append(value)
    return result
