"""Paneles del dashboard: cabecera, sesiones, log, detalle, conversación, ayuda y avisos."""

import textwrap
import time
from collections import Counter

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import Segment
from rich.text import Text

from .agents_view import agent_now, agent_time, glitched
from .config import limits
from .game import EVO, lead_game
from .text import HOME, SPIN, age, clock, ktok, short_model, short_path, trunc
from .theme import C, role_color, FRESH, NEWS, STATES, blend, breath, label, state_color


def header(store, sessions, account, now, glitch=False, god=False, focus=None):
    busy = sum(1 for s in sessions if store.state(s) == "busy")
    open_ = sum(1 for s in sessions if store.state(s) != "ended")
    title = Text(justify="center")
    sep = "  //  " if C.HUD else "  ·  "
    title.append("▚▚ CLAUDE CODE" if C.HUD else "CLAUDE CODE", style=f"bold {C.LEAD}" if C.HUD else "bold")
    if god:
        title.append("  ★ GOD MODE ★", style=f"bold {breath(C.WAIT, now * 2)}")
    if focus is not None:
        title.append(sep, style=C.DIM)
        title.append(f"◎ FOCO {focus['type'].upper()}", style=f"bold {breath(focus['color'], now)}")
    if store.multi:
        title.append(sep, style=C.DIM)
        title.append(f"CUENTA {account.upper()}" if account else f"{len(store.active)} CUENTAS",
                     style=f"bold {C.ADVISOR}")
    title.append(sep, style=C.DIM)
    title.append(f"{open_} SESIÓN ABIERTA" if open_ == 1 else f"{open_} SESIONES ABIERTAS", style=f"bold {C.LEAD}")
    title.append(sep, style=C.DIM)
    title.append(f"{busy} TRABAJANDO", style=f"bold {C.SWARM if busy else C.DIM}")
    waiting = sum(1 for s in sessions if store.state(s) == "waiting")
    if waiting:
        title.append(sep, style=C.DIM)
        title.append(f"◆ {waiting} ESPERA{'N' if waiting != 1 else ''} TU RESPUESTA", style=f"bold {breath(C.WAIT, now)}")
    jammed = sum(1 for s in sessions if store.stuck(s, now))
    if jammed:
        title.append(sep, style=C.DIM)
        title.append(f"⚠ {jammed} ATASCADA{'S' if jammed != 1 else ''}", style=f"bold {breath(C.ERR, now)}")
    title.append(sep, style=C.DIM)
    title.append(time.strftime("%H:%M:%S", time.localtime(now)), style=C.DIM)
    legend = Text(justify="center")
    for color, name in ((C.LEAD, "lead"), (C.SWARM, "subagentes"), (C.TOOL, "herramientas"), (C.ADVISOR, "advisor")):
        legend.append("■ ", style=color)
        legend.append(name + "     ", style=C.DIM)
    return Group(glitched(title, now) if glitch else title, Rule(style=C.LINE), legend)


