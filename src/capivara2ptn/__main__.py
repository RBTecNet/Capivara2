"""Entry point; o diretório de trabalho pertence a quem iniciou a aplicação."""

from capivara2ptn.app import CapivaraApp


def main() -> None:
    CapivaraApp().run()


if __name__ == "__main__":
    main()
