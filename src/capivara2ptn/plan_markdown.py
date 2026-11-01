"""Normalização restrita aos títulos e leitura estrutural do Markdown do PLAN."""

import re


class PlanFormatError(Exception):
    pass


def _heading(line: str) -> tuple[str, str, str] | None:
    text = line.strip()
    if text.startswith("**") and text.endswith("**"):
        text = text[2:-2].strip()
    match = re.fullmatch(r"(#{2,3})\s*(.+)", text)
    if not match:
        return None
    level, body = match.groups()
    if body.startswith("**") and body.endswith("**"):
        body = body[2:-2].strip()
    pattern = r"Fase\s+(\d+)\s*[-–—:]\s*(.+)" if level == "##" else r"Task\s+(\d+\.\d+)\s*[-–—:]\s*(.+)"
    title = re.fullmatch(pattern, body, flags=re.IGNORECASE)
    if title and title[2].strip():
        return ("phase" if level == "##" else "thread", title[1], title[2].strip())
    return None


def normalize_plan(markdown: str) -> str:
    """Mantém todo o restante, inclusive linhas em branco e blocos desconhecidos."""
    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        stripped = body.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = marker if fence is None else (None if fence == marker else fence)
            output.append(line)
            continue
        heading = _heading(body) if fence is None else None
        if heading is None:
            output.append(line)
        else:
            kind, identifier, title = heading
            prefix = "## Fase" if kind == "phase" else "### Task"
            output.append(f"{prefix} {identifier} — {title}" + line[len(body):])
    return "".join(output)


def parse_plan(markdown: str, defined_ids: set[str] | None = None) -> tuple[str, list[dict]]:
    """Lê somente fases/tasks e referências declaradas, sem julgar sua qualidade."""
    preamble = []
    phases = []
    phase = None
    thread = None
    fence = None
    phase_ids = set()
    thread_ids = set()
    for number, line in enumerate(markdown.splitlines(keepends=True), 1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = marker if fence is None else (None if fence == marker else fence)
            heading = None
        else:
            heading = _heading(line) if fence is None else None
        if heading:
            kind, identifier, title = heading
            if kind == "phase":
                if identifier in phase_ids:
                    raise PlanFormatError(f"Fase duplicada na linha {number}.")
                phase_ids.add(identifier)
                phase = {"id": identifier, "title": title, "description": "", "threads": []}
                phases.append(phase)
                thread = None
            else:
                if phase is None or identifier.split(".")[0] != phase["id"] or identifier in thread_ids:
                    raise PlanFormatError(f"Task duplicada ou sem fase correspondente na linha {number}.")
                thread_ids.add(identifier)
                thread = {"id": identifier, "title": title, "description": "", "dependencies": [], "writer": "pending", "error": ""}
                phase["threads"].append(thread)
            continue
        if fence is None:
            candidate = line.strip().lstrip("*_").strip()
            suspicious = re.match(r"#{1,6}\s*(?:\*\*)?(?:Fase|Task)(?=\s|\d|$)", candidate, flags=re.IGNORECASE)
            if suspicious or (phase is not None and re.match(r"#{1,3}\s", candidate)):
                raise PlanFormatError(f"Título não interpretável com segurança na linha {number}; o Markdown foi preservado.")
        if thread is not None:
            thread["description"] += line
        elif phase is not None:
            phase["description"] += line
        else:
            preamble.append(line)
    if fence is not None:
        raise PlanFormatError("Bloco Markdown não fechado; não é seguro identificar as tasks.")
    if not phases or any(not item["threads"] for item in phases):
        raise PlanFormatError("O plano precisa conter fases e tasks identificáveis; nenhum conteúdo foi descartado.")
    defined = set(defined_ids or ())
    for phase in phases:
        phase["description"] = phase["description"].strip("\r\n")
        for thread in phase["threads"]:
            description = thread["description"].strip("\r\n")
            dependency_lines = re.findall(r"^\s*(?:[-*]\s+)?(?:\*\*)?Depend[eê]ncias(?:\*\*)?\s*:(?:\*\*)?\s*(.*?)\s*$", description, flags=re.IGNORECASE | re.MULTILINE)
            if len(dependency_lines) != 1:
                raise PlanFormatError(f"Task {thread['id']}: declare uma linha Dependências: nenhuma ou IDs de tasks anteriores.")
            dependency_text = dependency_lines[0].strip().strip("*`")
            dependencies = list(dict.fromkeys(re.findall(r"\b\d+\.\d+\b", dependency_text)))
            if not dependencies and not re.fullmatch(r"(?:nenhuma|nenhum|n[aã]o\s+h[aá]|-)\.?", dependency_text, flags=re.IGNORECASE):
                raise PlanFormatError(f"Task {thread['id']}: dependências não interpretáveis com segurança.")
            if any(identifier not in defined for identifier in dependencies):
                raise PlanFormatError(f"Task {thread['id']}: dependência desconhecida, própria ou posterior; a ordem original foi preservada.")
            thread["description"] = description
            thread["dependencies"] = dependencies
            defined.add(thread["id"])
    return "".join(preamble).strip("\r\n"), phases


def parse_task_correction(response: str, phase: dict, thread: dict, available: set[str]) -> dict:
    text = normalize_plan(response).strip("\r\n")
    if text.strip().startswith("ALTERACAO_AMPLA_NECESSARIA"):
        raise PlanFormatError("A correção exige uma alteração mais ampla; nenhuma proposta foi aplicada.\n" + text.strip())
    lines = text.splitlines()
    heading = _heading(lines[0]) if lines else None
    if heading is None or heading[:2] != ("thread", thread["id"]):
        raise PlanFormatError("O escritor deve corrigir somente a task atual, mantendo seu ID. Resposta preservada; o plano não foi reescrito.")
    _, phases = parse_plan(f"## Fase {phase['id']} — {phase['title']}\n" + text, available)
    if len(phases) != 1 or len(phases[0]["threads"]) != 1:
        raise PlanFormatError("A resposta tentou alterar outras tasks/fases. Uma alteração mais ampla precisa ser explícita; proposta não aplicada.")
    corrected = phases[0]["threads"][0]
    return {key: corrected[key] for key in ("id", "title", "description", "dependencies")}


def replace_task(markdown: str, identifier: str, corrected: dict) -> str:
    """Troca só o intervalo da task; prefixo e sufixo permanecem byte a byte."""
    lines = markdown.splitlines(keepends=True)
    start = end = None
    fence = None
    found = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = marker if fence is None else (None if fence == marker else fence)
            continue
        heading = _heading(line) if fence is None else None
        if heading:
            if start is not None and end is None:
                end = index
            if heading[:2] == ("thread", identifier):
                found += 1
                start = index
    if found != 1 or start is None:
        raise PlanFormatError("Não foi possível isolar uma única task no Markdown. Nenhuma alteração foi aplicada.")
    end = len(lines) if end is None else end
    replacement = f"### Task {identifier} — {corrected['title']}\n{corrected['description'].rstrip(chr(10))}\n\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])
