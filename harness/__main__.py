import argparse
from pathlib import Path

from .app import HarnessApp
from .catalog import Catalog, DEFAULT_CONFIG
from .mcp_registry import MCPRegistry
from .mascot import prepare_mascot


def main() -> None:
    parser = argparse.ArgumentParser(description="Wizard de inicialização do Capivara2 em TUI.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Arquivo TOML de modelos e efforts.")
    parser.add_argument("--mcp-config", type=Path, help="Arquivo de instâncias MCP (padrão: configuração do usuário).")
    args = parser.parse_args()
    try:
        catalog = Catalog.load(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    prepare_mascot()
    HarnessApp(catalog=catalog, mcp_registry=MCPRegistry(args.mcp_config)).run()


if __name__ == "__main__":
    main()
