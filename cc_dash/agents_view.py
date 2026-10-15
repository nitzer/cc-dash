"""Panel de agentes: los diseños diagrama, lista, tabla, tarjetas y tiempo."""

import os
import random
import textwrap
import time
from dataclasses import dataclass

from rich.text import Text

from .canvas import Canvas, activity_graph, graph_rows, hp_color, meter, segs
from .config import limits
from .faces import (
    FACE_SIZE,
    _visor,
    agent_face,
    compact_stats,
    dither,
    face_pixels,
    lead_face,
    put_cells,
    put_stats,
    stat_rows,
)
from .game import EVO, agent_game, lead_game
from .text import SPIN, age, clock, ktok, short_model, trunc
from .theme import FRESH, STATES, C, blend, breath

# (filas del rostro, barras bajo el rostro) en el diagrama, de la más grande a la más chica
DIAGRAM_SIZES = ((6, 3), (4, 3), (3, 3), (3, 2), (3, 1))
SLOT = 17          # columnas para el rostro y sus barras, iguales en todas las filas
TX = 2 + SLOT + 2  # donde empieza el texto de cada fila


def no_sessions(cv):
    cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", C.DIM)
    return cv.text()


def plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def lead_spin(state, phase):
    return SPIN[phase % len(SPIN)] if state == "busy" else "◆" if state == "waiting" else "■"


def lead_status(store, s, state, now):
    """Qué le pasa al lead: espera respuesta, parece atascado, usa una herramienta o, si no, su título."""
    if state == "waiting":
        doc = f" · D abre {os.path.basename(s.docs[-1])}" if s.docs else ""
        return f"◆ necesita {store.waiting_for(s)}{doc}", f"bold {breath(C.WAIT, now)}"
    why = store.stuck(s, now)
    if why:
        return "⚠ posible atasco: " + " · ".join(why), f"bold {breath(C.ERR, now)}"
    if s.pending and state != "ended":
        name, lab, _ = max(s.pending.values(), key=lambda p: p[2])
        more = f" (+{len(s.pending) - 1})" if len(s.pending) > 1 else ""
        return f"▶ {name}{more}: {lab}", C.TOOL
    return s.title or "—", C.DIM


