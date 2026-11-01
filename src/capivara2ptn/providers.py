"""Chamadas textuais independentes pelas CLIs instaladas, sem API direta."""

import asyncio
import json
import os
import re
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

EFFORTS = ("Default", "Low", "Medium", "High", "Extra High")


class ProviderError(Exception):
    def __init__(self, message: str, partial: str = "") -> None:
        super().__init__(message)
        self.partial = partial


@dataclass(frozen=True)
class Selection:
    provider: str
    model: str
    effort: str


def command(selection: Selection, access: str = "text", cwd: Path | None = None) -> tuple[list[str], dict[str, str]]:
    executable = shutil.which(selection.provider)
    if not executable:
        raise ProviderError(f"CLI {selection.provider} não encontrada no PATH.")
    levels = {"Default": None, "Low": "low", "Medium": "medium", "High": "high", "Extra High": "xhigh"}
    if selection.effort not in levels:
        raise ProviderError("Effort não reconhecido.")
    effort = levels[selection.effort]
    env = {**os.environ, "NO_COLOR": "1"}
    if selection.provider == "codex":
        argv = [executable, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                "--json", "--ephemeral", "--color", "never", "-m", selection.model,
                "-c", "mcp_servers={}", "-c", "web_search=\"disabled\"",
                "--disable", "apps", "--disable", "multi_agent", "--disable", "shell_tool",
                "--disable", "code_mode_host", "--disable", "default_mode_request_user_input"]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv += ["-"]
    elif selection.provider == "claude":
        argv = [executable, "--print", "--output-format", "stream-json", "--verbose",
                "--model", selection.model, "--tools", "", "--permission-mode", "plan",
                "--permission-prompts", "none", "--strict-mcp-config", "--mcp-config",
                '{"mcpServers":{}}', "--no-session-persistence", "--safe-mode"]
        if effort:
            argv += ["--effort", effort]
    elif selection.provider == "agy":
        settings = {"toolPermission": "strict", "permissions": {"deny": [
            "read_file(*)", "write_file(*)", "command(*)", "mcp(*)", "read_url(*)", "execute_url(*)",
        ]}}
        argv = [executable, "--input-format", "stream-json", "--output-format", "stream-json", "--model", selection.model,
                "--mode", "plan", "--disable-slash-commands", "--settings", json.dumps(settings)]
        if effort:
            argv += ["--effort", effort]
    elif selection.provider == "opencode":
        model = selection.model
        if effort:
            model = model.split("#", 1)[0] + "#" + effort
        argv = [executable, "run", "--standalone", "--format", "json", "--agent", "plan", "--model", model]
        try:
            settings = json.loads(env.get("OPENCODE_CONFIG_CONTENT", "{}"))
            settings["permissions"] = [*settings.get("permissions", []), {"action": "*", "resource": "*", "effect": "deny"}]
            settings["policies"] = [*settings.get("policies", []), {"action": "tool.use", "resource": "*", "effect": "deny"}]
            settings["snapshots"] = False
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(settings)
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("A configuração inline do OpenCode é inválida.") from None
    else:
        raise ProviderError("Provedor não suportado pelo INIT.")
    if access not in ("text", "read", "write"):
        raise ProviderError("Modo de acesso da CLI inválido.")
    if access != "text":
        if selection.provider == "codex":
            argv[argv.index("--sandbox") + 1] = "workspace-write" if access == "write" else "read-only"
            index = argv.index("shell_tool")
            argv[index - 1] = "--enable"
            index = argv.index("code_mode_host")
            argv[index - 1] = "--enable"
            argv[-1:-1] = ["-c", 'approval_policy="never"']
        elif selection.provider == "claude":
            tools = "Read,Glob,Grep,Edit,Write,Bash" if access == "write" else "Read,Glob,Grep"
            argv[argv.index("--tools") + 1] = tools
            argv[argv.index("--permission-mode") + 1] = "acceptEdits" if access == "write" else "plan"
            argv += ["--allowedTools", tools, "--disallowedTools", "Agent,Task,AskUserQuestion"]
            if access == "write":
                argv += ["--settings", json.dumps({"permissions": {"deny": ["Edit(./.harness/**)"]}})]
        elif selection.provider == "agy":
            settings = {"toolPermission": "always-proceed" if access == "write" else "request-review", "permissions": {
                "allow": ["read_file(*)"] + (["write_file(*)", "command(*)"] if access == "write" else []),
                "deny": ["mcp(*)", "read_url(*)", "execute_url(*)"] +
                        (["write_file(.harness/)", f"write_file({cwd / '.harness'}/)"] if access == "write" and cwd else
                         ["write_file(.harness/)"] if access == "write" else ["write_file(*)", "command(*)"]),
            }}
            argv[argv.index("--mode") + 1] = "accept-edits" if access == "write" else "plan"
            argv[argv.index("--settings") + 1] = json.dumps(settings)
            if access == "write":
                argv += ["--dangerously-skip-permissions"]
        elif selection.provider == "opencode":
            argv[argv.index("--agent") + 1] = "build" if access == "write" else "plan"
            argv += ["--auto"]
            # Retirar somente as duas regras textuais adicionadas acima.
            settings = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            settings["permissions"].pop()
            previous_permissions = settings["permissions"]
            settings["policies"].pop()
            actions = ["read", "glob", "grep", "list", "execute"] + (["edit", "shell"] if access == "write" else [])
            settings["permissions"] = [{"action": "*", "resource": "*", "effect": "deny"}]
            settings["permissions"] += [{"action": action, "resource": "*", "effect": "allow"} for action in actions]
            settings["permissions"] += previous_permissions
            settings["permissions"] += [{"action": action, "resource": "*", "effect": "deny"}
                                        for action in ("question", "subagent")]
            if access == "write":
                settings["permissions"] += [{"action": "edit", "resource": pattern, "effect": "deny"}
                    for pattern in (".harness/*", str(cwd / ".harness/*") if cwd else "*/.harness/*")]
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(settings)
    return argv, env


