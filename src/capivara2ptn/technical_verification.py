"""Procedimento da última task: comandos do PLAN, executados sem LLM."""

import asyncio
import codecs
import json
import os
import re
import signal
import shlex
import shutil
from pathlib import Path

from capivara2ptn.plan_markdown import PlanFormatError
from capivara2ptn.plan_state import write_json

TITLE = "Verificação técnica da aplicação"
BLOCK = re.compile(r"(?ms)^\s*```verificacao-tecnica[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*\r?$")


def configure_final_task(phases: list[dict], *, required: bool = False) -> None:
    """Lê o pequeno contrato de execução; não julga tecnologias ou qualidade."""
    tasks = [task for phase in phases for task in phase["threads"]]
    found = []
    for task in tasks:
        task.pop("technical_check", None)
        blocks = BLOCK.findall(task["description"])
        named = " ".join(task["title"].casefold().split()) == TITLE.casefold()
        if not blocks and not named:
            continue
        if task is not tasks[-1] or not named or len(blocks) != 1:
            raise PlanFormatError("Declare uma única task final 'Verificação técnica da aplicação', com um bloco verificacao-tecnica.")
        try:
            config = json.loads(blocks[0])
            if not isinstance(config, dict) or set(config) != {"comandos", "nao_aplicavel"} or not isinstance(config["comandos"], list):
                raise ValueError
            for item in config["comandos"]:
                if (not isinstance(item, dict) or set(item) != {"diretorio", "comando"}
                        or any(not isinstance(item[key], str) or not item[key].strip() or "\x00" in item[key] for key in item)):
                    raise ValueError
            reason = config["nao_aplicavel"]
            if config["comandos"]:
                if reason is not None:
                    raise ValueError
            elif not isinstance(reason, str) or not reason.strip():
                raise ValueError
        except (ValueError, TypeError):
            raise PlanFormatError("Verificação técnica: informe comandos com diretorio/comando, ou uma justificativa explícita em nao_aplicavel; o procedimento não foi inventado.") from None
        task["technical_check"] = config
        found.append(task)
    if required and not found:
        raise PlanFormatError("O PLAN precisa terminar com a task 'Verificação técnica da aplicação' e seu procedimento explícito. Markdown preservado para inspeção.")


def finished(result: dict | None) -> bool:
    return bool(result and result.get("status") in ("sucesso", "nao_aplicavel"))


