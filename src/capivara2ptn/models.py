"""Descoberta de modelos pelas CLIs locais, sem iniciar conversas ou agentes."""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

PROVIDERS = (
    ("Codex CLI", "codex"),
    ("Claude CLI", "claude"),
    ("Agy CLI", "agy"),
    ("OpenCode CLI", "opencode"),
)


@dataclass
class ModelCatalog:
    models: list[tuple[str, str]]  # Rótulo apresentado e ID original da CLI.
    message: str
    manual: bool = False


def _model_id(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not any(char.isspace() for char in value)


async def _close(process: asyncio.subprocess.Process) -> None:
    """Encerra somente o subprocesso de consulta, inclusive após cancelamento."""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.communicate(), timeout=1)
    except asyncio.TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()


async def _listing(executable: str, cwd: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        executable, "models", cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1"},
    )
    try:
        output, _ = await process.communicate()
        if process.returncode:
            raise ValueError(f"A CLI encerrou a listagem com código {process.returncode}.")
        return output.decode("utf-8", errors="replace")
    finally:
        await _close(process)


async def _codex(executable: str, cwd: Path) -> list[tuple[str, str]]:
    # O protocolo oficial do app-server usa JSON por linha sobre stdio.
    # Nenhum thread/start ou turn/start é enviado.
    process = await asyncio.create_subprocess_exec(
        executable, "app-server", cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024,
    )

    async def send(message: dict) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    async def response(identifier: int) -> dict:
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") == identifier:
                if "error" in message:
                    raise ValueError("O app-server não disponibilizou a listagem de modelos.")
                result = message.get("result")
                if not isinstance(result, dict):
                    break
                return result
        raise ValueError("Resposta de listagem do Codex inválida ou incompleta.")

    try:
        await send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "capivara2ptn", "version": "0.1.0"},
        }})
        await response(1)
        await send({"method": "initialized", "params": {}})
        models = []
        cursor = None
        seen_cursors = set()
        request_id = 2
        while True:
            await send({"id": request_id, "method": "model/list", "params": {
                "includeHidden": False, "limit": 100, "cursor": cursor,
            }})
            result = await response(request_id)
            entries = result.get("data")
            if not isinstance(entries, list):
                raise ValueError("Formato de catálogo do Codex não reconhecido.")
            for entry in entries:
                identifier = entry.get("model") or entry.get("id")
                if _model_id(identifier) and not entry.get("hidden", False):
                    models.append((entry.get("displayName") or identifier, identifier))
            cursor = result.get("nextCursor")
            if cursor is None:
                return models
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ValueError("Paginação de modelos do Codex inválida.")
            seen_cursors.add(cursor)
            request_id += 1
    finally:
        await _close(process)


async def discover_models(provider: str, cwd: Path) -> ModelCatalog:
    """Consulta cancelável com prazo limitado; falhas liberam entrada manual."""
    executable = shutil.which(provider)
    if executable is None:
        return ModelCatalog([], f"CLI {provider} não encontrada no PATH. Informe o ID do modelo manualmente.", True)
    try:
        async with asyncio.timeout(20):
            if provider == "claude":
                # Aliases explicitamente solicitados pelo usuário; não são
                # apresentados como um catálogo descoberto da conta.
                catalog = ModelCatalog(
                    [("Opus", "opus"), ("Sonnet", "sonnet"), ("Fable", "fable"), ("Haiku", "haiku")],
                    "Claude: aliases Opus, Sonnet, Fable e Haiku; sem listagem automática. Outros IDs podem ser informados manualmente.",
                    manual=True,
                )
            elif provider == "codex":
                catalog = ModelCatalog(await _codex(executable, cwd), "Modelos obtidos por codex app-server: model/list.")
            elif provider in ("agy", "opencode"):
                output = await _listing(executable, cwd)
                models = []
                for line in output.splitlines():
                    if provider == "agy":
                        identifier, separator, label = line.partition("\t")
                        if separator and _model_id(identifier):
                            models.append((label.strip() or identifier, identifier))
                    else:
                        identifier = line.strip()
                        if _model_id(identifier) and "/" in identifier:
                            models.append((identifier, identifier))
                catalog = ModelCatalog(models, f"Modelos obtidos por {provider} models.")
            else:
                raise ValueError("Provedor sem mecanismo de descoberta conhecido.")
        unique = {}
        for label, identifier in catalog.models:
            unique.setdefault(identifier, label)
        catalog.models = [(label, identifier) for identifier, label in unique.items()]
        if not catalog.models:
            catalog.message += " Nenhum modelo encontrado. Informe o ID manualmente."
            catalog.manual = True
        return catalog
    except asyncio.TimeoutError:
        return ModelCatalog([], f"A consulta a {provider} excedeu 20 segundos. Informe o ID manualmente.", True)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return ModelCatalog([], f"Não foi possível obter modelos de {provider}; listagem ou configuração indisponível/inválida. Informe o ID manualmente.", True)
