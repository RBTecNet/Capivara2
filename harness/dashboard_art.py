"""Marca e mascote em blocos de terminal, sem dependências gráficas."""

from pathlib import Path

from rich.console import Console
from rich.text import Text


BACKGROUND = "#0b1720"
FONT = {
    "C": ("01111", "11000", "11000", "11000", "11000", "11000", "01111"),
    "A": ("01110", "11011", "11011", "11111", "11011", "11011", "11011"),
    "P": ("11110", "11011", "11011", "11110", "11000", "11000", "11000"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "V": ("11011", "11011", "11011", "11011", "11011", "01110", "00100"),
    "R": ("11110", "11011", "11011", "11110", "11100", "11010", "11011"),
}


def pixel_text(rows: list[list[str]]) -> Text:
    text = Text()
    width = len(rows[0])
    for y in range(0, len(rows), 2):
        if y:
            text.append("\n")
        for x in range(width):
            top = rows[y][x]
            bottom = rows[y + 1][x] if y + 1 < len(rows) else BACKGROUND
            text.append("▀", style=f"{top} on {bottom}")
    return text


def wordmark() -> Text:
    colors = ("#21d8ee", "#14d0e7", "#0ac6df", "#05bfd8", "#00b6d1", "#00a9c5", "#009bb7")
    rows = []
    for index in range(7):
        bits = "0".join(FONT[letter][index] for letter in "CAPIVARA")
        rows.append([colors[index] if bit == "1" else BACKGROUND for bit in bits])
    return pixel_text(rows)


MASCOT_ANSI = Path(__file__).with_name("assets") / "build-busto-40x13.ans"


def capybara(max_width: int = 34, max_height: int = 10) -> Text:
    """Ajusta a arte ANSI fornecida à área do cabeçalho, preservando suas cores."""
    source = Text.from_ansi(MASCOT_ANSI.read_text(encoding="utf-8").rstrip("\n"))
    lines = source.split("\n")
    width = max(line.cell_len for line in lines)
    height = len(lines)
    scale = min(1.0, max_width / width, max_height / height)
    if scale == 1.0:
        return source
    target_width = max(1, min(max_width, round(width * scale)))
    target_height = max(1, min(max_height, round(height * scale)))
    console = Console()
    fitted = Text()
    # A arte original usa somente caracteres de uma célula. Amostragem central
    # mantém o enquadramento completo e os estilos RGB/inversos, sem cortes.
    for y in range(target_height):
        if y:
            fitted.append("\n")
        line = lines[min(height - 1, int((y + 0.5) * height / target_height))]
        for x in range(target_width):
            offset = min(width - 1, int((x + 0.5) * width / target_width))
            character = line.plain[offset] if offset < len(line.plain) else " "
            fitted.append(character, style=line.get_style_at_offset(console, offset))
    return fitted
