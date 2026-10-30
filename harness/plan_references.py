"""Reassociação de IDs após mudanças estruturais com sucessor inequívoco."""
import re


def rewrite(value, bindings):
    if isinstance(value, list):
        return [rewrite(item, bindings) for item in value]
    if not isinstance(value, str) or not bindings:
        return value
    pattern = r'(?<![\w-])(?:' + '|'.join(re.escape(k) for k in bindings) + r')(?![\w-])'
    return re.sub(pattern, lambda match:bindings[match[0]], value)


def successors(before, after, removed):
    old = {t.id:t for p in before for t in p.tasks}
    bindings = {}
    for identifier in removed:
        task = old[identifier]
        if not task.seed.coverage:
            continue
        candidates = [t.id for p in after for t in p.tasks if p.number == task.phase_number and all(
            any(c.requirement_id == prior.requirement_id and prior.excerpt in c.excerpt for c in t.seed.coverage)
            for prior in task.seed.coverage)]
        if len(candidates) == 1:
            bindings[identifier] = candidates[0]
    return bindings


def rebind_tasks(phases, bindings):
    touched = set()
    for phase in phases:
        for task in phase.tasks:
            data = task.content.model_dump()
            updated = {k:rewrite(v, bindings) for k,v in data.items()}
            scope, title = rewrite(task.seed.scope, bindings), rewrite(task.seed.title, bindings)
            if data != updated or scope != task.seed.scope or title != task.seed.title:
                task.content = type(task.content).model_validate(updated)
                task.seed.scope, task.seed.title = scope, title
                touched.add(task.id)
    return touched


def referencing_tasks(phases, identifiers):
    bindings = {identifier:'' for identifier in identifiers}
    return {task.id for phase in phases for task in phase.tasks if any(
        rewrite(value, bindings) != value
        for value in [*task.content.model_dump().values(), task.seed.scope, task.seed.title])}
