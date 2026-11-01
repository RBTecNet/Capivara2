"""Migração do ciclo de init.sh: entrevista, PRD separado e somente G0 INIT."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from capivara2ptn.prompt_files import load_prompt
from capivara2ptn.providers import CliRunner, ProviderError
from capivara2ptn.init_resume import InitSession, RUN_FILES, load_correction_prompt
from capivara2ptn.workspace import data_directory, migrate_legacy, ensure_project_destination, ensure_harness


class InitError(Exception):
    pass


class InitCancelled(InitError):
    pass


@dataclass(frozen=True)
class Question:
    text: str
    choices: tuple[str, ...]

    def transcript(self) -> str:
        return self.text + "\n" + "\n".join(f"{index + 1}. {choice}" for index, choice in enumerate(self.choices))


def parse_question(response: str) -> Question | None:
    if response.strip().upper() == "CONCLUIDO":
        return None
    text = response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        raise InitError("A CLI não retornou CONCLUIDO nem uma pergunta com alternativas no formato esperado. Veja .harness/dados/resposta.txt.") from None
    if (not isinstance(data, dict) or set(data) != {"pergunta", "alternativas"}
            or not isinstance(data["pergunta"], str) or not data["pergunta"].strip()
            or not isinstance(data["alternativas"], list)
            or len(data["alternativas"]) < 2
            or any(not isinstance(choice, str) or not choice.strip() for choice in data["alternativas"])
            or len(set(data["alternativas"])) < 2):
        raise InitError("Pergunta inválida: o INIT exige uma pergunta e alternativas concretas. Veja .harness/dados/resposta.txt.")
    return Question(data["pergunta"].strip(), tuple(data["alternativas"]))


def g0_decision(response: str) -> tuple[bool, str]:
    text = response.strip()
    decisions = re.findall(r"\bG0\s+(APROVADO|REPROVADO)\b", text, flags=re.IGNORECASE)
    if text == "G0 APROVADO" and decisions == ["APROVADO"]:
        return True, ""
    lines = text.splitlines()
    if len(decisions) == 1 and lines and lines[0] == "G0 REPROVADO" and "\n".join(lines[1:]).strip():
        return False, "\n".join(lines[1:]).strip()
    raise InitError("G0 inconclusivo: resposta sem decisão única no formato exigido ou sem justificativa da reprovação. INIT não aprovado; veja .harness/dados/g0-init.txt.")


class RunFiles:
    NAMES = RUN_FILES

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.directory = data_directory(self.cwd)
        self.session = InitSession(self.cwd, InitError, parse_question, g0_decision)
        self._owned = self.session.owned

    def begin(self, original: str) -> None:
        migrate_legacy(self.cwd)
        ensure_project_destination(self.cwd)
        ensure_harness(self.cwd)
        self.session.begin(original)

    def write(self, name: str, text: str, append: bool = False) -> None:
        self.session.write(name, text, append)

    def history(self) -> str:
        return self.session.text("entrevista.txt") or ""

    @property
    def started(self) -> bool:
        return "prompt-original.txt" in self._owned


async def run_init(
    original: str,
    files: RunFiles,
    runner: CliRunner,
    ask: Callable[[Question], Awaitable[str | None]],
    stage: Callable[[str, str], None],
    *,
    gates_enabled: bool = True,
    auditor: CliRunner | None = None,
) -> Path:
    files.begin(original)
    session = files.session

    def data() -> dict[str, str]:
        return {"pedido_original": original, "historico_entrevista": files.history()}

    async def call(prompt: str, payload: dict[str, str], output: str, executor: CliRunner | None = None) -> str:
        session.check()
        instructions = load_correction_prompt() if prompt == "init-correction.md" else load_prompt(prompt)
        try:
            response = await (executor or runner).invoke(instructions, payload)
        except ProviderError as error:
            if error.partial:
                session.partial(output, error.partial)
            raise
        files.write(output, response)
        return response

    if session.resuming:
        stage("Entrevista concluída" if session.state["interview_complete"] else "Entrevista",
              "Retomando INIT com o pedido salvo e as respostas já confirmadas; nenhuma etapa concluída será repetida.")
    while not session.state["interview_complete"]:
        pending = session.state["question"]
        if pending is None:
            stage("Entrevista", "Consultando o modelo com o pedido original e o histórico completo.")
            response = await call("init.md", data(), "resposta.txt")
            question = session.accept_interview_response(response)
            if question is None:
                stage("Entrevista concluída", "O modelo respondeu CONCLUIDO. Gerando o PRD em uma chamada separada.")
                break
        else:
            question = Question(pending["text"], tuple(pending["choices"]))
        stage("Aguardando resposta", f"Pergunta {session.state['questions_answered'] + 1} da entrevista.")
        answer = await ask(question)
        if answer is None:
            raise InitCancelled("Entrevista cancelada. Pedido original e histórico já confirmado foram preservados em .harness/dados/.")
        session.answer(question, answer)

    if session.state["phase"] == "correcting":
        stage("Gerando PRD", "Retomando a correção do PRD a partir dos motivos da reprovação do G0; entrevista preservada.")
        prd = await call("init-correction.md", {**data(), "prd_anterior": session.text("prompt-final.md"),
                         "reprovacao_g0": session.state["g0_issues"]}, "prompt-final.md")
    elif session.state["phase"] == "writing":
        stage("Gerando PRD", "Produzindo a especificação final a partir do pedido e da entrevista.")
        prd = await call("init-final.md", data(), "prompt-final.md")
    else:
        prd = session.text("prompt-final.md")
        if not prd or not prd.strip():
            raise InitError("O PRD confirmado está ausente. Resultados anteriores preservados.")
    if not gates_enabled:
        session.complete()
        stage("Concluído", "INIT concluído. Gates desabilitados: G0 INIT não foi executado. PRD salvo em .harness/dados/prompt-final.md.")
        return files.directory / "prompt-final.md"
    if session.state["phase"] == "done" and session.state["g0_status"] == "approved":
        session.complete()
        stage("Concluído", "INIT retomado após G0 já aprovado; resultado e histórico preservados.")
        return files.directory / "prompt-final.md"
    session.transition(phase="reviewing")
    stage("Avaliando G0", "Validando somente a fidelidade do PRD, em chamada independente e somente leitura.")
    # As instruções vêm exclusivamente do Markdown; o conteúdo é enviado como dados.
    response = await call("gates/g0-init.md", {**data(), "prd_final": prd}, "g0-init.txt", auditor)
    approved, issues = g0_decision(response)
    if not approved:
        raise InitError("G0 REPROVADO\n" + issues + "\n\nArquivos preservados em .harness/dados/; INIT não concluído.")
    session.complete()
    stage("Concluído", "G0 APROVADO. PRD final salvo em .harness/dados/prompt-final.md.")
    return files.directory / "prompt-final.md"