def side_panel(store, sessions, focus, rows, now, active=False):
    body = Text()
    width = 30
    fit = max(1, rows // 2)
    at = sessions.index(focus) if focus in sessions else 0
    first = max(0, at - fit + 1)   # la lista se desplaza para no perder el cursor
    for s in sessions[first: first + fit]:
        state = store.state(s)
        dot, color = STATES[state][0], state_color(state)
        why = store.stuck(s, now)
        if state == "waiting":
            color = f"bold {breath(C.WAIT, now)}"
        elif why:
            dot, color = "⚠", f"bold {breath(C.ERR, now)}"
        mark = "▸" if s is focus else " "
        when = age(now - s.last) if s.last else "—"
        name = trunc(store.label(s), width - 6 - len(when))
        body.append(f"{mark} ", style=f"bold {C.LEAD}")
        body.append(f"{dot} ", style=color)
        body.append(name, style="bold reverse" if s is focus else (C.FG if state != "ended" else C.DIM))
        body.append(" " * max(1, width - 4 - len(name) - len(when)) + when + "\n", style=C.DIM)
        text = ("necesita " + store.waiting_for(s) if state == "waiting" else why[0] if why
                else s.title or s.cwd.replace(HOME, "~") or "—")
        body.append("    " + trunc(text, width - 4) + "\n",
                    style=C.WAIT if state == "waiting" else C.ERR if why else C.DIM)
    return Panel(body, title=label("sesiones"), title_align="left", box=C.PANEL_BOX,
                 border_style=C.LEAD if active else C.LINE)


def session_card(store, s, now):
    """Tarjeta de color con todos los datos de la sesión bajo el cursor. Devuelve (panel, alto)."""
    state = store.state(s)
    live = store.live.get(s.sid) or {}
    badge, color = {"waiting": "◆ te espera", "busy": "● trabajando", "idle": "○ esperando",
                    "ended": "· cerrada"}[state], state_color(state)
    width = 30
    rows = []

    def row(key, value):
        if value:
            rows.append((key, trunc(value, width - 10)))

    if state == "waiting":
        row("necesita", store.waiting_for(s))
    row("atasco", " · ".join(store.stuck(s, now)))
    row("ritmo", f"{ktok(round(s.token_rate(now)))} tok/min · hoy {ktok(s.tokens_today(now))}")
    row("cuenta", s.account)
    row("carpeta", (s.cwd or "").replace(HOME, "~"))
    row("rama", s.branch)
    row("modelo", short_model(s.model) if s.model else "")
    row("effort", " · ".join(x for x in (s.effort, s.perm and f"permisos {s.perm}") if x))
    row("advisor", short_model(s.advisor) if s.advisor else "")
    if s.ctx:
        row("contexto", f"{min(100, round(100 * s.ctx / s.window()))}% · {ktok(s.ctx)} de {ktok(s.window())}")
    row("tokens", f"salida {ktok(s.out_tokens)}" + (f" · caché {s.cache_pct}%" if s.cache_pct is not None else ""))
    row("coste", f"${s.cost:.2f}" if s.cost is not None else "")
    row("turnos", f"{s.turns} · total {age(s.turn_total / 1000)}" if s.turns else "0")
    row("tools", f"{s.tools}" + (f" · {s.errors} con error" if s.errors else ""))
    agents = store.agents(s)
    if agents:
        row("subag.", f"{len(agents)} · {sum(1 for v in agents if v['running'])} en marcha")
    row("skills", " · ".join(s.skills))
    row("archivos", f"{len(s.edits)} editados · {len(s.reads - set(s.edits))} leídos" if s.edits or s.reads else "")
    if live.get("startedAt"):
        row("inicio", time.strftime("%d/%m %H:%M", time.localtime(live["startedAt"] / 1000))
            + f" · hace {age(now - live['startedAt'] / 1000)}")
    row("actividad", f"hace {age(now - s.last)}" if s.last else "")
    row("proceso", " · ".join(x for x in (live.get("pid") and f"pid {live['pid']}",
                                          live.get("version") and f"v{live['version']}") if x))
    row("tmux", live.get("tmux") or "")
    row("id", s.sid[:8])

    t = Text(no_wrap=True, overflow="ellipsis")
    name = trunc(store.name(s), width - len(badge) - 1)
    t.append(name, style="bold")
    t.append(" " * max(1, width - len(name) - len(badge)) + badge + "\n", style=f"bold {color}")
    t.append(trunc(s.title or "sin título", width) + "\n", style=C.FG)
    for n, (key, value) in enumerate(rows):
        t.append(f"{key:<10}", style=C.DIM)
        t.append(value + ("\n" if n < len(rows) - 1 else ""), style=C.FG)
    panel = Panel(t, title=label("detalles", "DATA"), title_align="left", box=C.CARD_BOX, border_style=C.LEAD,
                  style=f"on {C.CARD_BG}")
    return panel, len(rows) + 4


def log_events(store, focus, account, agent=None, only_focus=False, find=""):
    """(eventos, título, filtrado): el log general, el de un subagente o el de la sesión enfocada.
    `find` deja solo las entradas que contienen ese texto."""
    events, title, narrowed = _log_events(store, focus, account, agent, only_focus)
    if find:
        needle = find.lower()
        events = [e for e in events if needle in f"{e[3]} {e[4]}".lower()]
        title += f" · /{find} ({len(events)})"
    return events, title, narrowed


def _log_events(store, focus, account, agent, only_focus):
    title = label("session log", "LOG")
    if agent is not None:
        own = {agent["id"], agent["aid"]} - {None}
        return [e for e in store.log if e[5] in own], f"{title} · solo {agent['type']}: {agent['desc']}", True
    if only_focus and focus is not None:
        return ([e for e in store.log if e[1] == focus.sid],
                f"{title} · solo {store.label(focus)} · f para ver todo", True)
    def wanted(e):
        owner = getattr(store.sessions.get(e[1]), "account", None)
        return owner not in store.off and account in (None, owner)

    return [e for e in store.log if wanted(e)], title, False


def log_panel(store, focus, events, title, narrowed, rows, width, cursor=None, border=C.LINE):
    """`cursor`: índice de la entrada elegida cuando el log tiene el foco (tecla s)."""
    body = Text(no_wrap=True, overflow="ellipsis")
    col = 0 if narrowed else 22 if store.multi else 14
    gutter = 2 if cursor is not None else 0
    first = max(0, len(events) - rows) if cursor is None else max(0, min(cursor - rows // 2, len(events) - rows))
    chunk = events[first: first + rows]
    colors = {}   # sid -> {id del subagente: color}, para pintar sus entradas como en el panel de agentes

    def agent_color(sid, aid):
        if sid not in colors:
            owner = store.sessions.get(sid)
            colors[sid] = {k: v["color"] for v in (store.agents(owner) if owner else []) for k in (v["id"], v["aid"]) if k}
        return colors[sid].get(aid)

    for i, e in enumerate(chunk):
        ts, sid, style, tag, text = e[:5]
        s = store.sessions.get(sid)
        tint = agent_color(sid, e[5]) if e[5] else None
        picked = cursor is not None and first + i == cursor
        line = Text(no_wrap=True)
        if gutter:
            line.append("▸ " if picked else "  ", style=f"bold {C.LEAD}")
        line.append(time.strftime("%H:%M:%S", time.localtime(ts)) + " ", style=C.DIM)
        if col:
            name = trunc(store.label(s), col) if s else sid[:8]
            line.append(f"{name:<{col}} ", style=C.FG if s is focus else C.DIM)
        bad = role_color(style) == C.ERR
        line.append(f"{trunc(tag, 10):<10} ", style=f"bold {tint}" if tint and not bad else role_color(style))
        line.append(trunc(text, max(10, width - 25 - col - gutter)), style=C.FG if s is focus or picked else C.DIM)
        if picked:
            line.pad_right(max(0, width - 4 - len(line.plain)))
            line.stylize(f"bold on {C.CARD_BG}")
        body.append_text(line)
        if i < len(chunk) - 1:
            body.append("\n")
    if not events:
        body.append("sin eventos todavía", style=C.DIM)
    return Panel(body, title=trunc(title, max(10, width - 6)), title_align="left", box=C.PANEL_BOX,
                 border_style=border)


def event_panel(store, e, view, rows, width, now):
    """Recuadro de color con el detalle completo de la entrada del log bajo el cursor."""
    inside = view["pane"] == "out"
    frame = dict(box=C.CARD_BOX, border_style=C.LEAD if inside else C.SWARM, style=f"on {C.CARD_BG}",
                 title_align="left", subtitle_align="right")
    if e is None:
        return Panel(Text("el log está vacío", style=C.DIM), title="detalle", **frame)
    ts, sid, _, tag, text, _, ref = e
    s = store.sessions.get(sid)
    inner = max(20, width - 4)
    lines = []

    def section(name):
        if lines:
            lines.append(("", ""))
        lines.append((name.upper(), f"bold {C.SWARM}"))

    # `lines` empieza vacía: la primera sección o línea no lleva separación

    def add(body, st=C.FG):
        for raw in (body or "").expandtabs(4).splitlines() or [""]:
            for n, part in enumerate(textwrap.wrap(raw, inner - 2, replace_whitespace=False,
                                                   drop_whitespace=False) or [""]):
                lines.append(("  " + (part.lstrip() if n else part), st))

    kind = ref["k"] if ref else None
    status, sst = "", C.DIM
    if kind == "prompt":
        section("prompt")
        add(ref["text"])
    elif kind == "tool":
        if ref["err"]:
            status, sst = "✗ error", C.ERR
        elif ref["out"] is not None:
            status, sst = "✓ terminado", C.TOOL
        else:
            status, sst = "en curso", C.SWARM
        if ref["label"]:
            add(ref["label"], "bold")
        section("salida")
        if ref["out"] is None:
            add("todavía sin resultado" if s and store.state(s) == "busy" else "sin resultado registrado", C.DIM)
        else:
            add(ref["out"].strip() or "(sin salida)", C.ERR if ref["err"] else C.FG)
        section("entrada")
        for key, value in ref["input"].items():
            lines.append((f"  {key}", C.DIM))
            add(value)
    elif kind == "agent":
        v = next((v for v in store.agents(s) if v["id"] == ref["id"]), None) if s else None
        if v:
            failed = bool(v["end"]) and v["status"] != "completed"
            status, sst = ("en marcha", C.SWARM) if v["running"] else ("✗ " + v["status"], C.ERR) if failed else \
                          ("✓ terminado", C.TOOL) if v["end"] else ("interrumpido", C.DIM)
            section(f"subagente {v['type']}" + (f" · {v['model']}" if v["model"] else ""))
            add(v["desc"])
            section("prompt")
            add(v["prompt"] or "(prompt vacío)")
            section("respuesta")
            add(v["response"] or ("todavía sin respuesta" if v["running"] else "(sin respuesta registrada)"),
                C.FG if v["response"] else C.DIM)
    else:
        add(text)

    head = Text(no_wrap=True, overflow="ellipsis")
    head.append(time.strftime("%H:%M:%S", time.localtime(ts)), style="bold")
    head.append(f" · {store.label(s) if s else sid[:8]}", style=C.FG)
    if status:
        head.append(f" · {status}", style=f"bold {sst}")
    body_rows = max(1, rows - 2)
    view["rows"] = body_rows
    top = max(0, min(view["scroll"], len(lines) - body_rows))
    view["scroll"] = top
    body = Text(no_wrap=True, overflow="ellipsis")
    chunk = lines[top: top + body_rows]
    for n, (line, st) in enumerate(chunk):
        body.append(line + ("\n" if n < len(chunk) - 1 else ""), style=st or None)
    more = len(lines) - top - body_rows
    pos = f"{top + 1}–{min(len(lines), top + body_rows)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return Panel(Group(head, Text(""), body), title=trunc(f"detalle · {tag}", max(10, width - 6)),
                 subtitle=pos if len(lines) > body_rows else None, **frame)


def log_cursor(events, lg):
    """Índice de la entrada elegida; sin elección fija (None) sigue a la última."""
    return next((i for i, e in enumerate(events) if e is lg["cur"]), len(events) - 1)


def add_listing(t, width, title, items, color, total=None, extra="", limit=8, names=None, ctx=None):
    """Sección en columna: un elemento por línea, con su cuenta y una barra proporcional.

    ctx = {"open": títulos desplegados, "marks": {línea: título}} permite desplegarla entera."""
    items = list(items)
    opened = ctx is not None and title in ctx["open"]
    if opened:
        limit = len(items)
    first = t.plain.count("\n") + 1
    t.append("\n" + ("▾ " if opened else "▸ " if len(items) > limit else "") + title.upper(), style=f"bold {color}")
    t.append(f"  {total if total is not None else sum(n for _, n in items)}" + (f" · {extra}" if extra else "") + "\n",
             style=C.DIM)
    cap = max(12, min(40, width - 24))
    shown_names = [names(name, cap) if names else trunc(name, cap) for name, _ in items[:limit]]
    name_w = max([8] + [len(x) for x in shown_names])
    top = max((n for _, n in items), default=1)
    for shown, (name, n) in zip(shown_names, items[:limit]):
        if ctx is not None:
            ctx.setdefault("items", {})[t.plain.count("\n")] = name   # para abrir el archivo con Enter
        t.append(f"  {shown:<{name_w}}", style=C.FG)
        t.append(f" {n:>4}  ", style=color)
        t.append("▮" * max(1, round(8 * n / top)), style=blend(C.LINE, color, 0.7))
        t.append("\n")
    if len(items) > limit:
        t.append(f"  +{len(items) - limit} más · Enter despliega\n", style=C.DIM)
    if ctx is not None:
        for line in range(first, t.plain.count("\n")):
            ctx["marks"][line] = title


def scrolled(t, ui, rows, cursor=False, width=0):
    """Recorta un texto largo a lo que cabe, desde ui["scroll"]. Devuelve (cuerpo, subtítulo).

    Con `cursor`, la línea ui["dcur"] queda resaltada y la vista la sigue."""
    lines = t.split("\n", allow_blank=True)
    if lines and not lines[-1].plain:
        lines = lines[:-1]
    body_rows = max(1, rows - 2)
    top = 0
    if ui is not None and cursor and lines:
        ui["dlines"] = len(lines)
        cur = ui["dcur"] = max(0, min(ui["dcur"], len(lines) - 1))
        ui["scroll"] = max(cur - body_rows + 1, min(ui["scroll"], cur))
        lines[cur] = lines[cur].copy()
        lines[cur].pad_right(max(0, width - 4 - len(lines[cur].plain)))
        lines[cur].stylize(f"on {C.LINE}")
    if ui is not None:
        ui["rows"] = body_rows
        top = ui["scroll"] = max(0, min(ui["scroll"], len(lines) - body_rows))
    body = Text("\n", no_wrap=True, overflow="ellipsis").join(lines[top: top + body_rows])
    more = len(lines) - top - body_rows
    pos = f"{top + 1}–{min(len(lines), top + body_rows)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return body, pos if len(lines) > body_rows else None


def agent_stats(t, s, v, now, width, ctx=None):
    """Actividad de un solo subagente, para el modo foco."""
    side = v["side"]

    def listing(*args, **kw):
        add_listing(t, width, *args, ctx=ctx, **kw)

    def row(label, value, style=C.FG):
        t.append(f"{label:<11}", style=C.DIM)
        t.append(value + "\n", style=style)

    failed = bool(v["end"]) and v["status"] != "completed"
    state = "en marcha" if v["running"] else ("✗ " + (v["status"] or "error")) if failed else \
        "✓ terminado" if v["end"] else "interrumpido"
    row("estado", state, v["color"] if v["running"] else C.ERR if failed else C.TOOL if v["end"] else C.DIM)
    row("modelo", v["model"] or "—")
    row("tiempo", agent_time(v, now))
    text, tst = agent_now(v)
    row("ahora", trunc(text, max(10, width - 15)), tst)
    rate = sum(1 for t0 in v["beats"] if 0 <= now - t0 < 60)
    row("ritmo", f"{rate} tools/min · {v['tools']} en total" + (f" · {side.get('errors', 0)} con error"
                                                                   if side.get("errors") else ""))
    tools = side.get("tool_counts") or Counter()
    if tools:
        listing("tools", tools.most_common(), v["color"],
                    extra=f"{side['errors']} con error" if side.get("errors") else "")
    if side.get("skills"):
        listing("skills", side["skills"].most_common(), C.ADVISOR)
    edits, reads = side.get("edits") or Counter(), side.get("reads") or set()
    if edits:
        only_read = len(reads - set(edits))
        listing("archivos editados", edits.most_common(), C.LEAD, total=len(edits),
                    extra=f"{only_read} solo leído{'s' if only_read != 1 else ''}" if only_read else "",
                    names=lambda p, n: short_path(p, s.cwd, n))
    if reads - set(edits):
        listing("archivos leídos", [(p, 1) for p in sorted(reads - set(edits))], C.DIM,
                    names=lambda p, n: short_path(p, s.cwd, n))
    if side.get("mcp"):
        listing("mcp", side["mcp"].most_common(), C.FG)


def stats_panel(store, s, now, ui=None, rows=40, width=60):
    """Actividad de la sesión enfocada: lo que se muestra sin subagente seleccionado."""
    t = Text(no_wrap=True, overflow="ellipsis")
    if s is None:
        return Panel(t, title=label("actividad", "STAT"), title_align="left", box=C.PANEL_BOX, border_style=C.LINE)
    ctx = {"open": ui["open"] if ui is not None else set(), "marks": {}, "items": {}}
    if ui is not None:
        ui["dkind"], ui["dmarks"], ui["ditems"] = "list", ctx["marks"], ctx["items"]

    def row(label, value, style=C.FG):
        t.append(f"{label:<11}", style=C.DIM)
        t.append(value + "\n", style=style)

    def listing(*args, **kw):
        add_listing(t, width, *args, ctx=ctx, **kw)

    row("modelo", " · ".join(x for x in (short_model(s.model), s.effort and f"effort {s.effort}",
                                         s.perm and f"permisos {s.perm}") if x))
    g = lead_game(s)
    t.append(f"{'nivel':<11}", style=C.DIM)
    t.append(f"LV {g['lvl']} · {g['rounds']} rondas · {g['xp']}/10 para subir"
             + (f" · {EVO[g['tier']]}" if g["tier"] else "") + "\n", style=C.FG)
    if s.ctx:
        pct = min(100, round(100 * s.ctx / s.window()))
        fill = round(pct / 100 * 12)
        t.append(f"{'HP':<11}", style=C.DIM)
        t.append("█" * fill, style=C.TOOL if pct < 60 else C.LEAD if pct < 85 else C.ERR)
        t.append("░" * (12 - fill), style=C.LINE)
        left = s.turns_left()
        t.append(f" {100 - pct}% libre · {ktok(s.ctx)} de {ktok(s.window())}"
                 + (f" · ~{left} turnos para llenarse" if left is not None else "") + "\n", style=C.FG)
    why = store.stuck(s, now)
    if why:
        row("atasco", "⚠ " + " · ".join(why), C.ERR)
    usage = []
    if s.cache_pct is not None:
        usage.append(f"caché {s.cache_pct}%")
    usage.append(f"salida {ktok(s.out_tokens)}")
    if s.cost is not None:
        usage.append(f"${s.cost:.2f}")
    row("tokens", " · ".join(usage))
    today = store.daily[(s.account, time.localtime(now)[:3])]
    row("ritmo", f"{ktok(round(s.token_rate(now)))} tok/min · hoy {ktok(s.tokens_today(now))} sesión · "
                 f"{ktok(today)} cuenta")
    turns = str(s.turns)
    if s.turns:
        turns += f" · último {age(s.turn_last / 1000)} · total {age(s.turn_total / 1000)}"
    row("turnos", turns)
    tasks = s.progress()
    if tasks:
        done, total, doing = tasks
        fill = round(done / total * 12)
        t.append(f"{'tareas':<11}", style=C.DIM)
        t.append("▰" * fill, style=C.TOOL)
        t.append("▱" * (12 - fill), style=C.LINE)
        t.append(f" {done}/{total}" + (f" · ▶ {doing}" if doing else "") + "\n", style=C.FG)
    code = []
    info = store.git_info(s, now)
    if info:
        code.append(f"{info['branch'] or '—'} · {info['changed']} modificados"
                    + (f" · {info['untracked']} sin seguimiento" if info["untracked"] else "")
                    + f" · {info['commits']} commit{'s' if info['commits'] != 1 else ''} en la sesión")
    elif s.branch:
        code.append(f"rama {s.branch}")
    if s.lines and any(s.lines):
        code.append(f"+{s.lines[0]} −{s.lines[1]} líneas")
    if code:
        row("git", " · ".join(code))
    if s.tool_counts:
        listing("tools", s.tool_counts.most_common(), C.TOOL, extra=f"{s.errors} con error" if s.errors else "")
    if s.skills:
        listing("skills", s.skills.most_common(), C.ADVISOR)
    if s.edits or s.reads:
        only_read = len(s.reads - set(s.edits))
        listing("archivos editados", s.edits.most_common(), C.LEAD, total=len(s.edits),
                extra=f"{only_read} solo leído{'s' if only_read != 1 else ''}" if only_read else "",
                names=lambda p, n: short_path(p, s.cwd, n))
    if s.mcp:
        listing("mcp", s.mcp.most_common(), C.FG)
    agents = store.agents(s)
    if agents:
        live = sum(1 for v in agents if v["running"])
        listing("subagentes", Counter(v["type"] for v in agents).most_common(), C.SWARM,
                extra=f"{live} en marcha" if live else "")
    if s.hooks:
        listing("hooks", s.hooks.most_common(), C.DIM)
    # el panel se desplaza con AvPág/RePág cuando las listas no caben
    body, pos = scrolled(t, ui, rows, cursor=ui is not None and ui["pane"] == "detalle", width=width)
    return Panel(body, title=label("actividad", "STAT"), title_align="left", box=C.PANEL_BOX, border_style=C.LINE,
                 subtitle=pos, subtitle_align="right")


def tasks_panel(store, s, ui, rows, width, now):
    """Las tareas de la sesión: lo que corre en segundo plano y, si la lleva, su lista de pendientes."""
    frame = dict(title_align="left", subtitle_align="right", box=C.PANEL_BOX, border_style=C.TOOL)
    if s is None:
        return Panel(Text("sin sesión", style=C.DIM), title=label("tareas", "TODO"), **frame)
    inner = max(10, width - 8)
    alive = store.state(s) != "ended"
    lines = []      # una Text por fila
    follow = 0      # fila que conviene tener a la vista: la tarea en curso

    def row(mark, mark_style, text, style, right=""):
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f" {mark} ", style=mark_style)
        body = trunc(text, inner - len(right) - 1)
        line.append(body, style=style)
        if right:
            line.append(" " * max(1, inner - len(body) - len(right)) + right, style=C.DIM)
        lines.append(line)

    def section(title, summary):
        if lines:
            lines.append(Text(""))
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(title, style=f"bold {C.SWARM}")
        head.append(f"  {summary}", style=C.DIM)
        lines.append(head)

    def finished(mark_ok, text, fresh, right):
        """Fila de algo terminado: recién terminado lleva un barrido y brilla; después queda tachado."""
        if fresh < FRESH:
            body = trunc(text, inner - len(right) - 1)
            swept = len(body) if fresh >= 0.7 else int(fresh / 0.7 * len(body))
            glow = f"bold {breath(C.TOOL, now * 3)}"
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append(" ✦ " if int(now * 4) % 2 else f" {mark_ok} ", style=glow)
            line.append(body[:swept], style=f"bold {C.CARD_BG} on {C.TOOL}" if fresh < 0.7 else glow)
            line.append(body[swept:], style=C.FG)
            line.append(" " * max(1, inner - len(body) - len(right)) + right, style=C.DIM)
            lines.append(line)
        else:
            row(mark_ok, f"bold {C.TOOL}", text, f"{C.DIM} strike", right)

    # subagentes y comandos en segundo plano: lo que Claude Code llama tareas
    work = [("agent", v["start"], v) for v in store.agents(s)] + [("job", j["start"], j) for j in s.jobs.values()]
    work.sort(key=lambda w: w[1] or 0)
    if work:
        running = sum(1 for kind, _, w in work if (w["running"] if kind == "agent" else w["end"] is None and alive))
        section("EN SEGUNDO PLANO", f"{running} en marcha · {len(work) - running} terminadas")
        for kind, _, w in work:
            if kind == "agent":
                text = f"{w['type']}: {w['desc']}"
                live, end, failed = w["running"], w["end"], bool(w["end"]) and w["status"] != "completed"
                fresh = now - w["end"] if w["end"] else 1e9
            else:
                text = f"{w['tool']}: {w['label']}"
                live, end, failed = w["end"] is None and alive, w["end"], bool(w["end"]) and w["status"] != "completed"
                fresh = now - w.get("flash", 0)
            took = clock((now if live else end) - w["start"]) if (live or end) and w["start"] else ""
            if live:
                follow = len(lines)
                row(SPIN[int(now * 9) % len(SPIN)], f"bold {C.SWARM}", text, f"bold {C.FG}", took)
            elif failed:
                row("✗", f"bold {C.ERR}", text, C.ERR, w["status"])
            elif end:
                finished("✓", text, fresh, took)
            else:
                row("■", C.DIM, text, C.DIM, "interrumpida")

    # la lista de pendientes que escribe el propio agente, si tiene esas herramientas
    tasks = list(s.todos.values())
    if tasks:
        done = sum(1 for t in tasks if t["status"] == "completed")
        fill = round(done / len(tasks) * 12)
        section("PENDIENTES", f"{done} de {len(tasks)} hechas")
        bar = Text(no_wrap=True)
        bar.append(" " + "▰" * fill, style=C.TOOL)
        bar.append("▱" * (12 - fill), style=C.LINE)
        lines.append(bar)
        for task in tasks:
            if task["status"] == "completed":
                finished("✓", task["subject"], now - task.get("flash", 0), "")
            elif task["status"] == "in_progress":
                follow = len(lines)
                row("▶", f"bold {C.LEAD}", task["active"] or task["subject"], f"bold {C.LEAD}")
            else:
                row("☐", C.FG, task["subject"], C.FG)
    if not lines:
        note = Text("esta sesión no tiene tareas: no ha lanzado subagentes ni comandos en segundo plano, "
                    "y no lleva una lista de pendientes", style=C.DIM)
        return Panel(note, title=label("tareas", "TODO"), **frame)
    if not tasks:
        lines.append(Text(""))
        lines.append(Text(" esta sesión no lleva lista de pendientes", style=C.DIM))

    ui["rows"] = rows
    if ui["tasks_of"] != s.sid:   # al cambiar de sesión vuelve a seguir lo que está en curso
        ui["tasks_of"], ui["scroll"] = s.sid, -1
    top = ui["scroll"] if ui["scroll"] >= 0 else follow - rows // 3
    top = max(0, min(top, len(lines) - rows))
    ui["tasks_top"] = top
    body = Text(no_wrap=True, overflow="ellipsis")
    chunk = lines[top: top + rows]
    for n, line in enumerate(chunk):
        body.append_text(line)
        if n < len(chunk) - 1:
            body.append("\n")
    more = len(lines) - top - rows
    pos = f"{top + 1}–{top + len(chunk)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return Panel(body, title=label("tareas", "TODO"), subtitle=pos if len(lines) > rows else None, **frame)


def detail_panel(store, s, ui, rows, width, now):
    """A la derecha del log: el subagente seleccionado, o la actividad de la sesión."""
    ui["dkind"] = "text"   # lo cambian a "list" los paneles con cursor (actividad)
    agents = store.visible(s, now, ui["all"], ui["sel"])
    i = next((n for n, v in enumerate(agents) if v["id"] == ui["sel"]), None)
    if i is None:
        ui["sel"] = ui["focus"] = None
        if ui["news"]:
            return news_panel(store, ui, rows, width, now)
        return tasks_panel(store, s, ui, rows, width, now) if ui["tasks"] else stats_panel(store, s, now, ui, rows, width)
    v = agents[i]
    focused = ui["focus"] == v["id"]
    head = f"subagente {i + 1}/{len(agents)} · {v['type']}" + (f" · {v['model']}" if v["model"] else "")
    if focused and ui["tab"] == "act":
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" i actividad ", style="reverse bold")
        t.append("  ")
        t.append(" r respuesta ", style=C.DIM)
        t.append(" ")
        t.append(" p prompt ", style=C.DIM)
        t.append("\n\n")
        ctx = {"open": ui["open"], "marks": {}, "items": {}}
        ui["dkind"], ui["dmarks"], ui["ditems"] = "list", ctx["marks"], ctx["items"]
        agent_stats(t, s, v, now, width, ctx)
        body, pos = scrolled(t, ui, rows, cursor=ui["pane"] == "detalle", width=width)
        return Panel(body, title=trunc(f"◎ {head} · {v['desc']}", max(10, width - 6)), title_align="left",
                     subtitle=pos, subtitle_align="right", box=C.PANEL_BOX, border_style=v["color"])
    prompt = ui["tab"] == "prompt"
    text = v["prompt"] if prompt else v["response"]
    if not text:
        text = "(prompt vacío)" if prompt else "todavía sin respuesta" if v["running"] else "(sin respuesta registrada)"
    lines = []
    for raw in text.splitlines() or [""]:
        lines += textwrap.wrap(raw, max(20, width - 4), replace_whitespace=False,
                               drop_whitespace=False) or [""]
    body_rows = max(1, rows - 2)
    ui["rows"] = body_rows
    top = max(0, min(ui["scroll"], len(lines) - body_rows))
    ui["scroll"] = top

    tabs = Text(no_wrap=True, overflow="ellipsis")
    if focused:
        tabs.append(" i actividad ", style=C.DIM)
        tabs.append("  ")
    tabs.append(" r respuesta ", style="reverse bold" if not prompt else C.DIM)
    tabs.append(" ")
    tabs.append(" p prompt ", style="reverse bold" if prompt else C.DIM)
    state = "en marcha" if v["running"] else "terminado" if v["status"] == "completed" else v["status"] or "interrumpido"
    tabs.append(f"   {state}", style=C.SWARM if v["running"] else C.DIM)
    body = Text("\n".join(lines[top: top + body_rows]), style=C.FG, no_wrap=True, overflow="ellipsis")
    head = ("◎ " if focused else "") + head
    more = len(lines) - top - body_rows
    pos = f"{top + 1}–{min(len(lines), top + body_rows)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return Panel(Group(tabs, Text(""), body), title=trunc(f"{head} · {v['desc']}", max(10, width - 6)),
                 title_align="left", subtitle=pos if len(lines) > body_rows else None,
                 subtitle_align="right", box=C.PANEL_BOX, border_style=v["color"] if focused else C.SWARM)