class CliRunner:
    def __init__(self, selection: Selection, cwd: Path, log: Callable[[str], None], secrets: list[str] | None = None, *, forbid_automated_tests: bool = False) -> None:
        self.selection = selection
        self.cwd = cwd
        self.log = log
        self.forbid_automated_tests = forbid_automated_tests
        self.secrets = [value for value in (secrets or []) if value]
        self.secrets += [value for key, value in os.environ.items() if value and re.search(r"(?:TOKEN|API_KEY|SECRET|PASSWORD)$", key)]

    def safe(self, text: str) -> str:
        for value in sorted(set(self.secrets), key=len, reverse=True):
            text = text.replace(value, "[OCULTO]")
        text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [OCULTO]", text)
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)

    async def invoke(self, instructions: str, data: dict[str, object], *, access: str = "text") -> str:
        argv, env = command(self.selection, access, self.cwd)
        if access != "text":
            # Equivalente ao cd do Bash também para CLIs que consultam $PWD.
            env["PWD"] = str(self.cwd.resolve())
        restriction = ""
        if self.forbid_automated_tests:
            restriction = (
                "\n\nRestrição desta execução: não crie arquivos, suítes ou tarefas de testes automatizados "
                "e não remova testes existentes. Compilação, typecheck e conferências pontuais sem criar suítes "
                "são permitidos. Não acrescente esta restrição como funcionalidade do produto."
            )
        payload = instructions + restriction + "\n\n---\nDADOS DE ENTRADA (JSON)\n" + json.dumps(data, ensure_ascii=False)
        result = ""
        failed = False
        text_parts: dict[str, str] = {}
        errors: list[str] = []

        def emit(text: str) -> None:
            if text:
                self.log(self.safe(text))

        def consume(event: dict) -> None:
            nonlocal result, failed
            if self.selection.provider == "agy":
                # Agy usa event/step_update/result, não o protocolo do Claude.
                kind = event.get("event")
                if kind == "init":
                    emit("agy: init")
                elif kind == "step_update":
                    step = event.get("step_update", {})
                    if access == "text" and (step.get("step_type") == "tool" or step.get("subagent_info")):
                        raise ProviderError("A CLI tentou utilizar ferramentas. O INIT aceita somente respostas textuais.", result)
                    if step.get("step_type") == "tool":
                        emit("agy: " + str(step.get("tool_name") or "ferramenta"))
                    delta = step.get("text_delta")
                    if step.get("step_type") == "agent_response" and isinstance(delta, str):
                        key = str(step.get("step_index", 0))
                        text_parts[key] = text_parts.get(key, "") + delta
                        result = "".join(text_parts.values())
                        emit(delta)
                elif kind == "result":
                    final = event.get("result", {})
                    if final.get("status") != "SUCCESS":
                        failed = True
                        errors.append(self.safe(str(final.get("error") or "Agy informou falha.")))
                    elif isinstance(final.get("response"), str):
                        final_text = final["response"]
                        if final_text != result:
                            emit(final_text)
                        result = final_text
                return
            kind = event.get("type", "")
            item = event.get("item") or {}
            part = event.get("part") or {}
            message = event.get("message") or {}
            blocks = message.get("content", []) if isinstance(message, dict) else []
            for block in blocks if isinstance(blocks, list) else []:
                if access == "text" and isinstance(block, dict) and block.get("type") == "tool_use":
                    raise ProviderError("A CLI tentou utilizar ferramentas. O INIT aceita somente respostas textuais.", result)
            if access == "text" and kind == "item.started" and item.get("type") in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
                raise ProviderError("A CLI tentou utilizar ferramentas. O INIT aceita somente respostas textuais.", result)
            if access == "text" and (part.get("type") == "tool" or kind == "tool_use"):
                raise ProviderError("A CLI tentou utilizar ferramentas. O INIT aceita somente respostas textuais.", result)
            if access != "text":
                if kind in ("item.started", "item.completed") and item.get("type") in ("command_execution", "file_change"):
                    emit(str(item.get("command") or item.get("changes") or item.get("type")))
                    emit(str(item.get("aggregated_output") or ""))
                for block in blocks if isinstance(blocks, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        emit(str(block.get("name", "ferramenta")))
                if part.get("type") == "tool":
                    emit(str(part.get("tool", "ferramenta")))
            if kind == "item.completed" and item.get("type") == "agent_message":
                result = item.get("text", "")
                emit(result)
            elif kind == "assistant":
                text = "\n".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
                if text:
                    result = text
                    emit(text)
            elif kind == "result":
                if event.get("is_error") or str(event.get("subtype", "")).startswith("error"):
                    failed = True
                    errors.append(self.safe(str(event.get("result") or event.get("errors") or "A CLI informou falha.")))
                elif isinstance(event.get("result"), str):
                    result = event["result"]
                    if result:
                        emit(result)
            elif kind == "text" and isinstance(part.get("text"), str):
                text_parts[part.get("id", str(len(text_parts)))] = part["text"]
                result = "\n".join(text_parts.values())
                emit(part["text"])
            elif kind in ("error", "turn.failed"):
                failed = True
                errors.append(self.safe(str(event.get("error") or event.get("message") or "A CLI informou falha.")))
            elif kind in ("thread.started", "turn.started", "system", "step_start", "step_finish"):
                emit(f"{self.selection.provider}: {kind}")
            elif kind == "item.completed" and item.get("type") == "reasoning":
                emit(item.get("text", ""))

        try:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=self.cwd, env=env, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024,
            )
        except OSError:
            raise ProviderError("Não foi possível iniciar a CLI selecionada.") from None

        async def feed() -> None:
            try:
                wire = json.dumps({"event": "user", "message": {"content": payload}}, ensure_ascii=False) + "\n" if self.selection.provider == "agy" else payload
                process.stdin.write(wire.encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        async def stdout() -> None:
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    emit(line)
                    continue
                if isinstance(event, dict):
                    consume(event)

        async def stderr() -> None:
            async for raw in process.stderr:
                text = self.safe(raw.decode("utf-8", errors="replace").strip())
                if text:
                    emit(text)
                    errors.append(text)
                    del errors[:-10]

        tasks = [asyncio.create_task(feed()), asyncio.create_task(stdout()), asyncio.create_task(stderr())]
        try:
            await asyncio.gather(*tasks)
            code = await process.wait()
            # Mensagens informativas no stderr não são, sozinhas, uma falha.
            if code != 0 or failed:
                detail = "\n".join(errors[-5:])
                reason = f"encerrou com código {code}" if code else "informou falha"
                raise ProviderError(f"{self.selection.provider} {reason}.\n{detail}".strip(), result)
            if not result.strip():
                raise ProviderError("A CLI não devolveu uma resposta textual final.\n" + "\n".join(errors[-5:]), result)
            return result.strip()
        except (ValueError, TypeError, AttributeError) as error:
            raise ProviderError("A saída da CLI não corresponde ao formato esperado.", result) from error
        except OSError as error:
            raise ProviderError("Falha de comunicação com a CLI selecionada.", result) from error
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
