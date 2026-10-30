"""Catálogo configurável para a TUI; não inicia nem autentica CLIs."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import tomllib

from .domain import Model, PROVIDERS


DEFAULT_CONFIG = Path(__file__).with_name("harness.toml")


@dataclass
class Catalog:
    path: Path
    models: dict[str, tuple[Model, ...]]
    sources: dict[str, str]
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    build_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "Catalog":
        path = path.expanduser().resolve()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Não foi possível carregar o catálogo {path}: {exc}") from exc
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            raise ValueError("A configuração providers deve ser uma tabela TOML.")
        models: dict[str, tuple[Model, ...]] = {}
        sources = {}
        for key in PROVIDERS:
            provider = providers.get(key, {})
            if not isinstance(provider, dict):
                raise ValueError(f"providers.{key} deve ser uma tabela TOML.")
            entries = provider.get("models", [])
            if not isinstance(entries, list):
                raise ValueError(f"providers.{key}.models deve ser uma lista.")
            parsed = []
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(f"Modelo inválido em providers.{key}.")
                model_id = entry.get("id")
                name = entry.get("name", model_id)
                efforts = entry.get("efforts", ["default"])
                if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
                    raise ValueError(f"Modelo sem id válido ou duplicado em providers.{key}.")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"Nome inválido no modelo {key}/{model_id}.")
                if (not isinstance(efforts, list) or not efforts
                        or any(not isinstance(e, str) or not e.strip() for e in efforts)
                        or len(set(efforts)) != len(efforts)):
                    raise ValueError(f"Efforts inválidos no modelo {key}/{model_id}.")
                parsed.append(Model(model_id, name, tuple(efforts)))
                seen.add(model_id)
            models[key] = tuple(parsed)
            sources[key] = "Catálogo configurado · disponibilidade no CLI ainda não verificada"
            if key == "codex" and provider.get("read_local_cache", False):
                cached = read_codex_cache()
                if cached:
                    models[key] = cached
                    sources[key] = "Modelos e efforts do cache local do Codex"
        commands = {}
        agents = data.get("agents", {})
        if not isinstance(agents, dict):
            raise ValueError("agents deve ser uma tabela TOML.")
        for provider, settings in agents.items():
            command = settings.get("command") if isinstance(settings, dict) else None
            if provider not in PROVIDERS or not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
                raise ValueError("Cada agents.<provedor>.command deve conter o programa e seus argumentos em uma lista de strings.")
            commands[provider] = tuple(command)
        build_commands = {}
        build_settings=data.get("build_agents", {})
        if not isinstance(build_settings,dict):raise ValueError("build_agents deve ser uma tabela TOML.")
        for provider, settings in build_settings.items():
            command = settings.get("command") if isinstance(settings, dict) else None
            if provider not in PROVIDERS or not isinstance(command, list) or not command or any(not isinstance(arg,str) or not arg for arg in command):
                raise ValueError("Cada build_agents.<provedor>.command deve ser uma lista explícita de programa e argumentos.")
            build_commands[provider] = tuple(command)
        return cls(path, models, sources, commands, build_commands)


def read_codex_cache() -> tuple[Model, ...]:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        data = json.loads((codex_home / "models_cache.json").read_text(encoding="utf-8"))
        entries = data.get("models", [])
        if not isinstance(entries, list):
            return ()
        models = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("visibility", "list") != "list":
                continue
            model_id = entry.get("slug")
            name = entry.get("display_name", model_id)
            levels = entry.get("supported_reasoning_levels", [])
            if not isinstance(levels, list):
                continue
            efforts = tuple(dict.fromkeys(level["effort"] for level in levels
                                          if isinstance(level, dict) and isinstance(level.get("effort"), str)
                                          and level["effort"].strip()))
            if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
                continue
            models.append(Model(model_id, name if isinstance(name, str) else model_id, efforts or ("default",)))
            seen.add(model_id)
        return tuple(models)
    except (OSError, ValueError, AttributeError):
        return ()