_lines_cache = {}


def transcript_lines(s, width):
    """La conversación de la sesión como líneas (texto, estilo) ya ajustadas al ancho."""
    key = (s.sid, width, s.rev)
    if _lines_cache.get("key") == key:
        return _lines_cache["lines"]
    out = []

    def add(text, style, first, rest):
        lead = first
        for raw in text.splitlines() or [""]:
            for n, part in enumerate(textwrap.wrap(raw, max(20, width - len(first)), replace_whitespace=False,
                                                   drop_whitespace=False) or [""]):
                out.append((lead + (part.lstrip() if n else part), style))
                lead = rest

    for it in s.items:
        if it["k"] == "prompt":
            stamp = time.strftime("%H:%M:%S", time.localtime(it["ts"])) if it["ts"] else ""
            out.append(("", ""))
            out.append((f"── {stamp} " + "─" * max(0, width - len(stamp) - 4), C.LINE))
            add(it["text"], f"bold {C.LEAD}", "› ", "  ")
            out.append(("", ""))
        elif it["k"] == "text":
            add(it["text"], C.FG, "  ", "  ")
            out.append(("", ""))
        else:
            out.append(("  " + trunc(f"▶ {it['name']}  {it['label']}", width - 2), C.TOOL))
            if it["res"] is None:
                continue
            style = C.ERR if it["err"] else C.DIM
            for n, line in enumerate(it["res"]):
                out.append((("    ⎿ " if n == 0 else "      ") + trunc(line, width - 6), style))
            if it["more"]:
                out.append((f"      … +{it['more']} líneas", C.DIM))
    _lines_cache.update(key=key, lines=out)
    return out