async def _stop(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    # O shell pode ter terminado deixando um descendente com o pipe aberto.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _python_equivalent(command: str, context: str, directory: Path, env: dict) -> tuple[str, dict] | None:
    # Caso deliberadamente restrito: verificação stdlib de um projeto Python 3.
    # Não inferir equivalência por retorno zero nem reescrever comandos compostos.
    if any(char in command for char in ";|&<>$`\n\r*?[]{}~"):
        return None
    try:
        arguments = shlex.split(command, comments=True)
    except ValueError:
        return None
    start = re.match(r"^([ \t]*)python(?=[ \t])", command)
    versions = re.findall(r"\bPython\s*3(?:\.(\d+))?(?:\.\d+)?\b", context, re.I)
    search_path = os.pathsep.join(str(directory / part) if not os.path.isabs(part) else part
                                 for part in env.get("PATH", os.defpath).split(os.pathsep))
    if (not start or len(arguments) < 3 or arguments[:2] != ["python", "-m"]
            or arguments[2] not in ("compileall", "py_compile")
            or not versions or re.search(r"\bPython\s*2\b", context, re.I)
            or shutil.which("python", path=search_path) is not None):
        return None
    candidate = shutil.which("python3", path=search_path)
    if not candidate:
        return None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            candidate, "-I", "-c", "import json,sys; print(json.dumps(list(sys.version_info[:3])))",
            cwd=directory, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        output, _ = await process.communicate()
        version = json.loads(output)
        minimum_minor = max(int(value or 0) for value in versions)
        if process.returncode or not isinstance(version, list) or len(version) != 3 or any(type(v) is not int for v in version):
            return None
        if version[0] != 3 or version[1] < minimum_minor:
            return None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if process and process.returncode is None:
            await _stop(process)
    # Só trocar o primeiro executável; preservar literalmente todo o restante.
    effective = command[:start.start() + len(start[1])] + shlex.quote(candidate) + command[start.end():]
    return effective, {
        "executavel_original": "python", "executavel_alternativo": candidate,
        "versao_confirmada": ".".join(map(str, version)),
        "argumentos_preservados": arguments[1:],
        "motivo": "python não existe no PATH; Python 3 exigido pelo PLAN foi confirmado no executável alternativo, preservando o módulo e todos os argumentos.",
    }


async def _run(command: str, directory: Path, cwd: Path, env: dict, output: Path, log, safe, attempt: dict) -> None:
    process = None
    attempt.update(comando=command, codigo_retorno=None, saida="", arquivo_saida=str(output.relative_to(cwd)))
    try:
        if not directory.is_relative_to(cwd.resolve()) or directory.is_relative_to(cwd / ".harness"):
            raise OSError("O diretório de execução deve ficar na área da aplicação, fora de .harness.")
        with output.open("xb") as stream:
            os.chmod(output, 0o600)
            process = await asyncio.create_subprocess_exec(
                "bash", "-e", "-o", "pipefail", "-c", command,
                cwd=directory, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while chunk := await process.stdout.read(4096):
                stream.write(chunk)
                stream.flush()
                text = decoder.decode(chunk)
                if text:
                    log(safe(text))
            tail = decoder.decode(b"", final=True)
            if tail:
                log(safe(tail))
            attempt["codigo_retorno"] = await process.wait()
    except asyncio.CancelledError:
        if process:
            await _stop(process)
            attempt["codigo_retorno"] = process.returncode
        raise
    except OSError as error:
        attempt["erro_execucao"] = safe(str(error))
        log(attempt["erro_execucao"])
    finally:
        if process and process.returncode is None:
            await _stop(process)
        attempt["saida"] = safe(output.read_text(encoding="utf-8", errors="replace")) if output.exists() else ""


async def execute(cwd: Path, config: dict, folder: Path, prefix: str, log, safe, *, context: str = "") -> dict:
    result = {"status": "executando", "comandos": [], "motivo": config["nao_aplicavel"]}
    report = folder / f"{prefix}.json"
    if not config["comandos"]:
        result["status"] = "nao_aplicavel"
        write_json(report, result)
        log("Verificação técnica não aplicável: " + config["nao_aplicavel"] + ". Nenhuma compilação foi simulada.")
        return result
    write_json(report, result)
    for index, planned in enumerate(config["comandos"], 1):
        output = folder / f"{prefix}.comando-{index}.log"
        item = {**planned, "codigo_retorno": None, "saida": "", "arquivo_saida": str(output.relative_to(cwd))}
        result["comandos"].append(item)
        log(f"Verificação técnica · comando {index}/{len(config['comandos'])} · {planned['diretorio']}\nPrevisto: {safe(planned['comando'])}")
        directory = (cwd / planned["diretorio"]).resolve()
        env = {**os.environ, "PWD": str(directory), "NO_COLOR": "1"}
        alternate = None
        try:
            await _run(planned["comando"], directory, cwd, env, output, log, safe, item)
            if item["codigo_retorno"] == 127:
                replacement = await _python_equivalent(planned["comando"], context, directory, env)
                if replacement:
                    effective, proof = replacement
                    item.update(comando_previsto=planned["comando"], comando_executado=effective,
                                substituicao=proof, tentativas=[dict(item)])
                    alternate = {}
                    item["tentativas"].append(alternate)
                    write_json(report, result)
                    log(f"Substituição equivalente: {proof['motivo']}\nPrevisto: {safe(planned['comando'])}\nExecutado: {safe(effective)}\nRuntime confirmado: Python {proof['versao_confirmada']}.")
                    await _run(effective, directory, cwd, env, output.with_name(output.stem + ".alternativo.log"), log, safe, alternate)
                    for key in ("codigo_retorno", "saida", "arquivo_saida", "erro_execucao"):
                        if key in alternate:
                            item[key] = alternate[key]
        except asyncio.CancelledError:
            if alternate:
                item.update({key: alternate[key] for key in ("codigo_retorno", "saida", "arquivo_saida")})
            result["status"] = "interrompida"
            write_json(report, result)
            raise
        log(f"Verificação técnica · comando {index}: código de retorno efetivo {item['codigo_retorno']}.")
        write_json(report, result)
    result["status"] = "sucesso" if all(item["codigo_retorno"] == 0 for item in result["comandos"]) else "falha"
    write_json(report, result)
    return result
