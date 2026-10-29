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
    # Perfil: corpo alongado, orelha pequena e arredondada, focinho achatado.
    outline = ((3, 13), (2, 10), (3, 7), (6, 5), (14, 5), (18, 6),
               (19, 4), (20, 2), (22, 2), (23, 4), (27, 4), (29, 5),
               (31, 5), (32, 7), (32, 11), (30, 13), (24, 13),
               (23, 17), (20, 18), (19, 15), (12, 15), (12, 18),
               (8, 18), (8, 15), (5, 15))

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
    palette = ("#a1723e", "#a87843", "#b17e46", "#9b6b39", "#ad7940")
    for y in range(height - 1):
        for x in range(width):
            neighbors = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if (x, y) in mask:
                rows[y][x] = "#493b2c" if any(point not in mask for point in neighbors) else palette[(x * 13 + y * 7) % len(palette)]
            elif any(point in mask for point in neighbors):
                rows[y][x] = "#d93684"
    for y in (3, 4):
        rows[y][21] = "#60452c"
    rows[6][25] = "#293026"
    rows[7][25] = "#1e251e"
    rows[6][26] = "#d5ab65"
    for y in range(8, 12):
        for x in range(26, 32):
            if (x, y) in mask:
                rows[y][x] = "#b58a51" if y < 10 else "#a97b44"
    rows[9][31] = "#293026"
    for x in range(28, 32):
        rows[11][x] = "#65482c"
    for x in range(6, 21):
        if (x, 14) in mask:
            rows[14][x] = "#825a33"
    for x in (9, 10, 21, 22):
        rows[17][x] = "#51402a"
    for x in range(3, width - 1):
        rows[-1][x] = "#09b8d4" if x % 4 else BACKGROUND
    return pixel_text(rows)
