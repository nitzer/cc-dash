"""Rostros de braille de los agentes, sus barras de HP/LV/XP y la galería de evoluciones."""

import math

from rich import box
from rich.panel import Panel
from rich.text import Text

from .canvas import hp_color, meter, segs
from .game import EVO, EVO_GAIN, agent_game
from .theme import C, blend, breath, label


# Rostros en braille con tramado ordenado: cada uno es una función que da la luz de
# cada píxel (0 negro, 1 brillo, EYE ojo encendido, None fondo). Una celda de braille
# son 2x4 píxeles, así que un rostro de 6 filas tiene 24x24 píxeles.
EYE = -1.0
IMPLANT = -2.0   # píxel que brilla como los ojos: los implantes de la segunda evolución
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


def _lit(u, v, cx, cy, a, b):
    """Luz de un elipsoide iluminado desde arriba; None fuera de él."""
    du, dv = (u - cx) / a, (v - cy) / b
    r = du * du + dv * dv
    if r > 1:
        return None
    return max(0.15, min(0.95, 0.08 + 0.8 * (-0.25 * du - 0.45 * dv + 0.85 * math.sqrt(1 - r))))


def _in(u, v, cx, cy, a, b):
    return ((u - cx) / a) ** 2 + ((v - cy) / b) ** 2 <= 1


def _skull(u, v):
    for sx in (-1, 1):
        if _in(u, v, sx * 0.36, 0.02, 0.21, 0.2):
            return EYE if _in(u, v, sx * 0.36, 0.05, 0.08, 0.09) else 0
    if 0.22 < v < 0.4 and abs(u) < 0.03 + (v - 0.22) * 0.5:
        return 0   # nariz
    s = _lit(u, v, 0, -0.18, 0.8, 0.72)
    if s is None and abs(u) < 0.42 and 0.3 < v < 0.9:
        s = 0.7 - 0.3 * abs(u)
    if s is not None and 0.52 < v < 0.8 and abs(u) < 0.4:
        s = 0.9 if int((u + 1) * 9) % 2 else 0.02   # dientes
    return s


def _oni(u, v):
    for sx in (-1, 1):   # cuernos
        t = (-v - 0.45) / 0.55
        if 0 <= t <= 1 and abs(u * sx - (0.55 + 0.3 * t)) < 0.14 * (1 - t) + 0.03:
            return 0.95 - 0.5 * t
    for sx in (-1, 1):
        if _in(u, v, sx * 0.32, 0.0, 0.16, 0.08):
            return EYE
        if -0.24 < v < -0.06 and 0.1 < u * sx < 0.56 and v > -0.3 + (u * sx - 0.1) * 0.4:
            return 0.02   # cejas en V
    if 0.42 < v < 0.74 and abs(u) < 0.46:
        return 1.0 if abs(abs(u) - 0.3) < 0.07 and v < 0.68 else 0.03   # boca y colmillos
    return _lit(u, v, 0, 0.05, 0.85, 0.85)


def _visor(u, v):
    if -0.16 < v < 0.08 and abs(u) < 0.8:
        return EYE if abs(v + 0.04) < 0.06 else 0.04
    if 0.35 < v < 0.75 and abs(u) < 0.3:
        return 0.06 if int((u + 1) * 12) % 2 else 0.6   # rejilla
    return _lit(u, v, 0, 0.0, 0.82, 0.9)


def _mask(u, v):
    for sx in (-1, 1):   # lentes
        if _in(u, v, sx * 0.36, -0.08, 0.2, 0.2):
            return EYE if not _in(u, v, sx * 0.36, -0.08, 0.1, 0.1) else 0.04
    if _in(u, v, 0, 0.55, 0.3, 0.28):
        return 0.08 if int((v + 1) * 14) % 2 else 0.8   # filtro
    return _lit(u, v, 0, -0.1, 0.8, 0.82)