def session_panel(store, s, view, rows, width, now):
    """Conversación de la sesión, en el lugar del diagrama. view["top"] None = seguir en vivo."""
    if s is None:
        return Panel(Text("sin sesión", style=C.DIM), box=C.PANEL_BOX, border_style=C.LEAD)
    lines = transcript_lines(s, max(20, width - 4))
    last = max(0, len(lines) - rows)
    if view["top"] is not None and view["top"] >= last:
        view["top"] = None
    top = last if view["top"] is None else max(0, view["top"])
    view["rows"], view["last"] = rows, last
    body = Text(no_wrap=True, overflow="ellipsis")
    chunk = lines[top: top + rows]
    for n, (text, style) in enumerate(chunk):
        body.append(text + ("\n" if n < len(chunk) - 1 else ""), style=style or None)
    if not lines:
        body.append("esta sesión todavía no tiene conversación", style=C.DIM)
    state = STATES[store.state(s)][1]
    title = f"sesión · {store.label(s)} · {state}" + (f" · {s.title}" if s.title else "")
    if view["top"] is None:
        pos = "siguiendo en vivo" if store.state(s) != "ended" else "final"
    else:
        pos = f"líneas {top + 1}–{min(len(lines), top + rows)} de {len(lines)} · Fin para seguir en vivo"
    return Panel(body, title=trunc(title, max(10, width - 6)), title_align="left", subtitle=pos,
                 subtitle_align="right", box=C.PANEL_BOX, border_style=C.LEAD if view["pane"] == "out" else C.LINE)


