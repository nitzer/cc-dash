"""Lienzo de caracteres y gráfico de actividad."""

from collections import Counter

from rich.text import Text

from .text import ktok, trunc
from .theme import C, blend


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.st = [[""] * w for _ in range(h)]

    def put(self, x, y, text, style=""):
        if not 0 <= y < self.h:
            return
        for i, c in enumerate(text):
            if 0 <= x + i < self.w:
                self.ch[y][x + i] = c
                self.st[y][x + i] = style

    def tint(self, cells, style):
        """Cambia el color de celdas ya dibujadas sin tocar sus caracteres."""
        for x, y in cells:
            if 0 <= y < self.h and 0 <= x < self.w:
                self.st[y][x] = style

    def box(self, x, y, w, h, style, heavy=False):
        tl, tr, bl, br, hz, vt = "┏┓┗┛━┃" if heavy else "┌┐└┘─│"
        self.put(x, y, tl + hz * (w - 2) + tr, style)
        for r in range(1, h - 1):
            self.put(x, y + r, vt, style)
            self.put(x + w - 1, y + r, vt, style)
        self.put(x, y + h - 1, bl + hz * (w - 2) + br, style)

    def branch(self, y, xs, trunk, style, down=True):
        """Línea horizontal que une `trunk` con las columnas `xs`.
        down=True: el tronco llega desde arriba y las ramas bajan."""
        lo, hi = min(*xs, trunk), max(*xs, trunk)
        self.put(lo, y, "─" * (hi - lo + 1), style)
        ends = ("┌", "┐", "┬") if down else ("└", "┘", "┴")
        for x in xs:
            self.put(x, y, ends[0] if x == lo else ends[1] if x == hi else ends[2], style)
        if trunk in xs:
            c = "├" if trunk == lo else "┤" if trunk == hi else "┼"
        elif down:
            c = "└" if trunk == lo else "┘" if trunk == hi else "┴"
        else:
            c = "┌" if trunk == lo else "┐" if trunk == hi else "┬"
        self.put(trunk, y, c, style)

    def text(self):
        out = Text()
        for y in range(self.h):
            run, cur = [], None
            for c, st in zip(self.ch[y], self.st[y]):
                if st != cur and run:
                    out.append("".join(run), style=cur or None)
                    run = []
                cur = st
                run.append(c)
            out.append("".join(run).rstrip() if not cur else "".join(run), style=cur or None)
            if y < self.h - 1:
                out.append("\n")
        return out


def graph_rows(h):
    """Filas que ocupa el gráfico de actividad (leyenda incluida) en un panel de `h` filas."""
    return 4 if h >= 20 else 3


DOTS = (0x40 | 0x80, 0x04 | 0x20, 0x02 | 0x10, 0x01 | 0x08)   # filas de puntos braille, de abajo arriba


def activity_graph(cv, y, w, h, s, agents, now, span=600, focus=None):
    """Matriz de puntos con las herramientas usadas: una columna por intervalo, apilada por
    quién la usó (el lead y cada subagente en su color).

    Cuanto más uso, más intenso el color; la columna de ahora destella con cada herramienta nueva."""
    rows = h - 1
    sources = graph_sources(s, agents, now, span, focus)
    counts = [[0] * w for _ in sources]
    for k, (_, _, beats) in enumerate(sources):
        for t in beats:
            back = now - t
            if 0 <= back < span:
                counts[k][w - 1 - int(back / span * w)] += 1
    totals = [sum(c[x] for c in counts) for x in range(w)]
    top = max(totals) or 1
    graph_legend(cv, y, w, s, sources, now, span, focus)
    fresh = now - max((t for _, _, b in sources for t in b), default=0) < 1.2
    base = y + rows
    for col in range(w):
        if totals[col]:
            graph_column(cv, col, base, rows, [(c[col], src[1]) for c, src in zip(counts, sources, strict=True)],
                         totals[col] / top, fresh and col == w - 1)
        else:   # línea base de puntos, con una marca de tiempo más clara cada 2 minutos
            tick = (w - 1 - col) % max(1, round(w / 5)) == 0
            cv.put(col, base, "⣀", C.DIM if tick else C.LINE)


