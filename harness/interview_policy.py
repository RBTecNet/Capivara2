"""Regras de entrevista: ferramentas de validação são decisões do harness."""

import re
import unicodedata

from .init_models import Question


def plain(text: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(character))


TEST_TOOL = re.compile(
    r"\b(?:playwright|vitest|jest|cypress|selenium|pytest|unittest|mocha|jasmine|"
    r"testng|junit|nunit|xunit|rspec|testing\s+library|robot\s+framework)\b"
)
TEST_TOOL_CHOICE = re.compile(
    r"\b(?:suite|switch|stack|framework|runner)s?\s+(?:(?:de|para|dos|os|a|as|um|uma)\s+){0,3}test(?:e|es|s)?\b"
    r"|\bferramentas?\s+(?:de|para)\s+(?:test(?:e|es|s)?|qa|validacao)\b"
    r"|\b(?:nivel|percentual|meta)\s+(?:de\s+)?cobertura\s+(?:de\s+)?test(?:e|es|s)?\b"
    r"|\btest(?:ing)?\s+(?:suite|framework|runner|stack)\b"
)


def internal_question_reason(question: Question) -> str | None:
    # Usa a pergunta e as escolhas; mencionar testes nos requisitos ou na citação
    # do objetivo não transforma uma decisão funcional em ferramenta interna.
    text = plain(question.question)
    if (question.topic == "requirements"
            and re.search(r"\b(?:integracao|integracoes|suportar|suporte|oferecer|expor)\b", text)
            and re.search(r"\b(?:produto|plataforma|aplicacao|clientes|usuarios)\b", text)):
        # Um produto pode oferecer integrações com motores de testes: decidir
        # essa funcionalidade é diferente de escolher como o harness faz QA.
        return None
    options = [plain(f"{option.label} {option.description}") for option in question.options]
    tool_options = sum(bool(TEST_TOOL.search(option)) for option in options)
    if (TEST_TOOL_CHOICE.search(text)
            or tool_options == len(options)
            or (TEST_TOOL.search(text) and tool_options >= 2)):
        return ("A escolha de suíte/framework de testes ou navegador de validação pertence ao harness. "
                "Não apresente essa pergunta ao usuário: resolva seus gap_ids em inferred_decisions "
                "e inclua as ferramentas inferidas na fase 0 com scope=harness. Se houver também "
                "uma decisão funcional do produto, separe-a da escolha das ferramentas de QA.")
    return None