def footer(store, s, auto, account, now, sel=False, hint=None, flash=None, width=200):
    t = Text(no_wrap=True, overflow="ellipsis")
    if s is None:
        t.append("q salir", style=C.DIM)
        return t
    state = store.state(s)
    t.append((s.cwd or "?").replace(HOME, "~") + " $ ", style=f"bold {C.LEAD}")
    t.append("▌ " if int(now * 2) % 2 else "  ", style=C.FG)

    def field(label, value, color):
        t.append(f" {label} ", style=C.DIM)
        t.append(f"[{value}]", style=f"bold {color}")

    agents = store.agents(s)
    field("lead", state, state_color(state))
    field("subagents", f"{sum(1 for v in agents if v['running'])}/{len(agents)}", C.SWARM)
    if width >= 110:   # en terminales estrechas se prioriza la ayuda de teclas
        field("advisor", "on call" if s.advisor else "off", C.ADVISOR)
        field("tools", len(s.pending) if state != "ended" else 0, C.TOOL)
    if store.multi:
        field("cuenta", account or "todas", C.ADVISOR)
    if flash:
        t.append(f"   ◆ {flash}", style=f"bold {C.WAIT}")
        return t
    if hint:
        t.append(f"   {hint}", style=C.DIM)
        return t
    if sel:
        keys = "←→ subagente · r respuesta · p prompt · Esc soltar · ? ayuda"
    else:
        keys = "? ayuda · tab panel · , config · q salir"
    t.append(f"   {'auto' if auto else 'fija'} · {keys}", style=C.DIM)
    return t


