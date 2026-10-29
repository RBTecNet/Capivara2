import argparse
from pathlib import Path

from .app import HarnessApp
from .catalog import Catalog, DEFAULT_CONFIG


def main() -> None:
    parser = argparse.ArgumentParser(description="Wizard de inicialização do Capivara2 em TUI.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Arquivo TOML de modelos e efforts.")
    args = parser.parse_args()
    try:
        catalog = Catalog.load(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    HarnessApp(catalog=catalog).run()


if __name__ == "__main__":
    main()
