"""Migração do ciclo de init.sh: entrevista, PRD separado e somente G0 INIT."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from capivara2ptn.prompt_files import load_prompt
from capivara2ptn.providers import CliRunner, ProviderError
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
    NAMES = ("prompt-original.txt", "entrevista.txt", "resposta.txt", "prompt-final.md", "g0-init.txt", "erro-init.txt")

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.directory = data_directory(self.cwd)
        self._owned: set[str] = set()

    def begin(self, original: str) -> None:
        migrate_legacy(self.cwd)
        ensure_project_destination(self.cwd)
        ensure_harness(self.cwd)
        if self.directory.is_symlink() or any((self.directory / name).exists() or (self.directory / name).is_symlink() for name in self.NAMES):
            raise InitError("Já existem arquivos do INIT em .harness/dados/ ou o diretório é um link. Nada foi sobrescrito; use outro diretório ou preserve/mova os arquivos anteriores.")
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise InitError("Não foi possível criar .harness/dados/ no diretório de trabalho.") from None
        self.write("prompt-original.txt", original)
        self.write("entrevista.txt", "")
        self.write("resposta.txt", "")

    def write(self, name: str, text: str, append: bool = False) -> None:
        if name not in self.NAMES:
            raise InitError("Arquivo de execução não reconhecido.")
        flags = os.O_WRONLY | os.O_NOFOLLOW
        flags |= (os.O_APPEND if append else os.O_TRUNC) if name in self._owned else os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.directory / name, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
            self._owned.add(name)
        except OSError:
            raise InitError(f"Não foi possível salvar .harness/dados/{name}. Os arquivos anteriores foram preservados.") from None

    def history(self) -> str:
        return (self.directory / "entrevista.txt").read_text(encoding="utf-8")

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

    def data() -> dict[str, str]:
        return {"pedido_original": original, "historico_entrevista": files.history()}

    async def call(prompt: str, payload: dict[str, str], output: str, executor: CliRunner | None = None) -> str:
        instructions = load_prompt(prompt)  # Leitura nova, inclusive a cada rodada.
        try:
            response = await (executor or runner).invoke(instructions, payload)
        except ProviderError as error:
            if error.partial:
                files.write(output, error.partial)
            raise
        files.write(output, response)
        return response

    question_number = 0
    while True:
        stage("Entrevista", "Consultando o modelo com o pedido original e o histórico completo.")
        response = await call("init.md", data(), "resposta.txt")
        question = parse_question(response)
        if question is None:
            stage("Entrevista concluída", "O modelo respondeu CONCLUIDO. Gerando o PRD em uma chamada separada.")
            break
        question_number += 1
        stage("Aguardando resposta", f"Pergunta {question_number} da entrevista.")
        answer = await ask(question)
        if answer is None:
            raise InitCancelled("Entrevista cancelada. Pedido original e histórico já confirmado foram preservados em .harness/dados/.")
        files.write("entrevista.txt", f"Pergunta: {question.transcript()}\nResposta: {answer}\n\n", append=True)

    stage("Gerando PRD", "Produzindo a especificação final a partir do pedido e da entrevista.")
    prd = await call("init-final.md", data(), "prompt-final.md")
    if not gates_enabled:
        stage("Concluído", "INIT concluído. Gates desabilitados: G0 INIT não foi executado. PRD salvo em .harness/dados/prompt-final.md.")
        return files.directory / "prompt-final.md"
    stage("Avaliando G0", "Validando somente a fidelidade do PRD, em chamada independente e somente leitura.")
    # As instruções vêm exclusivamente do Markdown; o conteúdo é enviado como dados.
    response = await call("gates/g0-init.md", {**data(), "prd_final": prd}, "g0-init.txt", auditor)
    approved, issues = g0_decision(response)
    if not approved:
        raise InitError("G0 REPROVADO\n" + issues + "\n\nArquivos preservados em .harness/dados/; INIT não concluído.")
    stage("Concluído", "G0 APROVADO. PRD final salvo em .harness/dados/prompt-final.md.")
    return files.directory / "prompt-final.md"
