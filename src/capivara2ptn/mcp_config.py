"""Cadastro global privado de endpoints MCP, separado dos projetos."""

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4


class ConfigError(Exception):
    """Erro seguro para apresentação; nunca inclui valores de credenciais."""


@dataclass(frozen=True)
class McpServer:
    id: str
    name: str
    url: str
    token: str = field(default="", repr=False)


def make_server(name: str, url: str, token: str = "", identifier: str | None = None) -> McpServer:
    name, url, token = name.strip(), url.strip(), token.strip()
    if not name or not all(char.isprintable() for char in name):
        raise ConfigError("Informe um nome de identificação válido.")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "?" in url or "#" in url or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii")
        host = f"[{host}]" if ":" in host else host
        authority = host + (f":{parsed.port}" if parsed.port is not None else "")
        path = quote(parsed.path if parsed.path not in ("", "/") else "/mcp", safe="/%:@-._~!$&'()*+,;=")
        url = urlunsplit((parsed.scheme, authority, path, "", ""))
    except (ValueError, UnicodeError):
        raise ConfigError("Use uma URL HTTP ou HTTPS, sem usuário, senha, query ou fragmento.") from None
    if token and not all(33 <= ord(char) <= 126 for char in token):
        raise ConfigError("Informe somente o token, sem prefixo Bearer, espaços ou quebras de linha.")
    return McpServer(identifier or uuid4().hex, name, url, token)


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        base = Path.home() / ".config"
    return base / "capivara2ptn" / "mcp.json"


def _private_directory(path: Path, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ConfigError("O diretório de configuração MCP deve ser privado e não pode ser um link.")
    path.chmod(0o700)


def load_servers() -> list[McpServer]:
    path = config_path()
    try:
        if not path.parent.exists() and not path.parent.is_symlink():
            return []
        _private_directory(path.parent)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ConfigError("O cadastro MCP deve ser um arquivo regular privado.")
            os.fchmod(stream.fileno(), 0o600)
            contents = stream.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise ValueError
            payload = json.loads(contents)
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("servers"), list):
            raise ValueError
        servers = []
        for item in payload["servers"]:
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("id", "name", "url", "token")) or not item["id"]:
                raise ValueError
            servers.append(make_server(item["name"], item["url"], item["token"], item["id"]))
        if len({server.id for server in servers}) != len(servers) or len({server.name.casefold() for server in servers}) != len(servers):
            raise ValueError
        return servers
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError, ConfigError):
        raise ConfigError("Não foi possível ler o cadastro MCP. Verifique o formato, os links e as permissões de mcp.json.") from None


def _save_servers(servers: list[McpServer]) -> None:
    path = config_path()
    temporary = None
    try:
        _private_directory(path.parent, create=True)
        if path.is_symlink():
            raise ConfigError("O cadastro MCP não pode ser um link.")
        descriptor, temporary = tempfile.mkstemp(prefix=".mcp-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": 1, "servers": [
                {"id": server.id, "name": server.name, "url": server.url, "token": server.token}
                for server in servers
            ]}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, ConfigError):
        raise ConfigError("Não foi possível salvar o cadastro MCP. Verifique as permissões da configuração global.") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def add_server(server: McpServer) -> list[McpServer]:
    servers = load_servers()
    if any(item.name.casefold() == server.name.casefold() for item in servers):
        raise ConfigError("Já existe um servidor com esse nome. Escolha outro nome.")
    servers.append(server)
    _save_servers(servers)
    return servers


def remove_server(identifier: str) -> list[McpServer]:
    servers = [server for server in load_servers() if server.id != identifier]
    _save_servers(servers)
    return servers
