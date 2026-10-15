"""La pantalla del dashboard: compone todos los paneles y decide qué avisar."""

import time
from functools import partial

from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .agents_view import board, diagram
from .config import limits
from .faces import gallery_panel
from .help_view import help_panel
from .log_view import event_panel, log_cursor, log_events, log_panel, query
from .panels import (
    Overlay,
    detail_panel,
    footer,
    header,
    mode_hint,
    session_card,
    session_panel,
    side_panel,
    toast_panel,
)
from .settings import settings_panel
from .text import age, trunc
from .theme import C


def new_ui():
    return {"sel": None, "tab": "resp",
            "scroll": 0, "info": False, "rows": 1, "all": False,
            "only": True,                 # el log solo muestra la sesión elegida (tecla f: todas)
            "focus": None,                # subagente enfocado (tecla F): todo se centra en él
            "pane": "sesiones",           # panel con el foco del teclado (Tab): sesiones, agentes, log, detalle
            "panes": ["sesiones", "agentes", "log", "detalle"],   # los que se ven ahora
            "dcur": 0, "dlines": 0, "dkind": "text", "dmarks": {}, "ditems": {},   # cursor del panel derecho
            "open": set(),                # listas desplegadas en la actividad
            "g_at": 0.0,                  # cuándo llegó una g, para reconocer gg
            "entry": None,                # entrada del log abierta en ventana flotante (Enter)
            "tiers": {},                  # evolución vista de cada agente, para anunciar las nuevas
            "gallery": None,              # ventana de evoluciones (L): {"face", "tier"}
            "layout": "lista",  # diseño del panel de agentes (tecla d)
            "theme": "clasico", "alerts": True, "effects": True,
            "cheat": "", "cheat_at": 0.0, "cheat_undo": None,   # lo tecleado hacia un truco
            "god": False, "rain_until": 0.0,
            "help": None,                 # ventana de teclas abierta: {"top": fila, "rows": n}
            "settings": None,             # ventana de configuración abierta: {"at": fila}
            "cfg": None,                  # la configuración cargada, para dibujar esa ventana
            "toasts_on": True,            # si las novedades salen como toast (tecla X)
            "toasts": [],                 # novedades en pantalla: {"born", "kind", "who", "text"}
            "news": False,                # registro de novedades en lugar de la actividad (tecla n)
            "tasks": False,               # lista de tareas en lugar de la actividad (tecla o)
            "tasks_of": None, "tasks_top": 0,   # sesión mostrada y primera fila visible
            "find": "", "typing": None,   # filtro del log y texto que se está escribiendo (bytes)
            "glitch": 0.0,                # hasta cuándo dura el parpadeo de un aviso
            "flash": ("", 0.0),  # (mensaje, hasta cuándo) en la barra inferior
            "session": None,  # conversación abierta (v): {"top", "rows", "last", "pane": "list" | "out"}
            "log": None}      # cursor en el log (s): {"cur": evento | None, "scroll", "rows", "pane"}