def diagram(store, s, w, h, now, sel=None, show_all=False, idle_rain=False, focus=None):
    """El lead en una fila y, debajo, una fila por subagente: su rostro, quién es y, a la
    derecha, el log de lo que fue haciendo."""
    cv = Canvas(w, h)
    if s is None:
        return no_sessions(cv)
    everyone = store.agents(s)
    agents = store.visible(s, now, show_all, sel)
    gh = graph_rows(h)
    activity_graph(cv, h - gh, w, gh, s, everyone, now, focus=focus)
    h -= gh

    # rostro y barras lo más grandes que quepan con todos los subagentes
    indent = 4 if w >= 90 else 0   # los subagentes cuelgan un poco a la derecha del lead
    per_row = 2 if w >= 170 else 1
    rows_needed = -(-len(agents) // per_row)
    reserve = 1 if len(everyone) > len(agents) or (show_all and everyone) else 0
    size = next(((f, n) for f, n in DIAGRAM_SIZES if (5 + n) + rows_needed * (f + n + 2) <= h - reserve),
                DIAGRAM_SIZES[-1])
    top = diagram_lead(cv, store, s, w, now, indent, size[1])
    card_h = sum(size) + 2
    avail = h - top - reserve
    if rows_needed * card_h > avail:
        avail = h - top - 1   # hará falta la nota de "+N más"
    shown = pick(agents, max(1, avail // card_h) * per_row, sel)
    diagram_notes(cv, w, h, everyone, agents, shown, show_all)

    if not shown:
        diagram_empty(cv, w, h, top, avail, now, idle_rain == "force" or (idle_rain and not everyone))
        return cv.text()
    if idle_rain == "force":
        rain(cv, top, now, h)
    cw = (w - indent - 2 * (per_row - 1)) // per_row
    if indent:
        guide(cv, top, card_h, -(-len(shown) // per_row))
    for n, v in enumerate(shown):
        row, col = divmod(n, per_row)
        agent_card(cv, v, n, (indent + col * (cw + 2), top + row * card_h, cw, card_h), size, now, v["id"] == sel)
    return cv.text()


def diagram_lead(cv, store, s, w, now, indent, stat_n):
    """Fila del lead, con el rostro y el texto en las columnas de los subagentes. Devuelve su alto."""
    state = store.state(s)
    lead_h = 5 + stat_n
    lst = C.WAIT if state == "waiting" else C.LEAD if state != "ended" else C.LINE
    lg = lead_game(s)
    cv.box(0, 0, w, lead_h, lst)
    put_cells(cv, indent + 2 + (SLOT - 6) // 2, 1, lead_face(state, now, lg["tier"]))
    put_stats(cv, indent + 2, 4, stat_rows(lg, lst if state != "ended" else C.DIM, stat_n, now))
    ltx = indent + TX
    iw = w - ltx - 2
    right = f"{lead_spin(state, int(now * 9))} {STATES[state][1]}"
    who = f"{short_model(s.model)} · lead · {store.label(s)}" + (f" · effort {s.effort}" if s.effort else "")
    cv.put(ltx, 1, trunc(who, max(0, iw - len(right) - 2)), f"bold {lst}")
    cv.put(ltx + iw - len(right), 1, right, f"bold {lst}")
    stats = f"{s.tools} tools · salida {ktok(s.out_tokens)}"
    if s.cost is not None:
        stats += f" · ${s.cost:.2f}"
    if s.advisor:
        stats += f" · advisor {short_model(s.advisor)}" + (f" ×{s.advisor_calls}" if s.advisor_calls else "")
    cv.put(ltx, 2, trunc(stats, iw), C.DIM)
    text, style = lead_status(store, s, state, now)
    cv.put(ltx, 3, trunc(text, iw), style)
    if stat_n >= 2:
        cv.put(ltx, 4, trunc(f"HP = contexto libre: {lg['hp']}%" + (f" de {ktok(s.window())}" if s.ctx else ""), iw), C.DIM)
    return lead_h


def diagram_notes(cv, w, h, everyone, agents, shown, show_all):
    """Nota al pie con los subagentes que no caben y los terminados ocultos."""
    notes, live_hidden = [], 0
    if len(agents) > len(shown):
        ids = {v["id"] for v in shown}
        hidden = [v for v in agents if v["id"] not in ids]
        live_hidden = sum(1 for v in hidden if v["running"])
        notes.append(f"+{len(hidden)} más" + (f", {live_hidden} en marcha" if live_hidden else "") + " · j/k o ←→")
    gone = len(everyone) - len(agents)
    if gone:
        notes.append(f"{plural(gone, 'terminado')} oculto{'s' if gone != 1 else ''} · t")
    elif show_all and any(not v["running"] for v in everyone):
        notes.append("t oculta terminados")
    if notes:
        note = trunc(" · ".join(notes), w)
        cv.put(max(0, w - len(note)), h - 1, note, C.SWARM if live_hidden else C.DIM)


def diagram_empty(cv, w, h, top, avail, now, rainy):
    """Sin subagentes: un visor apagado y cómo aparecerán."""
    if rainy:
        rain(cv, top, now, h)
    mid = w // 2
    size = 6 if avail >= 8 else 4
    put_cells(cv, mid - FACE_SIZE[size][0] // 4, top + 1, dither(face_pixels(_visor, size),
                                                                (C.LINE, C.LINE, blend(C.LINE, C.FG, 0.35)), None))
    msg = trunc("sin subagentes · aparecen aquí, uno por fila, a medida que el lead los invoca", w - 2)
    cv.put(max(0, mid - len(msg) // 2), top + size + 2, msg, C.DIM)


def guide(cv, top, card_h, rows):
    """Guía tenue desde el lead hasta cada fila de subagentes."""
    mids = [top + r * card_h + card_h // 2 for r in range(rows)]
    for yy in range(top, mids[-1]):
        cv.put(1, yy, "│", C.LINE)
    for r, yy in enumerate(mids):
        cv.put(1, yy, ("└" if r == rows - 1 else "├") + "──", C.LINE)


def agent_card(cv, v, n, box, size, now, chosen):
    """Fila de un subagente: rostro y barras a la izquierda; quién es y qué hace, a la derecha."""
    x, y, cw, card_h = box
    face_rows, stat_n = size
    live = v["running"]
    failed = bool(v["end"]) and v["status"] != "completed"
    edge = "bold #ffffff" if chosen else C.ERR if failed else v["color"] if live else C.LINE
    cv.box(x, y, cw, card_h, edge, heavy=chosen)
    g = agent_game(v)
    phase = int(now * 9)
    show_face = cw >= 64
    if show_face:   # centrado en un hueco del mismo ancho para todos; las barras, debajo
        fw = FACE_SIZE[face_rows][0] // 2
        put_cells(cv, x + 2 + (SLOT - fw) // 2, y + 1, agent_face(v, phase, n, now, face_rows, g["tier"]))
        put_stats(cv, x + 2, y + 1 + face_rows, stat_rows(g, v["color"] if live else C.DIM, stat_n, now))
    ix = x + (TX if show_face else 2)
    iw = x + cw - 2 - ix   # el texto ocupa todo el ancho de la fila
    stat = (f"{plural(g['rounds'], 'ronda')} · " + agent_time(v, now)
            + (f" · {v['tools']} tools" if v["tools"] else "") + (f" · {v['model']}" if v["model"] else ""))
    if g["tier"]:
        stat = f"{EVO[g['tier']]} · " + stat
    icon, ist = agent_icon(v, phase, n)
    cv.put(ix, y + 1, icon, ist)
    cv.put(ix + 2, y + 1, trunc(v["type"], max(0, iw - len(stat) - 4)),
           f"bold {v['color']}" if live else "bold" if chosen else C.FG)
    cv.put(ix + iw - len(stat), y + 1, stat, v["color"] if live else C.DIM)
    inner = card_h - 2
    for k, (text, style) in enumerate(agent_lines(v, iw, inner, live or chosen, failed)[:inner - 1]):
        cv.put(ix, y + 2 + k, trunc(text, iw), style)


def agent_lines(v, iw, inner, lit, failed):
    """(texto, estilo) bajo la cabecera de un subagente, por orden de importancia."""
    body = C.FG if lit else C.DIM
    desc = textwrap.wrap(v["desc"], iw) or [""]
    lines = [(desc[0], body)]
    if len(desc) > 1 and inner >= 5:
        lines.append((trunc(" ".join(desc[1:]), iw), body))
    if not (v["end"] and not failed and v["response"]):   # terminado con respuesta: se muestra la respuesta abajo
        text, tst = agent_now(v)
        lines.insert(1 if inner < 5 else len(lines), (trunc(text, iw), tst))
    if v["response"] and (v["running"] or v["end"]):   # lo último que dijo, si queda sitio
        said = textwrap.wrap(" ".join(v["response"].split()), iw - 2)[:max(0, inner - 1 - len(lines))]
        lines.extend(("“ " + part, blend(C.LINE, C.FG, 0.6)) for part in said)
    return lines


def agent_icon(v, phase, i=0):
    if v["running"]:
        return SPIN[(phase + i * 3) % len(SPIN)], f"bold {v['color']}"
    if v["end"] and v["status"] != "completed":
        return "✗", f"bold {C.ERR}"
    if v["end"]:
        return "✓", f"bold {C.TOOL}"
    return "■", C.DIM


def agent_now(v):
    """Qué hace el subagente ahora mismo, o cómo terminó."""
    if limits["stuck_tool"] and v["running"] and v["quiet"] > limits["stuck_tool"]:
        return (f"⚠ sin actividad hace {age(v['quiet'])}", C.ERR)
    if v["running"]:
        return (f"▶ {v['tool'][0]}: {v['tool'][1]}", C.TOOL) if v["tool"] else ("pensando…", C.DIM)
    if v["end"] and v["status"] != "completed":
        return (v["status"] or "error", C.ERR)
    if not v["end"]:
        return ("interrumpido", C.DIM)
    return (v["response"] or "sin respuesta", C.DIM)


def n_tools(n):
    return f"{n} tool{'s' if n != 1 else ''}"


def agent_time(v, now):
    if v["running"]:
        return clock(now - v["start"])
    return clock(v["end"] - v["start"]) if v["end"] else "—"


def agent_bar_style(v, now):
    if v["running"]:
        return f"bold {breath(v['color'], now)}"
    return C.ERR if v["end"] and v["status"] != "completed" else blend(C.LINE, v["color"], 0.45)


def lead_block(cv, store, s, w, now, phase, compact=False):
    """Cabecera del lead (3 líneas, o 1 en `compact`). Devuelve la siguiente fila libre."""
    state = store.state(s)
    color = C.WAIT if state == "waiting" else C.LEAD if state != "ended" else C.DIM
    label = STATES[state][1]
    right = f"{lead_spin(state, phase)} {label} · {plural(s.turns, 'turno')}"
    pct = min(100, round(100 * s.ctx / s.window())) if s.ctx else 0
    if compact:
        left = f"{short_model(s.model)} · contexto {pct}%" + (f" · ${s.cost:.2f}" if s.cost is not None else "")
        x = segs(cv, 0, 0, [("LEAD  ", f"bold {color}")])
        cv.put(x, 0, trunc(left, max(0, w - x - len(right) - 2)), "bold")
        cv.put(max(0, w - len(right)), 0, right, f"bold {color}")
        return 2
    x = segs(cv, 0, 0, [("LEAD  ", f"bold {color}")])
    who = short_model(s.model) + (f" · effort {s.effort}" if s.effort else "") + f" · {store.label(s)}"
    cv.put(x, 0, trunc(who, max(0, w - x - len(right) - 2)), "bold")
    cv.put(max(0, w - len(right)), 0, right, f"bold {color}")
    text, style = lead_status(store, s, state, now)
    cv.put(0, 1, trunc(text, w), style)
    g = lead_game(s)
    hp_v, xp_v = f"{g['hp']}%", f"{g['xp']}/10"
    x = segs(cv, 0, 2, [("HP ", C.DIM), *meter(g["hp"], 100, 8, hp_color(g["hp"])), (f" {hp_v:>4}   ", hp_color(g["hp"])),
                        ("LV ", C.DIM), *meter(g["lvl"] % 10, 10, 8, color), (f" {g['lvl']:>4}   ", f"bold {color}"),
                        ("XP ", C.DIM), *meter(g["xp"], 10, 8, C.HIGHLIGHT), (f" {xp_v:>5}", C.HIGHLIGHT),
                        (f"   {g['rounds']} rondas", C.DIM)])
    extra = [f"salida {ktok(s.out_tokens)}"]
    if s.cache_pct is not None:
        extra.append(f"caché {s.cache_pct}%")
    if s.cost is not None:
        extra.append(f"${s.cost:.2f}")
    if s.advisor:
        extra.append(f"advisor {short_model(s.advisor)}")
    cv.put(x + 3, 2, trunc(" · ".join(extra), max(0, w - x - 3)), C.DIM)
    tasks = s.progress()
    if tasks:
        done, total, doing = tasks
        fill = round(done / total * 12)
        just = next((t for t in s.todos.values() if now - t.get("flash", 0) < FRESH), None)
        glow = f"bold {breath(C.TOOL, now * 3)}"
        x = segs(cv, 0, 3, [("tareas   ", C.DIM), ("▰" * fill, glow if just else C.TOOL), ("▱" * (12 - fill), C.LINE),
                            (f" {done}/{total}", C.FG)])
        if just:   # una tarea acaba de terminar: se anuncia aquí unos segundos
            cv.put(x + 3, 3, trunc(f"✦ terminada: {just['subject']}", max(0, w - x - 3)), glow)
        else:
            cv.put(x + 3, 3, trunc(f"▶ {doing}" if doing else "", max(0, w - x - 3)), C.TOOL)
        return 5
    return 4


def time_window(agents, now, floor=1800):
    """Tramo de tiempo que cubren las barras: desde el primer subagente visible hasta ahora."""
    starts = [v["start"] for v in agents if v["start"]]
    t0 = min(starts) if starts else now - floor
    return min(t0, now - 60), now


def time_axis(cv, x, y, w, t0, t1):
    fmt = "%H:%M:%S" if t1 - t0 < 600 else "%H:%M"
    used = w - len("ahora")
    cv.put(x + used, y, "ahora", C.DIM)
    free = 0  # primera columna libre, para que las etiquetas no se pisen
    for frac in (0, 1 / 3, 2 / 3):
        label = time.strftime(fmt, time.localtime(t0 + (t1 - t0) * frac))
        at = int(w * frac)
        if at >= free and at + len(label) + 1 < used:
            cv.put(x + at, y, label, C.DIM)
            free = at + len(label) + 2


def time_bar(cv, x, y, w, t0, t1, start, end, style, running):
    span = max(1.0, t1 - t0)
    a = min(w - 1, max(0, int((start - t0) / span * w)))
    b = w if running else min(w, max(a + 1, int(((end or t1) - t0) / span * w)))
    cv.put(x, y, "·" * a, C.LINE)
    cv.put(x + a, y, "█" * (b - a), style)
    if running:
        cv.put(x + w - 1, y, "▌", style)


def pick(agents, cap, sel):
    """Los `cap` subagentes que caben, desplazados para no perder el seleccionado."""
    at = next((i for i, v in enumerate(agents) if v["id"] == sel), None)
    first = at - cap + 1 if at is not None and at >= cap else 0
    return agents[first: first + cap]


def agents_header(cv, y, w, everyone, agents, shown, room):
    """Línea 'SUBAGENTES …' con los recuentos y lo que queda fuera de la vista."""
    live = sum(1 for v in everyone if v["running"])
    done = sum(1 for v in everyone if v["end"])
    x = segs(cv, 0, y, [("SUBAGENTES  ", f"bold {C.SWARM}"), (f"{live} en marcha · {done} terminados", C.FG)])
    notes = []
    if len(agents) > len(shown):
        notes.append(f"+{len(agents) - len(shown)} más · ←→")
    gone = len(everyone) - len(agents)
    if gone:
        notes.append(f"{plural(gone, 'oculto')} · t")
    if notes:
        cv.put(x + 3, y, trunc(" · ".join(notes), max(0, room - x - 3)), C.DIM)


@dataclass
class Board:
    """Lo que comparten los diseños del panel de agentes."""
    cv: Canvas
    store: object
    s: object
    w: int
    h: int        # filas disponibles, sin el gráfico de actividad
    y: int        # primera fila libre bajo el lead
    now: float
    sel: object
    everyone: list
    agents: list

    @property
    def phase(self):
        return int(self.now * 9)

    @property
    def empty(self):
        return "sin subagentes en uso" + (" — t muestra los terminados" if self.everyone else "")

    def row_style(self, v, chosen):
        return "bold" if chosen else C.FG if v["running"] else C.DIM


def board(store, s, w, h, now, sel=None, show_all=False, layout="lista", idle_rain=False, focus=None):
    """Panel del lead y sus subagentes sin flechas, en el diseño elegido."""
    cv = Canvas(w, h)
    if s is None:
        return no_sessions(cv)
    everyone = store.agents(s)
    agents = store.visible(s, now, show_all, sel)
    gh = graph_rows(h)
    activity_graph(cv, h - gh, w, gh, s, everyone, now, focus=focus)
    h -= gh + 1   # el resto del panel queda por encima del gráfico
    y = lead_block(cv, store, s, w, now, int(now * 9), compact=layout == "tiempo" or h < 14)
    b = Board(cv, store, s, w, h, y, now, sel, everyone, agents)
    shown = LAYOUTS.get(layout, board_list)(b)
    if not shown and layout != "tiempo":
        cv.put(2, y + 2, b.empty, C.DIM)
    if idle_rain == "force":
        rain(cv, y, now, h)
    elif idle_rain and not agents:
        rain(cv, y + 4, now, h)
    return cv.text()


def board_table(b):
    """Una línea por subagente, en columnas."""
    cv, w, y = b.cv, b.w, b.y
    shown = pick(b.agents, max(1, b.h - y - 2), b.sel)
    agents_header(cv, y, w, b.everyone, b.agents, shown, w)
    flex = max(20, w - 4 - 12 - 11 - 7 - 6 - 17)
    c_task = flex // 2
    cols = [(4, "TIPO"), (16, "MODELO"), (27, "TAREA"), (27 + c_task, "AHORA")]
    for x, name in cols:
        cv.put(x, y + 1, name, C.DIM)
    cv.put(w - 30, y + 1, " HP  LV    XP", C.DIM)
    cv.put(w - 12, y + 1, "TIEMPO TOOLS", C.DIM)
    for i, v in enumerate(shown):
        table_row(b, v, i, y + 2 + i, c_task, flex - c_task)
    return shown


def table_row(b, v, i, row, c_task, c_now):
    cv, w = b.cv, b.w
    chosen = v["id"] == b.sel
    if chosen:
        cv.put(0, row, "▸" + " " * (w - 1), f"bold {C.LEAD} on {C.CARD_BG}")
    bg = f" on {C.CARD_BG}" if chosen else ""
    base = b.row_style(v, chosen) + bg
    icon, ist = agent_icon(v, b.phase, i)
    text, tst = agent_now(v)
    cv.put(2, row, icon, ist + bg)
    cv.put(4, row, trunc(v["type"], 11), (f"bold {v['color']}" + bg) if v["running"] else base)
    cv.put(16, row, trunc(v["model"] or "—", 10), base)
    cv.put(27, row, trunc(v["desc"], c_task - 1), base)
    cv.put(27 + c_task, row, trunc(text, c_now - 1), tst + bg)
    g = agent_game(v)
    segs(cv, w - 30, row, [(f"{g['hp']:>3}%", hp_color(g["hp"]) + bg), (f"{g['lvl']:>4}", f"bold {v['color']}" + bg),
                           (f"{g['xp']:>3}/10", C.HIGHLIGHT + bg)])
    cv.put(w - 12, row, f"{agent_time(v, b.now):>6} {v['tools']:>5}", base)


def board_cards(b):
    """Tarjetas de cinco filas, hasta cuatro por fila."""
    cv, w, y = b.cv, b.w, b.y
    per_row = max(1, min(4, w // 34))
    cw = (w - (per_row - 1) * 2) // per_row
    shown = pick(b.agents, max(1, (b.h - y - 1) // 5) * per_row, b.sel)
    agents_header(cv, y, w, b.everyone, b.agents, shown, w)
    for i, v in enumerate(shown):
        x, top = (i % per_row) * (cw + 2), y + 1 + (i // per_row) * 5
        chosen = v["id"] == b.sel
        failed = bool(v["end"]) and v["status"] != "completed"
        edge = "bold #ffffff" if chosen else breath(v["color"], b.now) if v["running"] else C.ERR if failed else C.LINE
        cv.box(x, top, cw, 5, edge, heavy=chosen)
        icon, ist = agent_icon(v, b.phase, i)
        head = trunc(v["type"] + (f" · {v['model']}" if v["model"] else ""), cw - 9)
        segs(cv, x + 2, top, [(" ", ""), (icon, ist), (f" {head} ", f"bold {v['color']}" if v["running"] else "bold")])
        text, tst = agent_now(v)
        cv.put(x + 2, top + 1, trunc(v["desc"], cw - 4), C.FG if v["running"] or chosen else C.DIM)
        cv.put(x + 2, top + 2, trunc(text, cw - 4), tst)
        cv.put(x + 2, top + 3, trunc(f"{agent_time(v, b.now)} · {n_tools(v['tools'])}", cw - 4),
               v["color"] if v["running"] else C.DIM)
    return shown


def board_timeline(b):
    """Una barra de tiempo por subagente bajo la del lead, con el eje de horas arriba."""
    cv, w, y, now = b.cv, b.w, b.y, b.now
    lw = min(20, max(12, w // 5))
    bw = w - lw - 1
    shown = pick(b.agents, max(1, (b.h - y - 4) // 2), b.sel)
    t0, t1 = time_window(shown, now)
    time_axis(cv, lw, y, bw, t0, t1)
    cv.put(lw, y + 1, "".join("┊" if i % max(1, bw // 3) == 0 else " " for i in range(bw)), C.LINE)
    cv.put(0, y + 2, "lead", f"bold {C.LEAD}")
    lead_timeline(b, lw, bw, t0, t1)
    for i, v in enumerate(shown):
        row, chosen = y + 4 + i * 2, v["id"] == b.sel
        icon, ist = agent_icon(v, b.phase, i)
        segs(cv, 0, row, [("▸ " if chosen else "  ", f"bold {C.LEAD}"), (icon + " ", ist),
                          (trunc(v["type"], lw - 5), f"bold {v['color']}" if v["running"] else "bold" if chosen else C.DIM)])
        time_bar(cv, lw, row, bw, t0, t1, v["start"], v["end"], agent_bar_style(v, now), v["running"])
        text, tst = agent_now(v)
        cv.put(lw, row + 1, trunc(f"{v['desc']} · {text} · {agent_time(v, now)}", bw),
               C.FG if chosen else tst if v["running"] else C.DIM)
    note = b.empty if not shown else ""
    if len(b.agents) > len(shown):
        note = f"+{len(b.agents) - len(shown)} más · ←→"
    elif len(b.everyone) > len(b.agents):
        gone = len(b.everyone) - len(b.agents)
        note = (note + " · " if note else "") + f"{plural(gone, 'oculto')} · t"
    cv.put(2 if not shown else 0, b.h - 1 if shown else y + 4, trunc(note, w), C.DIM)
    return shown


def lead_timeline(b, lw, bw, t0, t1):
    """Los turnos del lead como tramos llenos en su barra."""
    s = b.s
    cells = ["░"] * bw
    spans = list(s.turn_spans)
    if s.turn_start and b.store.state(s) in ("busy", "waiting"):
        spans.append((s.turn_start, b.now))
    for a, z in spans:
        if z < t0:
            continue
        lo = max(0, int((a - t0) / (t1 - t0) * bw))
        hi = min(bw, max(lo + 1, int((z - t0) / (t1 - t0) * bw)))
        cells[lo:hi] = ["█"] * (hi - lo)
    for i, c in enumerate(cells):
        b.cv.put(lw + i, b.y + 2, c, C.LEAD if c == "█" else C.LINE)


def board_list(b):
    """Dos líneas por subagente y una barra de tiempo a la derecha."""
    cv, w, y, now = b.cv, b.w, b.y, b.now
    bw = min(40, max(16, w // 3))
    bx = w - bw
    shown = pick(b.agents, max(1, (b.h - y - 1) // 2), b.sel)
    agents_header(cv, y, w, b.everyone, b.agents, shown, bx - 1)
    t0, t1 = time_window(shown, now)
    if shown:
        time_axis(cv, bx, y, bw, t0, t1)
    for i, v in enumerate(shown):
        row, chosen = y + 1 + i * 2, v["id"] == b.sel
        icon, ist = agent_icon(v, b.phase, i)
        base = b.row_style(v, chosen)
        x = segs(cv, 0, row, [("▸ " if chosen else "  ", f"bold {C.LEAD}"), (icon + " ", ist),
                              (f"{trunc(v['type'], 11):<12}", f"bold {v['color']}" if v["running"] else base),
                              (f"{trunc(v['model'] or '—', 9):<10}", base)])
        cv.put(x, row, trunc(v["desc"], max(0, bx - x - 2)), base)
        time_bar(cv, bx, row, bw, t0, t1, v["start"], v["end"], agent_bar_style(v, now), v["running"])
        text, tst = agent_now(v)
        tail = f" · {n_tools(v['tools'])} · {agent_time(v, now)}"
        room = max(0, bx - x - len(tail) - 2)
        cv.put(x, row + 1, trunc(text, room), tst)
        cv.put(x + len(trunc(text, room)), row + 1, tail, C.DIM)
        # HP / LV / XP en la misma columna para todos, bajo la barra de tiempo
        segs(cv, bx, row + 1, compact_stats(agent_game(v), v["color"] if v["running"] else C.DIM, now))
    return shown


LAYOUTS = {"tabla": board_table, "tarjetas": board_cards, "tiempo": board_timeline, "lista": board_list}


def glitched(text, now):
    """El texto con parte de sus caracteres corrompidos: el parpadeo de un aviso."""
    rng = random.Random(int(now * 12))
    out = Text(justify=text.justify)
    for ch in text.plain:
        if ch != " " and rng.random() < 0.22:
            out.append(rng.choice("▓▒░#%&@$<>/\\|"), style=f"bold {rng.choice((C.ERR, C.WAIT, C.SWARM))}")
        else:
            out.append(ch, style=f"bold {C.FG}")
    return out


def rain(cv, top, now, bottom=None):
    """Lluvia de caracteres en las celdas vacías entre las filas `top` y `bottom`: el modo reposo."""
    glyphs = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ0123456789"
    span = (bottom or cv.h) - top
    if span < 3:
        return
    for x in range(0, cv.w, 2):
        seed = (x * 2654435761 + 97) & 0xffffffff
        speed, tail = 3 + seed % 5, 4 + (seed >> 3) % 6
        cycle = span + tail + (seed >> 6) % 14
        head = int(now * speed + (seed >> 9)) % cycle
        for k in range(tail):
            y = head - k
            if 0 <= y < span and cv.ch[top + y][x] == " ":
                glyph = glyphs[(seed + y * 7 + (int(now * 6) if k == 0 else 0)) % len(glyphs)]
                cv.put(x, top + y, glyph, f"bold {C.FG}" if k == 0 else blend(C.TOOL, C.LINE, k / tail))
