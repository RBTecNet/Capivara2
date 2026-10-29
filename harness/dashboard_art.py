"""Marca e mascote em blocos de terminal, sem dependências gráficas."""

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


def capybara() -> Text:
    width, height = 34, 20
    rows = [[BACKGROUND] * width for _ in range(height)]
    outline = ((7, 18), (6, 15), (7, 11), (9, 8), (11, 5), (11, 1), (14, 1),
               (16, 4), (23, 4), (25, 1), (28, 1), (28, 7), (30, 11), (31, 18))

    def inside(x, y):
        contained = False
        previous = outline[-1]
        for current in outline:
            x1, y1 = previous
            x2, y2 = current
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                contained = not contained
            previous = current
        return contained

    mask = {(x, y) for y in range(height - 1) for x in range(width) if inside(x + 0.5, y + 0.5)}
    palette = ("#9c6938", "#a9743c", "#b27c40", "#986333", "#ad773b")
    for y in range(height - 1):
        for x in range(width):
            neighbors = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if (x, y) in mask:
                rows[y][x] = "#493b2c" if any(point not in mask for point in neighbors) else palette[(x * 13 + y * 7) % len(palette)]
            elif any(point in mask for point in neighbors):
                rows[y][x] = "#d93684"
    for x in (12, 26):
        for y in (2, 3):
            rows[y][x] = "#513a2b"
    for x in (13, 25):
        for y in (7, 8):
            rows[y][x] = "#1d2926"
        rows[7][x] = "#e8bd70"
    for y in range(9, 18):
        for x in range(13, 27):
            if ((x - 19.5) / 7) ** 2 + ((y - 13) / 5) ** 2 <= 1:
                rows[y][x] = "#bd8e50" if (x + y) % 4 else "#c99b5c"
    for x in range(17, 24):
        rows[10][x] = "#785532"
    for x in (17, 23):
        for y in (11, 12):
            rows[y][x] = "#342f23"
    for y in range(12, 17):
        rows[y][20] = "#795634"
    for x in range(16, 25):
        rows[16][x] = "#765331"
    for x in range(3, width - 1):
        rows[-1][x] = "#09b8d4" if x % 4 else BACKGROUND
    for x, y in ((2, 8), (3, 8), (1, 12), (3, 12), (31, 7), (32, 7), (32, 11)):
        rows[y][x] = "#e74398"
    return pixel_text(rows)
