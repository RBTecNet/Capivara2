"""Adaptadores CLI documentais. Nenhuma sondagem de ferramentas do ambiente."""

import asyncio
import json
import os
from pathlib import Path
import signal

from .artifacts import ArtifactStore, InitError
from .domain import AgentSelection, PROVIDERS


class AgentError(InitError):
    pass


class CLIAgents:
    def __init__(self, commands: dict[str, tuple[str, ...]] | None = None, *, timeout: float = 600) -> None:
        self.commands = commands or {}
        self.timeout = timeout

    def command(self, selection: AgentSelection, schema: dict, output: Path, schema_file: Path,
                store: ArtifactStore, env: dict) -> tuple[list[str], str]:
        provider = selection.provider
        if provider in self.commands:
            return list(self.commands[provider]), "custom"
        if provider == "codex":
            command = ["codex", "--no-daemon", "exec", "--json", "--ephemeral", "--ignore-user-config",
                       "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only",
                       "--output-schema", str(schema_file), "--output-last-message", str(output),
                       "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                       "--enable", "skip_host_skill_discovery"]
            for feature in ("shell_tool", "apps", "multi_agent", "hooks", "plugins", "browser_use",
                            "computer_use", "image_generation"):
                command.extend(["--disable", feature])
            if selection.model != "default":
                command.extend(["--model", selection.model])
            if selection.effort != "default":
                command.extend(["-c", f"model_reasoning_effort={json.dumps(selection.effort)}"])
            return command + ["-"], "codex"
        if provider == "claude":
            command = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                       "--tools", "", "--disallowedTools", "*", "--strict-mcp-config",
                       "--mcp-config", '{"mcpServers":{}}', "--settings", '{"disableAllHooks":true}',
                       "--setting-sources", "", "--no-session-persistence"]
            if selection.model != "default":
                command.extend(["--model", selection.model])
            if selection.effort != "default":
                command.extend(["--effort", selection.effort])
            return command, "claude"
        if provider == "opencode":
            # Agente V2 dedicado; as permissões impedem comandos, leitura e escrita.
            store.json("control/work/.opencode/opencode.json", {
                "agents": {"capivara-documentation": {"mode": "primary", "system": "Gere somente o JSON documental solicitado, sem ferramentas.",
                            "permissions": [{"action": "*", "resource": "*", "effect": "deny"}]}},
                "policies": [{"action": "tool.use", "resource": "*", "effect": "deny"}]})
            command = ["opencode", "run", "--standalone", "--format", "json", "--agent", "capivara-documentation"]
            if selection.model != "default":
                model = selection.model
                if selection.effort != "default":
                    model = model.split("#", 1)[0] + "#" + selection.effort
                command.extend(["--model", model])
            elif selection.effort != "default":
                raise AgentError("Selecione um modelo explícito do OpenCode para aplicar um effort.")
            return command, "opencode"
        raise AgentError(f"Configure agents.{provider}.command no catálogo para usar o CLI {PROVIDERS.get(provider, provider)} no init documental.")

    async def generate(self, selection: AgentSelection, role: str, prompt: str, schema: dict,
                       store: ArtifactStore, call_id: int) -> str:
        prefix = f"log/{role}-{call_id:04d}"
        store.write(prefix + ".input.txt", prompt)
        store.json(prefix + ".schema.json", schema)
        output = store.path(prefix + ".last.txt")
        work = store.path("control/work")
        work.mkdir(parents=True, mode=0o700, exist_ok=True)
        env = dict(os.environ, CAPIVARA2_ROLE=role, CAPIVARA2_MODEL=selection.model,
                   CAPIVARA2_EFFORT=selection.effort, CAPIVARA2_SCHEMA=str(store.path(prefix + ".schema.json")),
                   CAPIVARA2_OUTPUT=str(output), NO_COLOR="1")
        command, mode = self.command(selection, schema, output, store.path(prefix + ".schema.json"), store, env)
        try:
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=work, env=env,
                        start_new_session=True, limit=8 * 1024 * 1024)
        except OSError as exc:
            raise AgentError(f"Não foi possível iniciar {PROVIDERS[selection.provider]}: {exc.strerror or 'falha ao iniciar o CLI'}.") from None

        async def stop():
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (ProcessLookupError, TimeoutError):
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        await process.wait()

        stdout_parts, stderr_parts = [], []

        async def collect(stream, filename: str, parts: list[bytes]):
            path = store.path(filename)
            descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as log:
                while chunk := await stream.read(8192):
                    parts.append(chunk)
                    log.write(chunk)
                    log.flush()

        async def communicate():
            async with asyncio.TaskGroup() as group:
                group.create_task(collect(process.stdout, prefix + ".stdout.log", stdout_parts))
                group.create_task(collect(process.stderr, prefix + ".stderr.log", stderr_parts))
                process.stdin.write(prompt.encode("utf-8"))
                try:
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                process.stdin.close()
                await process.wait()

        try:
            await asyncio.wait_for(communicate(), timeout=self.timeout)
        except asyncio.CancelledError:
            await stop()
            raise
        except TimeoutError:
            await stop()
            raise AgentError(f"{PROVIDERS[selection.provider]} excedeu o tempo de resposta. Os logs foram preservados em .harness/log.") from None
        except Exception:
            await stop()
            raise AgentError("Falha na comunicação com o CLI. Consulte os logs em .harness/log.") from None
        stdout = b"".join(stdout_parts).decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise AgentError(f"{PROVIDERS[selection.provider]} encerrou com código {process.returncode}. Consulte .harness/{prefix}.stderr.log.")
        if mode == "codex":
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item", {})
                if item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                    raise AgentError("O agente tentou usar ferramentas durante o init documental. A execução não foi aprovada.")
            if not output.is_file():
                raise AgentError("O Codex não entregou uma resposta final. Consulte os logs da chamada.")
            return output.read_text(encoding="utf-8")
        if mode == "claude":
            try:
                result = json.loads(stdout)
                if result.get("is_error"):
                    raise ValueError()
                structured = result.get("structured_output")
                return json.dumps(structured, ensure_ascii=False) if isinstance(structured, dict) else result["result"]
            except (ValueError, KeyError, TypeError):
                raise AgentError("O Claude não entregou uma resposta documental válida. Consulte os logs.") from None
        if mode == "opencode":
            parts = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") in ("tool_use", "tool_call"):
                    raise AgentError("O OpenCode tentou usar ferramentas durante o init documental.")
                part = event.get("part", {})
                if event.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif event.get("type") == "error":
                    raise AgentError("O OpenCode informou uma falha. Consulte os logs da chamada.")
            return "\n".join(parts) if parts else stdout
        return stdout
