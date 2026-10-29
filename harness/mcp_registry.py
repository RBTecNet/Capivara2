"""Instâncias MCP por usuário, independentes do catálogo e da pasta do projeto."""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4


class MCPError(ValueError):
    """Erro que pode ser apresentado na TUI sem expor credenciais."""


@dataclass(frozen=True)
class MCPInstance:
    id: str
    name: str
    url: str
    api_key: str = field(repr=False)
    adapter: str = "doc-center"


def default_registry_path() -> Path:
    override = os.environ.get("CAPIVARA2_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "capivara2" / "mcps.json"


def make_instance(name: str, url: str, api_key: str, *, instance_id: str | None = None,
                  base_dir: Path | None = None) -> MCPInstance:
    name, url, api_key = name.strip(), url.strip(), api_key.strip()
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
        raise MCPError("Informe um nome de recurso com até 120 caracteres.")
    try:
        address = urlsplit(url)
        valid = (address.scheme in ("http", "https") and address.hostname
                 and not address.username and not address.password
                 and not address.query and not address.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in url))
        address.port
    except ValueError:
        valid = False
    if not valid:
        raise MCPError("Informe o endereço HTTP ou HTTPS completo do MCP, sem credenciais, query ou fragmento.")
    if not api_key:
        raise MCPError("Informe a chave API de autenticação.")
    if api_key.startswith("@"):
        path = Path(api_key[1:]).expanduser()
        if not api_key[1:].strip():
            raise MCPError("Informe o caminho do arquivo que contém a chave API após @.")
        if not path.is_absolute():
            path = (base_dir or Path.cwd()) / path
        api_key = f"@{path.resolve()}"
    return MCPInstance(instance_id or str(uuid4()), name, url, api_key)


def resolve_api_key(instance: MCPInstance) -> str:
    reference = instance.api_key
    if reference.startswith("env:"):
        value = os.environ.get(reference[4:], "").strip()
        if not value:
            raise MCPError("A variável de ambiente da chave API não está definida ou está vazia.")
    elif reference.startswith("@"):
        try:
            value = Path(reference[1:]).read_text(encoding="utf-8-sig").strip()
        except (OSError, ValueError):
            raise MCPError("Não foi possível ler o arquivo da chave API. Confira o caminho e a codificação UTF-8.") from None
    else:
        value = reference
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise MCPError("A chave API precisa conter um único token, sem espaços ou quebras de linha.")
    return value


class MCPRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_registry_path()).expanduser().resolve()

    def instances(self) -> tuple[MCPInstance, ...]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, ValueError):
            raise MCPError(f"Não foi possível ler a configuração MCP em {self.path}. O arquivo foi preservado.") from None
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("instances"), list):
            raise MCPError("Formato inválido da configuração MCP. O arquivo foi preservado.")
        instances = []
        ids, names = set(), set()
        for item in data["instances"]:
            if (not isinstance(item, dict)
                    or any(not isinstance(item.get(key), str) for key in ("id", "name", "url", "api_key"))
                    or not item["id"] or item.get("adapter", "doc-center") != "doc-center"):
                raise MCPError("Instância inválida na configuração MCP. O arquivo foi preservado.")
            instance = make_instance(item["name"], item["url"], item["api_key"], instance_id=item["id"], base_dir=self.path.parent)
            if instance.id in ids or instance.name.casefold() in names:
                raise MCPError("Há instâncias MCP duplicadas na configuração. O arquivo foi preservado.")
            ids.add(instance.id)
            names.add(instance.name.casefold())
            instances.append(instance)
        return tuple(instances)

    def check_name(self, candidate: MCPInstance) -> None:
        if any(instance.name.casefold() == candidate.name.casefold() and instance.id != candidate.id
               for instance in self.instances()):
            raise MCPError("Já existe um MCP com esse nome. Escolha outro nome de recurso.")

    def save(self, instance: MCPInstance) -> None:
        self.check_name(instance)
        instances = list(self.instances())
        for index, existing in enumerate(instances):
            if existing.id == instance.id:
                instances[index] = instance
                break
        else:
            instances.append(instance)
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".mcps-", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "instances": [asdict(item) for item in instances]}, output,
                          ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise MCPError("O acesso foi validado, mas não foi possível salvar a configuração MCP. Confira as permissões da pasta.") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