def graph_sources(s, agents, now, span, focus):
    """(nombre, color, latidos) de quien aparece en el gráfico: el lead y los subagentes activos."""
    side = Counter(t for v in agents for t in v["beats"])
    lead = list((Counter(s.beats) - side).elements())
    sources = [("lead", C.LEAD, lead)] + [(v["type"], v["color"], v["beats"]) for v in agents
                                        if any(0 <= now - t < span for t in v["beats"]) or v["running"]
                                        or v["id"] == focus]
    mine = next((v for v in agents if v["id"] == focus), None) if focus is not None else None
    if mine is None:
        return sources
    # modo foco: el agente enfocado primero (abajo) y el resto apagado
    others = [(n, blend(C.LINE, c, 0.3), b) for n, c, b in sources if b is not mine["beats"]]
    return [("◎ " + mine["type"], mine["color"], mine["beats"]), *others]


def graph_legend(cv, y, w, s, sources, now, span, focus):
    """Un latido que se acelera con el ritmo, quién aporta cuánto y el ritmo total."""
    rate = sum(1 for t in (sources[0][2] if focus is not None else s.beats) if 0 <= now - t < 60)
    beat = (now * (0.8 + min(3.0, rate / 10))) % 1 < 0.22
    x = segs(cv, 0, y, [("◆ " if beat else "◇ ", f"bold {C.LEAD}" if beat else C.LINE), ("ACTIVIDAD ", f"bold {C.FG}"),
                        ("10 min  ", C.DIM)])
    for (name, color, beats) in sources:
        n = sum(1 for t in beats if 0 <= now - t < span)
        if x + len(f"■ {trunc(name, 14)} {n}  ") > w - 26:
            break
        x = segs(cv, x, y, [("■ ", color), (f"{trunc(name, 14)} ", C.FG), (f"{n}  ", C.DIM)])
    right = f"{rate} tools/min" + ("" if focus is not None else f" · {ktok(round(s.token_rate(now)))} tok/min")
    cv.put(max(x, w - len(right)), y, right, C.DIM)


def graph_column(cv, col, base, rows, parts, share, flash):
    """Una columna apilada: `parts` son (cuántas, color) de abajo arriba y `share`, su alto relativo."""
    total = sum(n for n, _ in parts)
    height = max(1, round(share * rows * 4))
    k = 0.4 + 0.6 * share
    bounds, acc = [], 0
    for n, _ in parts:
        acc += n
        bounds.append(acc / total * height)
    for r in range(rows):
        dots = min(4, height - 4 * r)
        if dots <= 0:
            break
        mid = 4 * r + dots / 2
        style = blend(C.LINE, parts[next(i for i, b in enumerate(bounds) if mid <= b + 1e-9)][1], k)
        if 4 * r + dots == height:   # el punto más alto, más claro: la cresta
            style = blend(style, "#ffffff", 0.25)
        if k > 0.8:
            style = f"bold {style}"
        if flash:   # latido
            style = "bold #ffffff"
        cv.put(col, base - r, chr(0x2800 + sum(DOTS[:dots])), style)


def hp_color(hp):
    return C.TOOL if hp > 50 else C.WAIT if hp > 20 else C.ERR


def meter(value, total, width, color, empty=None):
    fill = round(width * value / total) if total else 0
    return [("▰" * fill, color), ("▱" * (width - fill), empty or C.LINE)]


def segs(cv, x, y, parts):
    """Escribe tramos (texto, estilo) seguidos y devuelve la columna final."""
    for text, style in parts:
        cv.put(x, y, text, style)
        x += len(text)
    return x