def render(console, store, focus_sid, account, now, ui=None):
    ui = ui if ui is not None else new_ui()
    width, height = console.size
    everyone = store.ordered(account)
    sessions = store.shown(everyone, now, ui["all"], focus_sid)
    by_id = {s.sid: s for s in everyone}
    focus = by_id.get(focus_sid) or store.auto_focus(sessions)
    dh = 15 if height < 34 else 21 if height < 46 else 25
    if ui["layout"] == "diagrama" and height >= 40:
        dh += 4   # rostros con sus barras: el diagrama necesita más alto que los demás diseños
    wide = width >= 108
    side_w = 34 if wide else 0
    chosen = next((v for v in store.visible(focus, now, ui["all"], ui["sel"]) if v["id"] == ui["sel"]), None)
    # el panel con el foco del teclado (Tab) se marca con un borde de color
    ui["panes"] = (["sesiones"] if wide else []) + (["agentes"] if ui["session"] is None else []) + ["log", "detalle"]
    if ui["pane"] not in ui["panes"]:
        ui["pane"] = ui["panes"][0]

    root = Layout()
    root.split_column(
        Layout(header(store, everyone, account, now, glitch=now < ui["glitch"], god=ui["god"],
                      focus=chosen if ui["focus"] else None),
               name="head", size=3),
        Layout(name="mid", size=dh + 2),
        bottom_layout(store, focus, account, chosen, ui, max(1, height - 3 - (dh + 2) - 1 - 2), width, now),
        Layout(footer(store, focus, focus_sid is None, account, now, sel=bool(ui["sel"]),
                      hint=mode_hint(ui), flash=ui["flash"][0] if now < ui["flash"][1] and ui["typing"] is None else None,
                      width=width),
               name="foot", size=1),
    )
    diag = agents_box(store, focus, ui, dh, width - side_w, now)
    if wide:
        root["mid"].split_row(side_layout(store, sessions, len(everyone) - len(sessions), focus, ui, dh, side_w, now),
                              Layout(diag))
    else:
        root["mid"].update(diag)
    ui["toasts"][:] = [t for t in ui["toasts"] if now - t["born"] < limits["toast_seconds"]]
    modal, modal_w = modal_window(store, focus, ui, width, height, now)
    if ui["toasts"] or modal is not None:
        tw = max(30, min(46, width // 2))
        root = Overlay(root, [toast_panel(t, tw, now) for t in ui["toasts"]], tw,
                       modal=modal, modal_width=modal_w)
    return root, sessions, focus


def bottom_layout(store, focus, account, chosen, ui, rows, width, now):
    """El log y el panel de detalle: lado a lado si caben; si no, el que toca según el foco."""
    split = width >= 100
    detail_w = max(44, width * 2 // 5) if split else width
    detail = detail_panel(store, focus, ui, rows, detail_w, now)
    events, title, narrowed = log_events(store, focus, account, chosen, ui["only"], query(ui))
    lg = ui["log"]
    log_w = width - detail_w if split else width
    if lg is not None:
        log = log_panel(store, focus, events, title, narrowed, rows, log_w, cursor=log_cursor(events, lg),
                        border=C.LEAD if lg["pane"] == "list" else C.LINE)
    else:
        log = log_panel(store, focus, events, title, narrowed, rows, log_w,
                        border=C.SWARM if chosen is not None else C.LINE)
    hot = f"bold {C.LEAD}"
    if ui["pane"] == "log":
        log.border_style = hot
    if ui["pane"] == "detalle":
        detail.border_style = hot
    bottom = Layout(name="bottom")
    if split:
        bottom.split_row(Layout(log), Layout(detail, size=detail_w))
    elif ui["pane"] in ("log", "detalle"):
        bottom.update(log if ui["pane"] == "log" else detail)
    elif lg is not None:
        bottom.update(detail if lg["pane"] == "out" else log)
    else:
        bottom.update(detail if ui["sel"] or ui["info"] or ui["tasks"] or ui["news"] else log)
    return bottom


def agents_box(store, focus, ui, dh, width, now):
    """El panel del medio: la conversación abierta (v) o el lead con sus subagentes."""
    if ui["session"] is not None:
        convo = session_panel(store, focus, ui["session"], dh, width, now)
        if convo is not None:
            return convo
    resting = (ui["effects"] and focus is not None and store.state(focus) in ("idle", "ended")
               and now - focus.last > 60)
    storm = now < ui["rain_until"]   # idkfa: llueve aunque haya trabajo
    draw = diagram if ui["layout"] == "diagrama" else partial(board, layout=ui["layout"])
    body = draw(store, focus, max(40, width - 4), dh, now, ui["sel"], ui["all"],
                idle_rain="force" if storm else resting, focus=ui["focus"])
    return Panel(body, box=C.PANEL_BOX, border_style=f"bold {C.LEAD}" if ui["pane"] == "agentes" else C.LINE,
                 title=trunc(focus.title, 60) if focus and focus.title else None, title_align="left",
                 subtitle=f"diseño: {ui['layout']} · d cambia", subtitle_align="right")


def side_layout(store, sessions, hidden, focus, ui, dh, side_w, now):
    """La lista de sesiones y, con una conversación abierta, la ficha de esa sesión debajo."""
    side = Layout(size=side_w)
    active = (ui["session"] is not None and ui["session"]["pane"] == "list") or ui["pane"] == "sesiones"
    card, card_h = session_card(store, focus, now) if ui["session"] is not None and focus else (None, 0)
    if card is None:
        side.update(side_panel(store, sessions, focus, dh, now, active=active, hidden=hidden))
        return side
    card_h = min(card_h, dh - 4)   # deja sitio para el cursor y una sesión vecina
    side.split_column(Layout(side_panel(store, sessions, focus, dh - card_h, now, active=active, hidden=hidden)),
                      Layout(card, size=card_h))
    return side


def modal_window(store, focus, ui, width, height, now):
    """La ventana flotante abierta, si hay una, y su ancho."""
    if ui["help"] is not None:
        return help_panel(ui["help"], min(112, width - 2), height - 4), min(112, width - 2)
    if ui["settings"] is not None:
        return settings_panel(ui["cfg"], store, ui["settings"], min(66, width - 2), height - 4), min(66, width - 2)
    if ui["gallery"] is not None:   # evoluciones de los rostros (tecla L)
        return gallery_panel(ui["gallery"], store, focus, now, min(84, width - 2), height - 2), min(84, width - 2)
    if ui["entry"] is not None:   # detalle de una entrada del log, en ventana flotante
        mw = min(120, width - 4)
        return event_panel(store, ui["entry"]["e"], ui["entry"], max(8, height - 6), mw, now), mw
    return None, 0


def watch(store, sessions, seen, now):
    """Novedades (sid, tipo, texto) por cambios de estado desde la pasada anterior."""
    out = []
    for s in sessions:
        state = store.state(s)
        why = store.stuck(s, now)
        if why and not seen.get(("stuck", s.sid)):
            out.append((s.sid, "stuck", why[0]))
        seen[("stuck", s.sid)] = bool(why)
        was, since = seen.get(s.sid, (None, now))
        if was == state:
            continue
        if was is not None:
            if state == "waiting":
                out.append((s.sid, "wait", f"necesita {store.waiting_for(s)}"))
            elif state == "idle" and was in ("busy", "waiting") and now - since >= 60:
                out.append((s.sid, "done", f"terminó tras {age(now - since)}"))
        # el reloj corre desde que empezó a trabajar; pasar por "waiting" no lo reinicia
        if not (state in ("busy", "waiting") and was in ("busy", "waiting")):
            since = now
        seen[s.sid] = (state, since)
    out += store.alerts
    store.alerts.clear()
    return out


def shown_text(store, focus, ui, now):
    """Lo que el usuario tiene delante y tendría sentido copiar."""
    if ui["log"] is not None:
        chosen = next((v for v in store.visible(focus, now, ui["all"], ui["sel"]) if v["id"] == ui["sel"]), None)
        events = log_events(store, focus, account=None, agent=chosen, only_focus=ui["only"], find=query(ui))[0]
        if not events:
            return ""
        e = events[log_cursor(events, ui["log"])]
        ref = e[6]
        if ref and ref["k"] == "tool":
            return ref["out"] or ""
        if ref and ref["k"] == "prompt":
            return ref["text"]
        if ref and ref["k"] == "agent":
            v = next((v for v in store.agents(store.sessions[e[1]]) if v["id"] == ref["id"]), None) \
                if e[1] in store.sessions else None
            return (v["response"] or v["prompt"]) if v else ""
        return e[4]
    if ui["sel"] and focus:
        v = next((v for v in store.agents(focus) if v["id"] == ui["sel"]), None)
        return (v["prompt"] if ui["tab"] == "prompt" else v["response"]) if v else ""
    if ui["session"] is not None and focus:
        return next((it["text"] for it in reversed(focus.items) if it["k"] == "text"), "")
    return ""


def boot(console, store):
    """Secuencia de arranque de un segundo, con los datos reales de lo que se cargó."""
    live = [s for s in store.sessions.values() if s.sid in store.live]
    steps = [
        (f"▚▚ CC-DASH v{__version__} // INIT", f"bold {C.LEAD}"),
        (f"   cuentas ........ {len(store.active)}" + (f" (+{len(store.off)} deshabilitadas)" if store.off else ""), C.FG),
        (f"   transcripts .... {store.source.transcripts}", C.FG),
        (f"   sesiones ....... {len(store.sessions)} · {len(live)} enlazadas", C.FG),
        (f"   eventos ........ {len(store.log)}", C.FG),
        ("   ENLACE ESTABLECIDO", f"bold {C.TOOL}"),
    ]
    console.print()
    for text, style in steps:
        for n in range(0, len(text) + 1, 6):
            console.print(Text(text[:n], style=style), end="\r")
            time.sleep(0.012)
        console.print(Text(text, style=style))
        time.sleep(0.09)
    time.sleep(0.25)
