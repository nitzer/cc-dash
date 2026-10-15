"""Panel de agentes: los diseños diagrama, lista, tabla, tarjetas y tiempo."""

import os
import random
import textwrap
import time

from rich.text import Text

from .canvas import Canvas, activity_graph, graph_rows, hp_color, meter, segs
from .config import limits
from .faces import FACE_SIZE, _visor, agent_face, compact_stats, dither, face_pixels, lead_face, put_cells, put_stats, stat_rows
from .game import EVO, agent_game, lead_game
from .text import SPIN, age, clock, ktok, short_model, trunc
from .theme import C, FRESH, STATES, blend, breath


def diagram(store, s, w, h, now, sel=None, show_all=False, idle_rain=False, focus=None):
    """El lead en una fila y, debajo, una fila por subagente: su rostro, quién es y, a la
    derecha, el log de lo que fue haciendo."""
    cv = Canvas(w, h)
    if s is None:
        cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", C.DIM)
        return cv.text()
    phase = int(now * 9)
    state = store.state(s)
    everyone = store.agents(s)
    agents = store.visible(s, now, show_all, sel)
    gh = graph_rows(h)
    activity_graph(cv, h - gh, w, gh, s, everyone, now, focus=focus)
    h -= gh

    # ── tamaños: rostro y barras bajo el rostro, lo más grande con lo que quepan todos ──
    indent = 4 if w >= 90 else 0   # los subagentes cuelgan un poco a la derecha del lead
    gone = len(everyone) - len(agents)
    idx = next((i for i, v in enumerate(agents) if v["id"] == sel), None)
    per_row = 2 if w >= 170 else 1
    rows_needed = max(1, -(-len(agents) // per_row)) if agents else 0
    reserve = 1 if gone or (show_all and everyone) else 0
    options = ((6, 3), (4, 3), (3, 3), (3, 2), (3, 1))
    face_rows, stat_n = next(((f, n) for f, n in options
                              if (5 + n) + rows_needed * (f + n + 2) <= h - reserve), options[-1])
    lead_h = 2 + 3 + stat_n
    card_h = face_rows + stat_n + 2

    # ── fila del lead ──
    lst = C.WAIT if state == "waiting" else C.LEAD if state != "ended" else C.LINE
    lg = lead_game(s)
    cv.box(0, 0, w, lead_h, lst)
    # todas las filas comparten columnas: hueco fijo para el rostro y sus barras, luego el texto
    slot = 17
    tx = 2 + slot + 2
    # el rostro, las barras y el texto del lead en las mismas columnas que los de los subagentes
    put_cells(cv, indent + 2 + (slot - 6) // 2, 1, lead_face(state, now, lg["tier"]))
    put_stats(cv, indent + 2, 4, stat_rows(lg, lst if state != "ended" else C.DIM, stat_n, now))
    ltx = indent + tx
    iw = w - ltx - 2
    spin = SPIN[phase % len(SPIN)] if state == "busy" else "◆" if state == "waiting" else "■"
    right = f"{spin} {STATES[state][1]}"
    who = f"{short_model(s.model)} · lead · {store.label(s)}" + (f" · effort {s.effort}" if s.effort else "")
    cv.put(ltx, 1, trunc(who, max(0, iw - len(right) - 2)), f"bold {lst}")
    cv.put(ltx + iw - len(right), 1, right, f"bold {lst}")
    stats = f"{s.tools} tools · salida {ktok(s.out_tokens)}"
    if s.cost is not None:
        stats += f" · ${s.cost:.2f}"
    if s.advisor:
        stats += f" · advisor {short_model(s.advisor)}" + (f" ×{s.advisor_calls}" if s.advisor_calls else "")
    cv.put(ltx, 2, trunc(stats, iw), C.DIM)
    why = store.stuck(s, now)
    plain = list(s.pending.values())
    if state == "waiting":
        doc = f" · D abre {os.path.basename(s.docs[-1])}" if s.docs else ""
        cv.put(ltx, 3, trunc(f"◆ necesita {store.waiting_for(s)}{doc}", iw), f"bold {breath(C.WAIT, now)}")
    elif why:
        cv.put(ltx, 3, trunc("⚠ posible atasco: " + " · ".join(why), iw), f"bold {breath(C.ERR, now)}")
    elif plain and state != "ended":
        name, lab, _ = max(plain, key=lambda p: p[2])
        more = f" (+{len(plain) - 1})" if len(plain) > 1 else ""
        cv.put(ltx, 3, trunc(f"▶ {name}{more}: {lab}", iw), C.TOOL)
    else:
        cv.put(ltx, 3, trunc(s.title or "—", iw), C.DIM)
    if stat_n >= 2:
        cv.put(ltx, 4, trunc(f"HP = contexto libre: {lg['hp']}%" + (f" de {ktok(s.window())}" if s.ctx else ""), iw), C.DIM)

    # ── una fila por subagente ──
    top = lead_h
    avail = h - top - reserve
    if rows_needed * card_h > avail:
        avail = h - top - 1   # hará falta la nota de "+N más"
    fit = max(1, avail // card_h) * per_row
    first = max(0, idx - fit + 1) if idx is not None and idx >= fit else 0
    shown = agents[first:first + fit]

    notes, live_hidden = [], 0
    if len(agents) > len(shown):
        hidden = agents[:first] + agents[first + fit:]
        live_hidden = sum(1 for v in hidden if v["running"])
        notes.append(f"+{len(hidden)} más" + (f", {live_hidden} en marcha" if live_hidden else "") + " · j/k o ←→")
    if gone:
        notes.append(f"{gone} terminado{'s' if gone != 1 else ''} oculto{'s' if gone != 1 else ''} · t")
    elif show_all and any(not v["running"] for v in everyone):
        notes.append("t oculta terminados")
    if notes:
        note = trunc(" · ".join(notes), w)
        cv.put(max(0, w - len(note)), h - 1, note, C.SWARM if live_hidden else C.DIM)

    if not shown:
        if idle_rain == "force" or (idle_rain and not everyone):
            rain(cv, top, now, h)
        mid = w // 2
        size = 6 if avail >= 8 else 4
        put_cells(cv, mid - FACE_SIZE[size][0] // 4, top + 1, dither(face_pixels(_visor, size),
                                                                    (C.LINE, C.LINE, blend(C.LINE, C.FG, 0.35)), None))
        msg = trunc("sin subagentes · aparecen aquí, uno por fila, a medida que el lead los invoca", w - 2)
        cv.put(max(0, mid - len(msg) // 2), top + size + 2, msg, C.DIM)
        return cv.text()
    if idle_rain == "force":
        rain(cv, top, now, h)

    gap = 2
    cw = (w - indent - gap * (per_row - 1)) // per_row
    rows_shown = -(-len(shown) // per_row)
    spare = 0
    if indent:   # guía tenue desde el lead hasta cada fila
        mids = [top + r * card_h + card_h // 2 for r in range(rows_shown)]
        for yy in range(top, mids[-1]):
            cv.put(1, yy, "│", C.LINE)
        for r, yy in enumerate(mids):
            cv.put(1, yy, ("└" if r == rows_shown - 1 else "├") + "──", C.LINE)
    for n, v in enumerate(shown):
        row, col = divmod(n, per_row)
        x, y = indent + col * (cw + gap), top + row * card_h
        this_h = card_h + (spare if row == rows_shown - 1 else 0)
        live, chosen = v["running"], v["id"] == sel
        failed = bool(v["end"]) and v["status"] != "completed"
        edge = "bold #ffffff" if chosen else C.ERR if failed else v["color"] if live else C.LINE
        cv.box(x, y, cw, this_h, edge, heavy=chosen)
        show_face = cw >= 64
        fw = FACE_SIZE[face_rows][0] // 2
        g = agent_game(v)
        if show_face:   # centrado en un hueco del mismo ancho para todos; las barras, debajo
            put_cells(cv, x + 2 + (slot - fw) // 2, y + 1, agent_face(v, phase, n, now, face_rows, g["tier"]))
            put_stats(cv, x + 2, y + 1 + face_rows, stat_rows(g, v["color"] if live else C.DIM, stat_n, now))
        ix = x + (tx if show_face else 2)
        iw = x + cw - 2 - ix   # el texto ocupa todo el ancho de la fila
        inner = this_h - 2
        icon, ist = agent_icon(v, phase, n)
        stat = (f"{g['rounds']} ronda{'s' if g['rounds'] != 1 else ''} · " + agent_time(v, now)
                + (f" · {v['tools']} tools" if v["tools"] else "") + (f" · {v['model']}" if v["model"] else ""))
        if g["tier"]:
            stat = f"{EVO[g['tier']]} · " + stat
        cv.put(ix, y + 1, icon, ist)
        cv.put(ix + 2, y + 1, trunc(v["type"], max(0, iw - len(stat) - 4)), f"bold {v['color']}" if live else "bold" if chosen else C.FG)
        cv.put(ix + iw - len(stat), y + 1, stat, v["color"] if live else C.DIM)
        lines = []   # (texto, estilo) bajo la cabecera, por orden de importancia
        desc = textwrap.wrap(v["desc"], iw) or [""]
        lines.append((desc[0], C.FG if live or chosen else C.DIM))
        if len(desc) > 1 and inner >= 5:
            lines.append((trunc(" ".join(desc[1:]), iw), C.FG if live or chosen else C.DIM))
        done_ok = bool(v["end"]) and not failed
        if not (done_ok and v["response"]):   # terminado con respuesta: se muestra la respuesta abajo
            text, tst = agent_now(v)
            lines.insert(1 if inner < 5 else len(lines), (trunc(text, iw), tst))
        if v["response"] and (v["running"] or v["end"]):   # lo último que dijo, si queda sitio
            lines.extend(("“ " + part, blend(C.LINE, C.FG, 0.6))
                         for part in textwrap.wrap(" ".join(v["response"].split()), iw - 2)[:max(0, inner - 1 - len(lines))])
        for k, (text, style) in enumerate(lines[:inner - 1]):
            cv.put(ix, y + 2 + k, trunc(text, iw), style)
    return cv.text()


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
    spin = SPIN[phase % len(SPIN)] if state == "busy" else "◆" if state == "waiting" else "■"
    right = f"{spin} {label} · {s.turns} turno{'s' if s.turns != 1 else ''}"
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
    why = store.stuck(s, now)
    if state == "waiting":
        doc = f" · D abre {os.path.basename(s.docs[-1])}" if s.docs else ""
        cv.put(0, 1, trunc(f"◆ necesita {store.waiting_for(s)}{doc}", w), f"bold {breath(C.WAIT, now)}")
    elif why:
        cv.put(0, 1, trunc("⚠ posible atasco: " + " · ".join(why), w), f"bold {breath(C.ERR, now)}")
    elif s.pending and state != "ended":
        name, lab, _ = max(s.pending.values(), key=lambda p: p[2])
        more = f" (+{len(s.pending) - 1})" if len(s.pending) > 1 else ""
        cv.put(0, 1, trunc(f"▶ {name}{more}: {lab}", w), C.TOOL)
    else:
        cv.put(0, 1, trunc(s.title or "—", w), C.DIM)
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
        notes.append(f"{gone} oculto{'s' if gone != 1 else ''} · t")
    if notes:
        cv.put(x + 3, y, trunc(" · ".join(notes), max(0, room - x - 3)), C.DIM)


def board(store, s, w, h, now, sel=None, show_all=False, layout="lista", idle_rain=False, focus=None):
    """Panel del lead y sus subagentes sin flechas, en el diseño elegido."""
    cv = Canvas(w, h)
    if s is None:
        cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", C.DIM)
        return cv.text()
    phase = int(now * 9)
    everyone = store.agents(s)
    agents = store.visible(s, now, show_all, sel)
    gh = graph_rows(h)
    activity_graph(cv, h - gh, w, gh, s, everyone, now, focus=focus)
    h -= gh + 1   # el resto del panel queda por encima del gráfico
    y = lead_block(cv, store, s, w, now, phase, compact=layout == "tiempo" or h < 14)
    empty = "sin subagentes en uso" + (" — t muestra los terminados" if everyone else "")

    if layout == "tabla":
        shown = pick(agents, max(1, h - y - 2), sel)
        agents_header(cv, y, w, everyone, agents, shown, w)
        flex = max(20, w - 4 - 12 - 11 - 7 - 6 - 17)
        c_task, c_now = flex // 2, flex - flex // 2
        cols = [(4, "TIPO", 12), (16, "MODELO", 11), (27, "TAREA", c_task), (27 + c_task, "AHORA", c_now)]
        for x, name, _ in cols:
            cv.put(x, y + 1, name, C.DIM)
        cv.put(w - 30, y + 1, " HP  LV    XP", C.DIM)
        cv.put(w - 12, y + 1, "TIEMPO TOOLS", C.DIM)
        for i, v in enumerate(shown):
            row, chosen = y + 2 + i, v["id"] == sel
            icon, ist = agent_icon(v, phase, i)
            text, tst = agent_now(v)
            base = "bold" if chosen else C.FG if v["running"] else C.DIM
            if chosen:
                cv.put(0, row, "▸" + " " * (w - 1), f"bold {C.LEAD} on {C.CARD_BG}")
            bg = f" on {C.CARD_BG}" if chosen else ""
            cv.put(2, row, icon, ist + bg)
            cv.put(4, row, trunc(v["type"], 11), (f"bold {v['color']}" if v["running"] else base) + bg)
            cv.put(16, row, trunc(v["model"] or "—", 10), base + bg)
            cv.put(27, row, trunc(v["desc"], c_task - 1), base + bg)
            cv.put(27 + c_task, row, trunc(text, c_now - 1), tst + bg)
            g = agent_game(v)
            segs(cv, w - 30, row, [(f"{g['hp']:>3}%", hp_color(g["hp"]) + bg), (f"{g['lvl']:>4}", f"bold {v['color']}" + bg),
                                   (f"{g['xp']:>3}/10", C.HIGHLIGHT + bg)])
            cv.put(w - 12, row, f"{agent_time(v, now):>6} {v['tools']:>5}", base + bg)
        if not shown:
            cv.put(2, y + 2, empty, C.DIM)

    elif layout == "tarjetas":
        per_row = max(1, min(4, w // 34))
        cw = (w - (per_row - 1) * 2) // per_row
        shown = pick(agents, max(1, (h - y - 1) // 5) * per_row, sel)
        agents_header(cv, y, w, everyone, agents, shown, w)
        for i, v in enumerate(shown):
            x, top = (i % per_row) * (cw + 2), y + 1 + (i // per_row) * 5
            chosen = v["id"] == sel
            failed = bool(v["end"]) and v["status"] != "completed"
            edge = "bold #ffffff" if chosen else breath(v["color"], now) if v["running"] else C.ERR if failed else C.LINE
            cv.box(x, top, cw, 5, edge, heavy=chosen)
            icon, ist = agent_icon(v, phase, i)
            head = trunc(v["type"] + (f" · {v['model']}" if v["model"] else ""), cw - 9)
            segs(cv, x + 2, top, [(" ", ""), (icon, ist), (f" {head} ", f"bold {v['color']}" if v["running"] else "bold")])
            text, tst = agent_now(v)
            cv.put(x + 2, top + 1, trunc(v["desc"], cw - 4), C.FG if v["running"] or chosen else C.DIM)
            cv.put(x + 2, top + 2, trunc(text, cw - 4), tst)
            cv.put(x + 2, top + 3, trunc(f"{agent_time(v, now)} · {n_tools(v['tools'])}", cw - 4),
                   v["color"] if v["running"] else C.DIM)
        if not shown:
            cv.put(2, y + 2, empty, C.DIM)

    elif layout == "tiempo":
        lw = min(20, max(12, w // 5))
        bw = w - lw - 1
        shown = pick(agents, max(1, (h - y - 4) // 2), sel)
        t0, t1 = time_window(shown, now)
        time_axis(cv, lw, y, bw, t0, t1)
        cv.put(lw, y + 1, "".join("┊" if i % max(1, bw // 3) == 0 else " " for i in range(bw)), C.LINE)
        cv.put(0, y + 2, "lead", f"bold {C.LEAD}")
        cells = ["░"] * bw
        spans = list(s.turn_spans) + ([(s.turn_start, now)] if s.turn_start and store.state(s) in ("busy", "waiting") else [])
        for a, b in spans:
            if b < t0:
                continue
            lo = max(0, int((a - t0) / (t1 - t0) * bw))
            hi = min(bw, max(lo + 1, int((b - t0) / (t1 - t0) * bw)))
            cells[lo:hi] = ["█"] * (hi - lo)
        for i, c in enumerate(cells):
            cv.put(lw + i, y + 2, c, C.LEAD if c == "█" else C.LINE)
        for i, v in enumerate(shown):
            row, chosen = y + 4 + i * 2, v["id"] == sel
            icon, ist = agent_icon(v, phase, i)
            segs(cv, 0, row, [("▸ " if chosen else "  ", f"bold {C.LEAD}"), (icon + " ", ist),
                              (trunc(v["type"], lw - 5), f"bold {v['color']}" if v["running"] else "bold" if chosen else C.DIM)])
            time_bar(cv, lw, row, bw, t0, t1, v["start"], v["end"], agent_bar_style(v, now), v["running"])
            text, tst = agent_now(v)
            cv.put(lw, row + 1, trunc(f"{v['desc']} · {text} · {agent_time(v, now)}", bw),
                   C.FG if chosen else tst if v["running"] else C.DIM)
        note = empty if not shown else ""
        if len(agents) > len(shown):
            note = f"+{len(agents) - len(shown)} más · ←→"
        elif len(everyone) > len(agents):
            gone = len(everyone) - len(agents)
            note = (note + " · " if note else "") + f"{gone} oculto{'s' if gone != 1 else ''} · t"
        cv.put(2 if not shown else 0, h - 1 if shown else y + 4, trunc(note, w), C.DIM)

    else:  # lista: dos líneas por subagente y una barra de tiempo a la derecha
        bw = min(40, max(16, w // 3))
        bx = w - bw
        shown = pick(agents, max(1, (h - y - 1) // 2), sel)
        agents_header(cv, y, w, everyone, agents, shown, bx - 1)
        t0, t1 = time_window(shown, now)
        if shown:
            time_axis(cv, bx, y, bw, t0, t1)
        for i, v in enumerate(shown):
            row, chosen = y + 1 + i * 2, v["id"] == sel
            icon, ist = agent_icon(v, phase, i)
            base = "bold" if chosen else C.FG if v["running"] else C.DIM
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
        if not shown:
            cv.put(2, y + 2, empty, C.DIM)
    if idle_rain == "force":
        rain(cv, y, now, h)
    elif idle_rain and not agents:
        rain(cv, y + 4, now, h)
    return cv.text()


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
