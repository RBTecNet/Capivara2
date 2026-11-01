"""G1: inventário de hashes e comparação, sem Git e sem LLM."""

import hashlib
import os
import re
import stat
from pathlib import Path

# Dependências instaladas, caches e controle não são entrega da task.
IGNORED = {".harness", ".git", ".hg", ".svn", "node_modules", ".venv", "venv",
           "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def inventory(root: Path) -> dict[str, str]:
    result = {}

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name in IGNORED:
                    continue
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    content = os.readlink(path).encode()
                    result[relative] = "link:" + hashlib.sha256(content).hexdigest()
                elif stat.S_ISDIR(mode):
                    visit(path)
                elif stat.S_ISREG(mode):
                    with path.open("rb") as stream:
                        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                    result[relative] = f"{stat.S_IMODE(mode):o}:" + checksum
    visit(root)
    return result


def inspection_only(task: dict) -> bool:
    if task.get("technical_check") or task.get("environment_check") or task.get("readme_delivery"):
        return True  # Executar uma verificação não exige produzir/alterar código.
    description = task["description"]
    explicit = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:Natureza|Tipo de entrega)\s*:\s*(?:inspeção|somente leitura|sem alterações)\s*\.?$", description)
    title = task["title"].casefold().strip()
    inspection = re.match(r"(?:inspecionar|analisar|verificar|conferir|investigar|diagnosticar|revisar|inspeção|análise|verificação|diagnóstico|typecheck|compilar|compilação|(?:executar|rodar|realizar)\s+(?:a\s+)?(?:verificação|conferência|inspeção|typecheck|checagem|compilação|build))\b", title)
    # Uma inspeção que também manda produzir um relatório/arquivo exige entrega.
    verbs = r"(?:criar|implementar|alterar|modificar|salvar|gravar|gerar|escrever|remover|adicionar|configurar|instalar|corrigir)"
    affirmative = re.sub(rf"\b(?:não|sem)\s+{verbs}(?:\s+(?:nem|ou)\s+{verbs})*", "", description, flags=re.I)
    writing = re.search(rf"\b{verbs}\b", affirmative, re.I)
    return bool((explicit or inspection) and not writing)


def evaluate(before: dict[str, str], after: dict[str, str], task: dict) -> dict:
    changes = [{"arquivo": name, "tipo": "criado" if name not in before else "removido" if name not in after else "modificado"}
               for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    approved = bool(changes) or inspection_only(task)
    reason = ("Alterações reais identificadas na área do projeto." if changes else
              "Task de inspeção sem exigência identificada de alterar arquivos." if approved else
              "Nenhum arquivo do projeto foi criado, modificado ou removido desde o início desta task; artefatos internos e caches não contam como entrega.")
    return {"aprovado": approved, "motivo": reason, "arquivos": changes}
