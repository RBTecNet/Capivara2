from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static
from textual_image.widget import Image

class Capivara(App):
    CSS = """
    Screen {
        background: #101820;
    }

    #cabecalho {
        height: 5;
        align: left middle;
        padding: 0 1;
    }

    #mascote {
        width: 10;
        height: 4;
    }

    #titulo {
        width: auto;
        height: auto;
        content-align: left middle;
        text-style: bold;
        color: #ffffff;
        padding-left: 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="cabecalho"):
            yield Image("assets/mascote.png", id="mascote")
            yield Static("CAPIVARA", id="titulo")


if __name__ == "__main__":
    Capivara().run()