def query(ui):
    """Texto por el que se filtra el log: lo que se está escribiendo o lo ya aplicado."""
    return ui["typing"].decode("utf-8", "ignore") if ui["typing"] is not None else ui["find"]


class Overlay:
    """Dibuja los toasts apilados en la esquina superior derecha, encima del resto."""

    def __init__(self, base, toasts, width, top=3, modal=None, modal_width=0):
        self.base, self.toasts, self.width, self.top = base, toasts, width, top
        self.modal, self.modal_width = modal, modal_width   # ventana centrada, por encima de todo

    def __rich_console__(self, console, options):
        lines = console.render_lines(self.base, options, pad=True)
        x = max(0, options.max_width - self.width - 1)
        y = self.top
        for toast in self.toasts:
            for row in console.render_lines(toast, options.update(width=self.width, height=None), pad=True):
                if y >= len(lines) - 1:
                    break
                parts = list(Segment.divide(lines[y], [x, x + self.width, options.max_width]))
                lines[y] = parts[0] + row + (parts[2] if len(parts) > 2 else [])
                y += 1
        if self.modal is not None:
            w = min(self.modal_width, options.max_width - 2)
            rows = console.render_lines(self.modal, options.update(width=w, height=None), pad=True)
            x = (options.max_width - w) // 2
            y = max(0, (len(lines) - len(rows)) // 2)
            for row in rows[: max(0, len(lines) - y)]:
                parts = list(Segment.divide(lines[y], [x, x + w, options.max_width]))
                lines[y] = parts[0] + row + (parts[2] if len(parts) > 2 else [])
                y += 1
        for line in lines:
            yield from line
            yield Segment.line()


# La ayuda (tecla ?): grupos de (teclas, qué hacen)
KEYS = [
    ("Sesiones", [
        ("↑ ↓  ·  j k", "cambia de sesión (con el foco en sesiones)"),
        ("a", "vuelve al modo automático"),
        ("Enter", "salta al panel de tmux de la sesión"),
        ("c", "filtra por cuenta, si hay más de una"),
        ("v", "conversación de la sesión"),
        ("D", "abre el último .md que escribió (spec, plan…)"),
    ]),
    ("Subagentes", [
        ("← →  ·  h l", "selecciona un subagente"),
        ("r  ·  p", "su última respuesta  ·  su prompt"),
        ("Esc", "suelta la selección"),
        ("t", "muestra u oculta los ya terminados"),
        ("F", "foco: log, actividad y gráfico solo de ese agente"),
    ]),
    ("Session log", [
        ("s", "cursor en el log (Enter abre el detalle)"),
        ("f", "solo las entradas de la sesión enfocada"),
        ("/", "busca texto (Enter aplica, Esc borra)"),
        ("y", "copia lo que tienes delante"),
    ]),
    ("Panel derecho", [
        ("o", "tareas: segundo plano y pendientes"),
        ("n", "registro de novedades"),
        ("i", "actividad, en terminales estrechas"),
        ("AvPág RePág", "desplaza el panel  ·  también espacio y b"),
    ]),
    ("Avisos", [
        ("x", "cierra los toasts en pantalla"),
        ("X", "apaga o enciende los toasts"),
        ("m", "silencia el pitido y el aviso de tmux"),
    ]),
    ("Apariencia y ajustes", [
        ("d", "diseño del panel de agentes"),
        ("L", "evoluciones de cada rostro (h/l personaje, j/k nivel)"),
        ("T", "tema de colores"),
        ("E", "efectos visuales"),
        (",", "configuración: ajustes y cuentas"),
    ]),
    ("Paneles", [
        ("Tab  ·  Shift+Tab", "pasa el foco al panel siguiente o anterior"),
        ("j k", "se mueve dentro del panel con el foco"),
        ("gg  ·  G", "primero  ·  último"),
        ("Enter", "agentes: foco · log: detalle · actividad: despliega la lista"),
        ("e", "actividad: el archivo con sus cambios resaltados"),
    ]),
    ("General", [
        ("?", "esta ayuda"),
        ("q", "salir"),
    ]),
]


def help_panel(view, width, max_rows):
    """Ventana flotante con todas las teclas, en dos columnas si hay ancho."""
    key_w = 13

    def block(groups, col_w):
        out = []
        for title, rows in groups:
            if out:
                out.append(Text(""))
            out.append(Text(title.upper(), style=f"bold {C.SWARM}"))
            for keys, what in rows:
                line = Text(no_wrap=True, overflow="ellipsis")
                line.append(f"{keys:<{key_w}}", style=f"bold {C.LEAD}")
                line.append(trunc(what, col_w - key_w), style=C.FG)
                out.append(line)
        return out

    inner = width - 4
    if inner >= 96:   # dos columnas, repartiendo los grupos por número de filas
        half, left, count = sum(len(rows) + 2 for _, rows in KEYS) / 2, [], 0
        for group in KEYS:
            size = len(group[1]) + 2
            if left and count + size / 2 > half:
                break
            left.append(group)
            count += size
        col_w = (inner - 3) // 2
        a, b = block(left, col_w), block(KEYS[len(left):], col_w)
        lines = []
        for n in range(max(len(a), len(b))):
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append_text(a[n] if n < len(a) else Text(""))
            line.pad_right(max(0, col_w + 3 - len(line.plain)))
            line.append_text(b[n] if n < len(b) else Text(""))
            lines.append(line)
    else:
        lines = block(KEYS, inner)
    room = max(3, max_rows - 4)
    view["rows"] = room
    top = max(0, min(view["top"], len(lines) - room))
    view["top"] = top
    body = Text(no_wrap=True, overflow="ellipsis")
    chunk = lines[top: top + room]
    for line in chunk:
        body.append_text(line)
        body.append("\n")
    more = len(lines) > room
    body.append("\n" + ("j/k desplaza · " if more else "") + "cualquier otra tecla cierra", style=C.DIM)
    return Panel(body, title=label("teclas", "HELP"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {C.SWARM}", style=f"on {C.CARD_BG}", width=width,
                 subtitle=f"{top + 1}–{top + len(chunk)} de {len(lines)}" if more else None, subtitle_align="right")


def toast_panel(toast, width, now):
    icon, title, role, _ = NEWS[toast["kind"]]
    tint = role_color(role)
    left = max(0.0, 1 - (now - toast["born"]) / limits["toast_seconds"])
    body = Text(no_wrap=True, overflow="ellipsis")
    if toast["who"]:
        body.append(trunc(toast["who"], width - 4) + "\n", style=C.DIM)
    wrapped = textwrap.wrap(toast["text"], width - 4)[:2] or [""]
    body.append("\n".join(wrapped) + "\n", style=f"bold {C.FG}")
    bar = round(left * (width - 4))
    body.append("▔" * bar, style=tint)   # se va acortando: lo que le queda en pantalla
    return Panel(body, title=f"{icon} {title}", title_align="left", box=box.HEAVY,
                 border_style=f"bold {tint}", style=f"on {C.CARD_BG}", width=width)


def news_panel(store, ui, rows, width, now):
    """Registro de novedades: todo lo que salió como toast, con su hora."""
    body = Text(no_wrap=True, overflow="ellipsis")
    items = list(store.news)
    ui["rows"] = rows
    back = min(ui["scroll"], max(0, len(items) - rows))   # cuántas entradas hacia atrás
    ui["scroll"] = back
    last = len(items) - back
    chunk = items[max(0, last - rows): last]
    for n, item in enumerate(chunk):
        icon, _, role, _ = NEWS[item["kind"]]
        body.append(time.strftime("%H:%M:%S", time.localtime(item["ts"])) + " ", style=C.DIM)
        body.append(f"{icon} ", style=f"bold {role_color(role)}")
        text = f"{item['who']} · {item['text']}" if item["who"] else item["text"]
        body.append(trunc(text, max(10, width - 16)) + ("\n" if n < len(chunk) - 1 else ""), style=C.FG)
    if not items:
        body.append("todavía no hay novedades", style=C.DIM)
    pos = f"{max(0, last - rows) + 1}–{last} de {len(items)}" if len(items) > rows else None
    return Panel(body, title=label("novedades", "NEWS"), title_align="left", subtitle=pos,
                 subtitle_align="right", box=C.PANEL_BOX, border_style=C.WAIT)


def mode_hint(ui):
    if ui["help"] is not None:
        return "teclas · cualquier tecla cierra"
    if ui["gallery"] is not None:
        return "evoluciones · h/l personaje · j/k evolución · Esc cierra"
    if ui["entry"] is not None:
        return "detalle · j/k desplaza · gg/G principio/final · y copia · Esc o Enter cierra"
    free = ui["log"] is None and ui["typing"] is None and ui["settings"] is None and ui["session"] is None
    if ui["focus"] and free:
        return "◎ foco · j/k otro agente · i actividad · r respuesta · p prompt · F o Esc sale · tab panel"
    if free and ui["pane"] == "agentes":
        return "agentes · j/k elige · gg/G primero/último · Enter foco · r/p respuesta/prompt · tab panel"
    if free and ui["pane"] == "detalle":
        return ("actividad · j/k mueve · Enter despliega la lista · e cambios del archivo · gg/G · tab panel" if ui["dkind"] == "list"
                else "panel derecho · j/k desplaza · gg/G principio/final · tab panel")
    if ui["settings"] is not None:
        return "configuración: j/k mover · h/l cambiar · Enter alterna · r por defecto · Esc cerrar"
    if ui["typing"] is not None:
        return f"buscar: {query(ui)}▌   Enter aplica · Esc borra el filtro"
    if ui["log"] is not None:
        return "log · j/k entrada · Enter abre el detalle · gg/G primera/última · tab panel · Esc cierra"
    if ui["session"] is not None:
        if ui["session"]["pane"] == "out":
            return "jk desplaza · h vuelve a sesiones · AvPág/RePág · G en vivo · v cierra · q salir"
        return "jk sesión · l entra a la conversación · AvPág/RePág · v cierra · q salir"
    return None
