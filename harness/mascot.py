"""PNG original em terminais gráficos; arte em blocos nos demais."""

import logging
from pathlib import Path
import sys

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from .dashboard_art import capybara


MASCOT_PNG = Path(__file__).with_name("assets") / "capivara-engineer.png"
_image_widget: type[Widget] | None = None
_protocol = "blocks"
_prepared = False


def prepare_mascot() -> str:
    """Negocia gráficos antes de o Textual assumir a leitura do terminal."""
    global _image_widget, _protocol, _prepared
    if _prepared:
        return _protocol
    _prepared = True
    if not (sys.__stdin__ and sys.__stdout__ and sys.__stdin__.isatty() and sys.__stdout__.isatty()):
        return _protocol
    logger = logging.getLogger("textual_image")
    previous_level = logger.level
    try:
        # Falta de resposta gráfica é normal e deve selecionar o fallback.
        logger.setLevel(logging.ERROR)
        from textual_image.renderable import Image as AutoImage
        from textual_image.renderable.sixel import Image as SixelRenderable
        from textual_image.renderable.tgp import Image as KittyRenderable

        if AutoImage is SixelRenderable:
            from textual_image.widget import SixelImage
            _image_widget, _protocol = SixelImage, "sixel"
        elif AutoImage is KittyRenderable:
            from textual_image.widget import TGPImage
            _image_widget, _protocol = TGPImage, "kitty"
    except Exception:
        # Uma decoração incompatível não deve impedir o uso do harness.
        _image_widget, _protocol = None, "blocks"
    finally:
        logger.setLevel(previous_level)
    return _protocol


class Mascot(Container):
    DEFAULT_CSS = """
    Mascot { width: 36; height: 10; align: center middle; }
    Mascot .mascot-png { width: auto; height: 100%; max-width: 100%; }
    Mascot .mascot-blocks { width: 34; height: 10; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.image_protocol = _protocol

    def compose(self) -> ComposeResult:
        if _image_widget is not None:
            try:
                image = _image_widget(MASCOT_PNG, classes="mascot-png")
                image.tooltip = f"Capivara · PNG original · {self.image_protocol}"
                yield image
                return
            except (OSError, ValueError):
                self.image_protocol = "blocks"
        fallback = Static(capybara(), classes="mascot-blocks", name="Capivara em blocos")
        fallback.tooltip = "Capivara em blocos · fallback para terminais sem imagens nativas"
        yield fallback