def _fox(u, v):
    for sx in (-1, 1):   # orejas
        t = (-v - 0.3) / 0.7
        if 0 <= t <= 1 and abs(u * sx - 0.55) < 0.28 * (1 - t):
            return 0.2 if abs(u * sx - 0.55) < 0.1 * (1 - t) else 0.9 - 0.3 * t
    if v < -0.35 or v > 0.95 or abs(u) > 0.8 - max(0, v) * 0.75:
        return None
    for sx in (-1, 1):   # ojos rasgados
        if abs(v - (abs(u) - 0.3) * 0.5) < 0.055 and 0.14 < u * sx < 0.52:
            return EYE
    if v > 0.6 and abs(u) < 0.06:
        return 0.04
    return max(0.12, min(1, 0.95 - 0.5 * (u + 0.2) ** 2 - 0.45 * max(0, v)))


def _cyclops(u, v):
    if _in(u, v, 0.25, -0.05, 0.3, 0.3):   # la óptica
        if _in(u, v, 0.25, -0.05, 0.12, 0.12):
            return EYE
        return 0.9 if _in(u, v, 0.25, -0.05, 0.2, 0.2) else 0.04
    s = _lit(u, v, 0, 0, 0.82, 0.92)
    if s is not None and u < -0.05:
        s = s * 0.55 + (0.3 if int((v + 1) * 7) % 2 else 0)   # placas de metal
    if s is not None and 0.5 < v < 0.62 and u > -0.05:
        s = 0.04
    return s


FACES = (("calavera", _skull), ("oni", _oni), ("visor", _visor), ("respirador", _mask),
         ("kitsune", _fox), ("cíclope", _cyclops))
FACE_SIZE = {10: (40, 40), 6: (24, 24), 4: (20, 16), 3: (12, 12)}   # filas -> píxeles (ancho, alto)
_face_px = {}


