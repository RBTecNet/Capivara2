from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness import mascot


class MascotProtocolTests(unittest.TestCase):
    def select(self, protocol):
        sixel_renderable = type("SixelRenderable", (), {})
        kitty_renderable = type("KittyRenderable", (), {})
        fallback = type("HalfcellRenderable", (), {})
        renderables = ModuleType("textual_image.renderable")
        renderables.Image = {"sixel": sixel_renderable, "kitty": kitty_renderable, "blocks": fallback}[protocol]
        sixel = ModuleType("textual_image.renderable.sixel")
        sixel.Image = sixel_renderable
        kitty = ModuleType("textual_image.renderable.tgp")
        kitty.Image = kitty_renderable
        widgets = ModuleType("textual_image.widget")
        widgets.SixelImage = type("SixelWidget", (), {})
        widgets.TGPImage = type("KittyWidget", (), {})
        modules = {"textual_image.renderable": renderables, "textual_image.renderable.sixel": sixel,
                   "textual_image.renderable.tgp": kitty, "textual_image.widget": widgets}
        with patch.multiple(mascot, _prepared=False, _protocol="blocks", _image_widget=None), \
                patch.object(mascot.sys, "__stdin__", SimpleNamespace(isatty=lambda: True)), \
                patch.object(mascot.sys, "__stdout__", SimpleNamespace(isatty=lambda: True)), \
                patch.dict("sys.modules", modules):
            result = mascot.prepare_mascot()
            selected = mascot._image_widget
            # Uma nova chamada reaproveita a negociação, sem sondar stdin de novo.
            with patch.object(mascot.sys, "__stdin__", None):
                self.assertEqual(mascot.prepare_mascot(), result)
            return result, selected, widgets

    def test_native_protocols_select_png_renderer_and_unsupported_uses_our_art(self):
        for protocol, name in (("sixel", "SixelImage"), ("kitty", "TGPImage")):
            with self.subTest(protocol=protocol):
                result, selected, widgets = self.select(protocol)
                self.assertEqual(result, protocol)
                self.assertIs(selected, getattr(widgets, name))
        result, selected, _ = self.select("blocks")
        self.assertEqual(result, "blocks")
        self.assertIsNone(selected)

    def test_noninteractive_startup_does_not_query_or_read_terminal(self):
        stdin, stdout = Mock(), Mock()
        stdin.isatty.return_value = False
        stdout.isatty.return_value = True
        with patch.multiple(mascot, _prepared=False, _protocol="blocks", _image_widget=None), \
                patch.object(mascot.sys, "__stdin__", stdin), patch.object(mascot.sys, "__stdout__", stdout):
            self.assertEqual(mascot.prepare_mascot(), "blocks")
        stdin.read.assert_not_called()
        stdin.buffer.read.assert_not_called()
        stdout.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
