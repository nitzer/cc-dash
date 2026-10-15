"""El session log: qué entradas se ven, su lista y el detalle de una entrada."""

import textwrap
import time

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from .text import trunc
from .theme import C, label, role_color


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


class DetailLines:
    """Las líneas (texto, estilo) del detalle de una entrada, con secciones y texto ajustado al ancho."""

    def __init__(self, inner):
        self.inner = inner
        self.lines = []

    def section(self, name):
        if self.lines:   # la primera sección no lleva separación
            self.lines.append(("", ""))
        self.lines.append((name.upper(), f"bold {C.SWARM}"))

    def add(self, body, st=C.FG):
        for raw in (body or "").expandtabs(4).splitlines() or [""]:
            for n, part in enumerate(textwrap.wrap(raw, self.inner - 2, replace_whitespace=False,
                                                   drop_whitespace=False) or [""]):
                self.lines.append(("  " + (part.lstrip() if n else part), st))


def prompt_detail(out, store, s, ref):
    out.section("prompt")
    out.add(ref["text"])
    return "", C.DIM


def tool_detail(out, store, s, ref):
    if ref["label"]:
        out.add(ref["label"], "bold")
    out.section("salida")
    if ref["out"] is None:
        out.add("todavía sin resultado" if s and store.state(s) == "busy" else "sin resultado registrado", C.DIM)
    else:
        out.add(ref["out"].strip() or "(sin salida)", C.ERR if ref["err"] else C.FG)
    out.section("entrada")
    for key, value in ref["input"].items():
        out.lines.append((f"  {key}", C.DIM))
        out.add(value)
    if ref["err"]:
        return "✗ error", C.ERR
    return ("✓ terminado", C.TOOL) if ref["out"] is not None else ("en curso", C.SWARM)


def agent_detail(out, store, s, ref):
    v = next((v for v in store.agents(s) if v["id"] == ref["id"]), None) if s else None
    if not v:
        return "", C.DIM
    out.section(f"subagente {v['type']}" + (f" · {v['model']}" if v["model"] else ""))
    out.add(v["desc"])
    out.section("prompt")
    out.add(v["prompt"] or "(prompt vacío)")
    out.section("respuesta")
    out.add(v["response"] or ("todavía sin respuesta" if v["running"] else "(sin respuesta registrada)"),
            C.FG if v["response"] else C.DIM)
    if v["running"]:
        return "en marcha", C.SWARM
    if v["end"] and v["status"] != "completed":
        return "✗ " + v["status"], C.ERR
    return ("✓ terminado", C.TOOL) if v["end"] else ("interrumpido", C.DIM)


DETAILS = {"prompt": prompt_detail, "tool": tool_detail, "agent": agent_detail}


def event_panel(store, e, view, rows, width, now):
    """Recuadro de color con el detalle completo de la entrada del log bajo el cursor."""
    inside = view["pane"] == "out"
    frame = dict(box=C.CARD_BOX, border_style=C.LEAD if inside else C.SWARM, style=f"on {C.CARD_BG}",
                 title_align="left", subtitle_align="right")
    if e is None:
        return Panel(Text("el log está vacío", style=C.DIM), title="detalle", **frame)
    ts, sid, _, tag, text, _, ref = e
    s = store.sessions.get(sid)
    out = DetailLines(max(20, width - 4))
    detail = DETAILS.get(ref["k"]) if ref else None
    if detail:
        status, sst = detail(out, store, s, ref)
    else:
        out.add(text)
        status, sst = "", C.DIM
    lines = out.lines

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


def query(ui):
    """Texto por el que se filtra el log: lo que se está escribiendo o lo ya aplicado."""
    return ui["typing"].decode("utf-8", "ignore") if ui["typing"] is not None else ui["find"]