def face_pixels(fn, rows, tier=0):
    """Píxeles de un rostro a un tamaño y nivel de evolución; se calculan una sola vez.

    Cada evolución le suma algo: I una cresta, II implantes en las mejillas, III un halo."""
    key = (fn, rows, tier)
    if key not in _face_px:
        w, h = FACE_SIZE[rows]
        px = [[fn(((x + 0.5) / w * 2 - 1) * 1.05, ((y + 0.5) / h * 2 - 1) * 1.05)
               for x in range(w)] for y in range(h)]
        cx = (w - 1) / 2
        if tier >= 1:   # cresta en punta sobre la frente
            for y in range(max(2, h // 5)):
                for x in range(w):
                    if abs(x - cx) <= 0.5 + y * 0.6:
                        px[y][x] = 1.0
        if tier >= 2:   # implantes: dos líneas que brillan como los ojos, en las mejillas
            for y in range(int(h * 0.45), int(h * 0.8)):
                for x in (round(w * 0.14), round(w * 0.86) - 1):
                    if px[y][x] not in (None, EYE):
                        px[y][x] = IMPLANT
        if tier >= 3:   # halo de puntos sobre la cabeza
            for x in range(1, w - 1, 2):
                if px[0][x] is None:
                    px[0][x] = 0.9
        _face_px[key] = px
    return _face_px[key]


def dither(px, palette, eyes, scan=None, crack=False):
    """Pasa los píxeles a celdas de braille [(carácter, estilo)].

    palette: (sombra, medio, brillo) según la luz media de la celda; eyes: estilo de los
    ojos o None si están apagados; scan: columna que ilumina un visor; crack: rostro roto."""
    h, w = len(px), len(px[0])
    out = []
    for cy in range(h // 4):
        row = []
        for cx in range(w // 2):
            bits, light, n, eye = 0, 0.0, 0, False
            for dx in range(2):
                for dy in range(4):
                    x, y = 2 * cx + dx, 4 * cy + dy
                    val = px[y][x]
                    if crack and abs(x - (w // 2 + (y - h // 2) // 3)) < 1:
                        continue
                    if val == IMPLANT:   # implantes: brillan con los ojos, también en un visor
                        if eyes is not None:
                            bits |= DOTS[dx][dy]
                            eye = True
                            continue
                        val = 0.5
                    if val == EYE:
                        if eyes is not None and (scan is None or abs(x - scan) <= 2):
                            bits |= DOTS[dx][dy]
                            eye = True
                            continue
                        val = 0.25 if eyes is not None else 0.0
                    val = val or 0.0
                    light += val
                    n += 1
                    if val > (BAYER[y % 4][x % 4] + 0.5) / 16:
                        bits |= DOTS[dx][dy]
            m = light / n if n else 0
            style = eyes if eye else palette[0] if m < 0.4 else palette[1] if m < 0.78 else palette[2]
            row.append((chr(0x2800 + bits) if bits else " ", style))
        out.append(row)
    return out


def eye_color(color):
    """Ojos que contrasten con el color del rostro: rojo, o cian si el rostro ya es rojizo."""
    if not color.startswith("#"):
        return "bright_cyan" if "red" in color else "bright_red"
    r, g = int(color[1:3], 16), int(color[3:5], 16)
    return "#00f0ff" if r > 200 and g < 140 else C.BLOOD


def agent_face(v, phase, i, now, rows, tier=0, fn=None):
    """Rostro tramado de un subagente, en su color; la expresión dice cómo va."""
    fn = fn or FACES[sum(map(ord, v["type"])) % len(FACES)][1]
    px = face_pixels(fn, rows, tier)
    color = v["color"]
    failed = bool(v["end"]) and v["status"] != "completed"
    scan = None
    if v["running"]:
        palette = (blend(C.LINE, color, 0.5), color, "#ffd75e" if tier >= 4 else C.HIGHLIGHT)   # EVO MAX: brillos dorados
        k = (1 - math.cos(2 * math.pi * (now + i * 0.7) / 1.6)) / 2
        glow = eye_color(color)
        eyes = None if (phase + i * 11) % 41 == 0 else f"bold {blend(glow, '#ffffff', 0.5 * k)}"
        if fn is _visor:   # el visor barre de lado a lado
            w = len(px[0])
            at = (phase + i * 3) % (2 * w - 2)
            scan = at if at < w else 2 * w - 2 - at
    elif failed:
        palette, eyes = (blend(C.LINE, C.ERR, 0.4), blend(C.LINE, C.ERR, 0.75), blend(C.LINE, "#ffb3b8", 0.6)), f"bold {C.ERR}"
    elif v["end"]:
        palette = (blend(C.LINE, color, 0.2), blend(C.LINE, color, 0.4), blend(C.LINE, C.HIGHLIGHT, 0.45))
        eyes = blend(C.LINE, color, 0.55)
    else:   # interrumpido: apagado
        palette, eyes = (C.LINE, C.LINE, blend(C.LINE, C.FG, 0.3)), None
    cells = dither(px, palette, eyes, scan, crack=failed)
    if v["running"] and v["tool"] and (phase + i * 5) % 7 < 2:   # señal corrupta mientras usa una herramienta
        g = (phase // 3 + i) % len(cells)
        accent = C.AGENTS[0] if color == C.AGENTS[1] else C.AGENTS[1]
        cells[g] = [(" ", "")] + [(ch, accent) for ch, _ in cells[g][:-1]]
    return cells


def lead_face(state, now, tier=0):
    px = face_pixels(_oni, 3, tier)
    if state == "ended":
        return dither(px, (C.LINE, C.LINE, blend(C.LINE, C.FG, 0.3)), None)
    k = (1 - math.cos(2 * math.pi * now / 1.6)) / 2
    eyes = f"bold {blend(C.WAIT if state == 'waiting' else C.BLOOD, '#ffffff', 0.3 * k)}"
    return dither(px, (blend(C.LINE, C.LEAD, 0.5), C.LEAD, "#ffe0c2"), eyes if state != "idle" else blend(C.LINE, C.BLOOD, 0.6))


def reached_tier(store, focus, k):
    """La evolución más alta que alcanzó en la sesión un subagente con el rostro k; las demás son sorpresa."""
    return max((agent_game(v)["tier"] for v in (store.agents(focus) if focus else [])
                if sum(map(ord, v["type"])) % len(FACES) == k), default=0)


def gallery_panel(view, store, focus, now, width, max_rows=40):
    """Ventana de evoluciones (tecla L): un personaje por vez, con h/l y j/k."""
    k = view["face"] % len(FACES)
    top = reached_tier(store, focus, k)
    tier = view["tier"] = max(0, min(top, view["tier"]))
    name, fn = FACES[k]
    color = C.AGENTS[k % len(C.AGENTS)]
    v = {"type": name, "color": color, "running": True, "tool": None, "end": None, "status": ""}
    cells = agent_face(v, int(now * 9), 0, now, 10 if max_rows >= 26 else 6, tier, fn)   # grande si cabe
    inner = width - 4
    body = Text(no_wrap=True, overflow="ellipsis")
    nav = Text(no_wrap=True)
    nav.append("◀ h   ", style=C.DIM)
    nav.append(f" {name.upper()} ", style=f"bold reverse {color}")
    nav.append(f"   l ▶     {k + 1}/{len(FACES)}", style=C.DIM)
    nav.align("center", inner)
    body.append_text(nav)
    body.append("\n\n")
    face_w = len(cells[0])
    pad = " " * max(0, (inner - face_w) // 2)
    for row in cells:
        body.append(pad)
        for ch, style in row:
            body.append(ch, style=style or None)
        body.append("\n")
    body.append("\n")
    for t in range(4, -1, -1):   # la escalera de evoluciones, la más alta arriba; sin niveles: es sorpresa
        picked = t == tier
        line = Text(no_wrap=True)
        line.append(" ▸ " if picked else "   ", style=f"bold {color}")
        if t > top:
            line.append(f"{'???':<9}", style=C.LINE)
            line.append("aún sin descubrir", style=C.LINE)
        else:
            line.append(f"{EVO[t] or 'BASE':<9}", style=f"bold {color}" if picked else C.FG if t < tier else C.DIM)
            line.append(EVO_GAIN[t], style=C.FG if picked else C.DIM)
        body.append_text(line)
        body.append("\n")
    users = sorted({v2["type"] for v2 in (store.agents(focus) if focus else [])
                    if sum(map(ord, v2["type"])) % len(FACES) == k})
    body.append("\n " + (f"lo llevan en esta sesión: {', '.join(users)}" if users
                          else "ningún subagente de esta sesión lleva este rostro"), style=C.DIM)
    return Panel(body, title=label("evoluciones", "EVO"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {color}", style=f"on {C.CARD_BG}", width=width,
                 subtitle="h/l personaje · j/k evolución · Esc cierra", subtitle_align="right")


def stat_rows(g, color, n, now):
    """Las barras que van bajo el rostro: HP, nivel (hacia la próxima evolución) y XP (rondas del nivel).

    Siempre las mismas columnas, con las cifras alineadas a la derecha para que no se peguen ni bailen."""
    hp, lvl, xp = g["hp"], g["lvl"], g["xp"]
    hpc = f"bold {breath(C.ERR, now * 2)}" if hp <= 10 else hp_color(hp)
    if n >= 3:
        return [[("HP ", C.DIM), *meter(hp, 100, 8, hpc), (f" {f'{hp}%':>4}", hpc)],
                [("LV ", C.DIM), *meter(lvl % 10, 10, 8, color), (f" {lvl:>4}", f"bold {color}")],
                [("XP ", C.DIM), *meter(xp, 10, 8, C.HIGHLIGHT), (f" {f'{xp}/10':>4}", C.HIGHLIGHT)]]
    if n == 2:
        return [[("HP ", C.DIM), *meter(hp, 100, 8, hpc), (f" {f'{hp}%':>4}", hpc)],
                [("LV", C.DIM), (f"{lvl:>3}", f"bold {color}"), ("   XP", C.DIM), (f"{f'{xp}/10':>6}", C.HIGHLIGHT)]]
    return [compact_stats(g, color, now)]


def compact_stats(g, color, now):
    """HP, nivel y XP en una línea de 17 columnas fijas: «HP  53% LV  6 XP3»."""
    hp = g["hp"]
    hpc = f"bold {breath(C.ERR, now * 2)}" if hp <= 10 else hp_color(hp)
    return [("HP", C.DIM), (f"{hp:>4}% ", hpc), ("LV", C.DIM), (f"{g['lvl']:>3} ", f"bold {color}"),
            ("XP", C.DIM), (f"{g['xp']}", C.HIGHLIGHT)]


def put_stats(cv, x, y, rows):
    for r, parts in enumerate(rows):
        segs(cv, x, y + r, parts)


def put_cells(cv, x, y, cells):
    for r, row in enumerate(cells):
        for c, (ch, style) in enumerate(row):
            if ch != " ":
                cv.put(x + c, y + r, ch, style)
