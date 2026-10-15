"""El dashboard interactivo: composición de la pantalla y bucle de teclas."""

import os
import re
import shlex
import subprocess
import sys
import time
from collections import deque

try:
    import select
    import termios
    import tty
except ImportError:  # Windows
    termios = None

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .agents_view import board, diagram
from .config import LAYOUTS, LIMITS, TEXTS, apply_limits, config_path, limits, load_config, save_config
from .external import copy_text, jump, notify, open_doc, self_command, viewer_command
from .faces import FACES, gallery_panel, reached_tier
from .game import EVO, agent_game, lead_game
from .model import Store
from .panels import Overlay, detail_panel, event_panel, footer, header, help_panel, log_cursor, log_events, log_panel, mode_hint, query, session_card, session_panel, side_panel, toast_panel
from .repo import change_source
from .settings import apply_config, change_setting, setting_entries, settings_panel
from .text import age, is_doc, trunc
from .theme import C, THEMES, apply_theme


CHEATS = ("iddqd", "idkfa")   # sí, esos


def new_ui():
    return {"sel": None, "tab": "resp",
            "scroll": 0, "info": False, "rows": 1, "all": False, "only": False,
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
    sessions = store.ordered(account)
    by_id = {s.sid: s for s in sessions}
    focus = by_id.get(focus_sid) or store.auto_focus(sessions)
    compact = height < 34
    dh = 15 if compact else 21 if height < 46 else 25
    if ui["layout"] == "diagrama" and height >= 40:
        dh += 4   # rostros con sus barras: el diagrama necesita más alto que los demás diseños
    wide = width >= 108
    side_w = 34 if wide else 0
    rows = max(1, height - 3 - (dh + 2) - 1 - 2)
    split = width >= 100
    detail_w = max(44, width * 2 // 5) if split else width

    detail = detail_panel(store, focus, ui, rows, detail_w, now)
    chosen = next((v for v in store.visible(focus, now, ui["all"], ui["sel"]) if v["id"] == ui["sel"]), None)
    events, title, narrowed = log_events(store, focus, account, chosen, ui["only"], query(ui))
    lg = ui["log"]
    log_w = width - detail_w if split else width
    if lg is not None:
        at = log_cursor(events, lg)
        log = log_panel(store, focus, events, title, narrowed, rows, log_w, cursor=at,
                        border=C.LEAD if lg["pane"] == "list" else C.LINE)
    else:
        log = log_panel(store, focus, events, title, narrowed, rows, log_w,
                        border=C.SWARM if chosen is not None else C.LINE)
    # el panel con el foco del teclado (Tab) se marca con un borde de color
    ui["panes"] = (["sesiones"] if wide else []) + (["agentes"] if ui["session"] is None else []) + ["log", "detalle"]
    if ui["pane"] not in ui["panes"]:
        ui["pane"] = ui["panes"][0]
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

    root = Layout()
    root.split_column(
        Layout(header(store, sessions, account, now, glitch=now < ui["glitch"], god=ui["god"],
                      focus=chosen if ui["focus"] else None),
               name="head", size=3),
        Layout(name="mid", size=dh + 2),
        bottom,
        Layout(footer(store, focus, focus_sid is None, account, now, sel=bool(ui["sel"]),
                      hint=mode_hint(ui), flash=ui["flash"][0] if now < ui["flash"][1] and ui["typing"] is None else None,
                      width=width),
               name="foot", size=1),
    )
    if ui["session"] is not None:
        diag = session_panel(store, focus, ui["session"], dh, width - side_w, now)
    else:
        diag = None
    if diag is None:
        inner = max(40, width - side_w - 4)
        resting = (ui["effects"] and focus is not None and store.state(focus) in ("idle", "ended")
                   and now - focus.last > 60)
        storm = now < ui["rain_until"]   # idkfa: llueve aunque haya trabajo
        if ui["layout"] == "diagrama":
            body = diagram(store, focus, inner, dh, now, ui["sel"], ui["all"],
                           idle_rain="force" if storm else resting, focus=ui["focus"])
        else:
            body = board(store, focus, inner, dh, now, ui["sel"], ui["all"], ui["layout"],
                         idle_rain="force" if storm else resting, focus=ui["focus"])
        diag = Panel(body, box=C.PANEL_BOX, border_style=f"bold {C.LEAD}" if ui["pane"] == "agentes" else C.LINE,
                     title=trunc(focus.title, 60) if focus and focus.title else None, title_align="left",
                     subtitle=f"diseño: {ui['layout']} · d cambia", subtitle_align="right")
    if wide:
        side = Layout(size=side_w)
        active = (ui["session"] is not None and ui["session"]["pane"] == "list") or ui["pane"] == "sesiones"
        card, card_h = session_card(store, focus, now) if ui["session"] is not None and focus else (None, 0)
        card_h = min(card_h, dh - 4)   # deja sitio para el cursor y una sesión vecina
        if card is not None:
            side.split_column(Layout(side_panel(store, sessions, focus, dh - card_h, now, active=active)),
                              Layout(card, size=card_h))
        else:
            side.update(side_panel(store, sessions, focus, dh, now, active=active))
        root["mid"].split_row(side, Layout(diag))
    else:
        root["mid"].update(diag)
    ui["toasts"][:] = [t for t in ui["toasts"] if now - t["born"] < limits["toast_seconds"]]
    modal, modal_w = None, 0
    if ui["help"] is not None:
        modal_w = min(112, width - 2)
        modal = help_panel(ui["help"], modal_w, height - 4)
    elif ui["settings"] is not None:
        modal_w = min(66, width - 2)
        modal = settings_panel(ui["cfg"], store, ui["settings"], modal_w, height - 4)
    elif ui["gallery"] is not None:   # evoluciones de los rostros (tecla L)
        modal_w = min(84, width - 2)
        modal = gallery_panel(ui["gallery"], store, focus, now, modal_w, height - 2)
    elif ui["entry"] is not None:   # detalle de una entrada del log, en ventana flotante
        modal_w = min(120, width - 4)
        modal = event_panel(store, ui["entry"]["e"], ui["entry"], max(8, height - 6), modal_w, now)
    if ui["toasts"] or modal is not None:
        tw = max(30, min(46, width // 2))
        root = Overlay(root, [toast_panel(t, tw, now) for t in ui["toasts"]], tw,
                       modal=modal, modal_width=modal_w)
    return root, sessions, focus


def read_keys(fd, timeout):
    if termios is None:
        import msvcrt
        time.sleep(timeout)
        keys = ""
        while msvcrt.kbhit():
            keys += msvcrt.getwch()
        arrows = {"\xe0H": b"k", "\xe0P": b"j", "\xe0K": b"h", "\xe0M": b"l",
                  "\xe0I": b"\x1b[5~", "\xe0Q": b"\x1b[6~"}
        return arrows.get(keys, keys.encode("utf-8", "ignore"))
    if fd is None:
        time.sleep(timeout)
        return b""
    ready, _, _ = select.select([fd], [], [], timeout)
    return os.read(fd, 64) if ready else b""


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
        (f"   transcripts .... {len(store.files)}", C.FG),
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


def now_mtime():
    try:
        return os.path.getmtime(config_path())
    except OSError:
        return 0.0


def reload_limits(cfg, ui, seen, now):
    """Relee umbrales e interruptores si el archivo de configuración cambió por fuera."""
    if now - seen[1] < 2:
        return
    seen[1] = now
    stamp = now_mtime()
    if stamp == seen[0]:
        return
    seen[0] = stamp
    try:
        fresh = load_config()
    except SystemExit:   # archivo a medio escribir o con errores: se mantiene lo que había
        return
    for key in (*LIMITS, *TEXTS, "toasts", "alerts", "effects", "disabled", "viewer_popup"):
        cfg[key] = fresh[key]
    ui["toasts_on"], ui["alerts"], ui["effects"] = cfg["toasts"], cfg["alerts"], cfg["effects"]
    apply_limits(cfg)
    return True


def split_keys(raw):
    """Separa lo leído en teclas sueltas: al mantener una tecla llegan varias juntas."""
    return re.findall(rb"\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Za-z]|[\x00-\xff]", raw, re.S)


def run(accounts, once, cfg):
    console = Console()
    store = Store(accounts)
    store.off = set(cfg["disabled"])
    store.poll(time.time())
    account = None
    store.ready = True
    ui = new_ui()
    ui["layout"], ui["theme"], ui["alerts"] = cfg["layout"], cfg["theme"], cfg["alerts"]
    ui["effects"], ui["toasts_on"], ui["cfg"] = cfg["effects"], cfg["toasts"], cfg
    apply_theme(ui["theme"])
    apply_limits(cfg)
    cfg_seen = [now_mtime(), 0.0]   # [mtime de la configuración, última vez que se miró]
    if once or not sys.stdout.isatty():
        console.print(render(console, store, None, account, time.time(), ui)[0])
        return

    fd = sys.stdin.fileno() if termios is not None and sys.stdin.isatty() else None
    saved = termios.tcgetattr(fd) if fd is not None else None
    focus_sid = None
    seen = {}        # sid -> (estado, desde cuándo trabaja), para los avisos

    def view_doc(doc, cmd=None, title=None, note=None):
        """Abre un archivo con el visor (u otra orden) en un popup de tmux, o en la terminal entera sin tmux."""
        why = open_doc(cfg, doc, cmd=cmd, title=title)
        if why == "tty":   # sin tmux: el visor toma la terminal hasta que se cierre
            live.stop()
            if saved is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            subprocess.run(cmd or viewer_command(cfg, doc), shell=True)
            if fd is not None:
                tty.setcbreak(fd)
            live.start(refresh=True)
            why = ""
        ui["flash"] = (why or note or f"abierto {os.path.basename(doc)}", time.time() + 4)

    def open_entry(e):
        """Enter sobre una entrada del log: un .md se abre con el visor; lo demás, en una ventana flotante."""
        ref = e[6]
        path = (ref.get("input") or {}).get("file_path") if ref and ref["k"] == "tool" else None
        if is_doc(path):
            s = store.sessions.get(e[1])
            full = os.path.join(s.cwd if s else "", path)
            if os.path.isfile(full):
                view_doc(full)
                return
        ui["entry"] = {"e": e, "scroll": 0, "rows": 1, "pane": "out"}
    queue = deque()  # teclas leídas y aún sin procesar
    try:
        if fd is not None:
            tty.setcbreak(fd)
        if ui["effects"]:
            boot(console, store)
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                now = time.time()
                store.poll(now)
                root, sessions, focus = render(console, store, focus_sid, account, now, ui)
                for sid, kind, text in watch(store, sessions, seen, now):
                    notify(store, ui, sid, kind, text, now)
                if focus is not None:
                    store.git_info(focus, now, refresh=True)
                    # cada 10 niveles el rostro evoluciona: se anuncia una vez por agente
                    for key, name, g in [(focus.sid, "el lead", lead_game(focus))] + [
                            (v["id"], v["type"], agent_game(v)) for v in store.agents(focus)]:
                        before = ui["tiers"].get(key)
                        ui["tiers"][key] = g["tier"]
                        if before is not None and g["tier"] > before:
                            notify(store, ui, focus.sid, "evo", f"{name} llegó al nivel {g['lvl']}: {EVO[g['tier']]}", now)
                            ui["glitch"] = now + 0.8
                if reload_limits(cfg, ui, cfg_seen, now) and store.off != set(cfg["disabled"]):
                    store.off = set(cfg["disabled"])
                    store.t_glob = store.t_live = 0.0
                if not queue:
                    live.update(root, refresh=True)
                    queue.extend(split_keys(read_keys(fd, 0.12)))
                if not queue:
                    continue
                keys = queue.popleft()
                if ui["typing"] is not None:
                    # escribiendo una búsqueda: todas las teclas son texto
                    if keys in (b"\r", b"\n"):
                        ui["find"], ui["typing"] = query(ui), None
                    elif keys == b"\x1b":
                        ui["find"], ui["typing"] = "", None
                    elif keys in (b"\x7f", b"\x08"):
                        ui["typing"] = query(ui)[:-1].encode()
                    elif keys == b"\x03":
                        break
                    elif len(keys) == 1 and keys[0] >= 0x20:
                        ui["typing"] += keys
                    if ui["log"] is not None:
                        ui["log"]["cur"], ui["log"]["scroll"] = None, 0
                    continue
                if ui["help"] is not None:
                    # ventana de teclas: j/k la desplazan, cualquier otra tecla la cierra
                    if keys == b"\x03":
                        break
                    if keys in (b"j", b"\x1b[B", b"\x1b[6~"):
                        ui["help"]["top"] += 1 if keys != b"\x1b[6~" else ui["help"]["rows"]
                    elif keys in (b"k", b"\x1b[A", b"\x1b[5~"):
                        ui["help"]["top"] = max(0, ui["help"]["top"] - (1 if keys != b"\x1b[5~" else ui["help"]["rows"]))
                    else:
                        ui["help"] = None
                    continue
                if keys == b"?" and ui["settings"] is None:
                    ui["help"] = {"top": 0, "rows": 1}
                    continue
                if ui["gallery"] is not None:
                    # evoluciones: h/l cambian de personaje, j/k de evolución
                    gv = ui["gallery"]
                    if keys in (b"\x1b", b"q", b"L", b"\r", b"\n"):
                        ui["gallery"] = None
                    elif keys == b"\x03":
                        break
                    elif keys in (b"l", b"\x1b[C"):
                        gv["face"] = (gv["face"] + 1) % len(FACES)
                        gv["tier"] = 4   # abre en la evolución más alta descubierta
                    elif keys in (b"h", b"\x1b[D"):
                        gv["face"] = (gv["face"] - 1) % len(FACES)
                        gv["tier"] = 4   # abre en la evolución más alta descubierta
                    elif keys in (b"k", b"\x1b[A"):
                        gv["tier"] = min(reached_tier(store, focus, gv["face"] % len(FACES)), gv["tier"] + 1)
                    elif keys in (b"j", b"\x1b[B"):
                        gv["tier"] = max(0, gv["tier"] - 1)
                    continue
                if keys == b"L" and ui["settings"] is None and ui["help"] is None:
                    sel_v = next((v for v in store.agents(focus) if v["id"] == ui["sel"]), None) if focus else None
                    start = sum(map(ord, sel_v["type"])) % len(FACES) if sel_v else 0
                    ui["gallery"] = {"face": start, "tier": agent_game(sel_v)["tier"] if sel_v else 0}
                    continue
                if ui["entry"] is not None:
                    # ventana flotante con el detalle de una entrada del log
                    ev = ui["entry"]
                    page = max(1, ev["rows"] - 2)
                    if keys == b"g" and now - ui["g_at"] > 0.6:
                        ui["g_at"] = now
                        continue
                    if keys in (b"\x1b", b"q", b"\r", b"\n", b"h"):
                        ui["entry"] = None
                    elif keys == b"\x03":
                        break
                    elif keys in (b"j", b"\x1b[B"):
                        ev["scroll"] += 1
                    elif keys in (b"k", b"\x1b[A"):
                        ev["scroll"] = max(0, ev["scroll"] - 1)
                    elif keys in (b" ", b"\x1b[6~"):
                        ev["scroll"] += page
                    elif keys in (b"b", b"\x1b[5~"):
                        ev["scroll"] = max(0, ev["scroll"] - page)
                    elif keys in (b"g", b"\x1b[H"):
                        ev["scroll"], ui["g_at"] = 0, 0.0
                    elif keys in (b"G", b"\x1b[F"):
                        ev["scroll"] = 10 ** 6
                    elif keys == b"y":
                        text = shown_text(store, focus, ui, now)
                        if text:
                            copy_text(text)
                        ui["flash"] = (f"copiado ({len(text)} caracteres)" if text else "nada que copiar", now + 3)
                    continue
                if ui["settings"] is not None:
                    # ventana de configuración: captura todas las teclas
                    rows = [e for e in setting_entries(store) if isinstance(e, tuple)]
                    at = ui["settings"]["at"] % len(rows)
                    item = rows[at]
                    if keys in (b",", b"\x1b", b"q"):
                        ui["settings"] = None
                    elif keys == b"\x03":
                        break
                    elif keys in (b"j", b"\x1b[B", b"\t"):
                        ui["settings"]["at"] = (at + 1) % len(rows)
                    elif keys in (b"k", b"\x1b[A"):
                        ui["settings"]["at"] = (at - 1) % len(rows)
                    elif keys in (b"l", b"\x1b[C", b"\r", b"\n", b" ", b"h", b"\x1b[D", b"r"):
                        change_setting(cfg, item, 0 if keys == b"r" else -1 if keys in (b"h", b"\x1b[D") else 1)
                        apply_config(cfg, ui, store)
                        cfg_seen[0] = now_mtime()
                        if account in store.off:   # el filtro apuntaba a una cuenta que se acaba de apagar
                            account = None
                        if focus_sid and getattr(store.sessions.get(focus_sid), "account", None) in store.off:
                            focus_sid = None
                    continue
                if len(keys) == 1 and keys.isalpha():
                    # trucos: las letras también son atajos, así que al completarse uno
                    # se deshace lo que hicieron por el camino
                    if now - ui["cheat_at"] > 2:
                        ui["cheat"] = ""
                    ui["cheat_at"] = now
                    typed = ui["cheat"] + keys.decode()
                    ui["cheat"] = typed if any(c.startswith(typed) for c in CHEATS) else "i" if keys == b"i" else ""
                    if ui["cheat"] == "i":
                        ui["cheat_undo"] = (ui["layout"], ui["info"], ui["only"], focus_sid, ui["sel"])
                    if ui["cheat"] in CHEATS:
                        ui["layout"], ui["info"], ui["only"], focus_sid, ui["sel"] = ui["cheat_undo"]
                        cfg["layout"] = ui["layout"]
                        try:
                            save_config(cfg)
                        except OSError:
                            pass
                        if ui["cheat"] == "iddqd":
                            ui["god"] = not ui["god"]
                            text = ("invulnerabilidad activada · los subagentes aún pueden fallar" if ui["god"]
                                    else "god mode desactivado · vuelves a ser mortal")
                        else:
                            ui["rain_until"] = now + 20
                            text = "todas las llaves, armas y munición · tormenta de 20 segundos"
                        ui["toasts"].append({"born": now, "kind": "cheat", "who": ui["cheat"].upper(), "text": text})
                        ui["glitch"], ui["cheat"] = now + 1.2, ""
                        continue
                    if ui["cheat"] == "iddq":   # esta q es del truco, no un "salir"
                        continue
                if keys in (b"q", b"\x03"):
                    break
                if keys == b",":
                    ui["settings"] = {"at": 0}
                    continue
                if keys == b"/":
                    ui["typing"] = ui["find"].encode()
                    continue
                if keys == b"y":
                    text = shown_text(store, focus, ui, now)
                    if text:
                        copy_text(text)
                    ui["flash"] = (f"copiado ({len(text)} caracteres)" if text
                                   else "nada que copiar: abre una entrada del log (s) o un subagente", now + 3)
                    continue
                if keys == b"D":
                    doc = next((p for p in reversed(focus.docs) if os.path.isfile(p)), None) if focus else None
                    view_doc(doc) if doc else None
                    if not doc:
                        ui["flash"] = ("esta sesión no ha escrito ningún .md", now + 4)
                    continue
                if keys == b"x":
                    ui["toasts"].clear()
                    continue
                if keys == b"X":
                    ui["toasts_on"] = cfg["toasts"] = not ui["toasts_on"]
                    ui["toasts"].clear()
                    ui["flash"] = ("toasts encendidos" if ui["toasts_on"]
                                   else "toasts apagados · las novedades siguen en el registro (n)", now + 3)
                    try:
                        save_config(cfg)
                    except OSError:
                        pass
                    continue
                if keys in (b"o", b"n"):
                    which, other = ("tasks", "news") if keys == b"o" else ("news", "tasks")
                    ui[which], ui[other] = not ui[which], False
                    ui["scroll"], ui["tasks_of"] = 0, None
                    if ui[which]:
                        ui["sel"] = ui["log"] = None   # el panel de la derecha pasa a ser este
                    continue
                if keys == b"E":
                    ui["effects"] = cfg["effects"] = not ui["effects"]
                    ui["flash"] = ("efectos activados" if ui["effects"] else "efectos apagados", now + 2)
                    try:
                        save_config(cfg)
                    except OSError:
                        pass
                    continue
                # navegación común a todos los paneles: j/k, gg (primero) y G (último)
                down, up = keys in (b"j", b"\x1b[B"), keys in (b"k", b"\x1b[A")
                if keys == b"g":
                    if now - ui["g_at"] > 0.6:   # primera g: se espera la segunda
                        ui["g_at"] = now
                        continue
                    ui["g_at"] = 0.0
                first = keys in (b"g", b"\x1b[H", b"\x1b[1~")
                last = keys in (b"G", b"\x1b[F", b"\x1b[4~")
                if keys in (b"\t", b"\x1b[Z") and ui["session"] is None:
                    # Tab rota el foco: sesiones → agentes → session log → panel derecho
                    panes = ui["panes"]
                    cur = ui["pane"] if ui["pane"] in panes else panes[0]
                    nxt = panes[(panes.index(cur) + (1 if keys == b"\t" else -1)) % len(panes)]
                    if nxt == "log":
                        ui["log"] = ui["log"] or {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}
                        ui["log"]["pane"] = "list"
                    else:
                        ui["log"] = None
                    if nxt == "agentes" and focus is not None:
                        vis = [v["id"] for v in store.visible(focus, now, ui["all"], ui["sel"])]
                        if ui["sel"] is None and vis:
                            ui["sel"], focus_sid, ui["scroll"] = vis[0], focus.sid, 0
                    if nxt == "detalle":
                        ui["dcur"] = 0
                    ui["pane"] = nxt
                    continue
                if ui["pane"] == "log" and ui["log"] is None and (down or up or first or last):
                    ui["log"] = {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}
                lg = ui["log"]
                if lg is not None:
                    # cursor en el log: la lista (j/k elige entrada) y su detalle (j/k desplaza)
                    chosen = next((v for v in store.visible(focus, now, ui["all"], ui["sel"])
                                   if v["id"] == ui["sel"]), None)
                    events = log_events(store, focus, account, chosen, ui["only"], query(ui))[0]
                    at = log_cursor(events, lg)
                    page = max(1, lg["rows"] - 2)
                    inside = lg["pane"] == "out"
                    move = None
                    handled = True
                    if keys in (b"s", b"S", b"\x1b"):
                        ui["log"] = None
                    elif keys in (b"l", b"\x1b[C", b"\r", b"\n", b" "):
                        if events:
                            open_entry(events[at])
                    elif inside and keys in (b"k", b"\x1b[A"):
                        lg["scroll"] = max(0, lg["scroll"] - 1)
                    elif inside and keys in (b"j", b"\x1b[B"):
                        lg["scroll"] += 1
                    elif keys in (b"k", b"\x1b[A"):
                        move = at - 1
                    elif keys in (b"j", b"\x1b[B"):
                        move = at + 1
                    elif keys in (b"b", b"\x1b[5~"):
                        move = at - page
                    elif keys == b"\x1b[6~":
                        move = at + page
                    elif inside and first:
                        lg["scroll"] = 0
                    elif inside and last:
                        lg["scroll"] = 10 ** 6
                    elif first:
                        move = 0
                    elif last:
                        move = len(events)
                    else:
                        handled = False
                    if move is not None and events:
                        move = max(0, move)
                        # en la última entrada el cursor queda libre y sigue a las nuevas
                        lg["cur"] = None if move >= len(events) - 1 else events[move]
                        lg["scroll"] = 0
                    if handled:
                        continue
                elif keys in (b"s", b"S"):
                    ui["log"] = {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}
                    ui["session"], ui["pane"] = None, "log"
                    continue
                view = ui["session"]
                if view is not None:
                    # dos focos al estilo vim: la lista (j/k elige sesión) y la conversación (j/k desplaza)
                    page = max(1, view["rows"] - 2)
                    here = view["last"] if view["top"] is None else view["top"]
                    inside = view["pane"] == "out"
                    handled = True
                    if keys in (b"v", b"V", b"\x1b"):
                        ui["session"] = None
                    elif keys in (b"h", b"\x1b[D"):
                        view["pane"] = "list"
                    elif keys in (b"l", b"\x1b[C", b"\r", b"\n"):
                        view["pane"] = "out"
                    elif inside and keys in (b"k", b"\x1b[A"):
                        view["top"] = max(0, here - 1)
                    elif inside and keys in (b"j", b"\x1b[B"):
                        view["top"] = here + 1
                    elif keys in (b"b", b"\x1b[5~"):
                        view["top"] = max(0, here - page)
                    elif keys in (b" ", b"\x1b[6~"):
                        view["top"] = here + page
                    elif first:
                        view["top"] = 0
                    elif last:
                        view["top"] = None
                    else:
                        handled = False
                    if handled:
                        continue
                elif keys in (b"v", b"V"):
                    if focus:
                        ui["session"] = {"top": None, "rows": 1, "last": 0, "pane": "list"}
                        ui["log"] = None
                    continue
                ids = [v["id"] for v in store.visible(focus, now, ui["all"], ui["sel"])]
                page = max(1, ui["rows"] - 1)
                pane = ui["pane"]
                if pane == "agentes" and (down or up or first or last):
                    # j/k recorren los subagentes; en modo foco, el foco los acompaña
                    if ids:
                        i = ids.index(ui["sel"]) if ui["sel"] in ids else (-1 if down else len(ids))
                        i = 0 if first else len(ids) - 1 if last else max(0, min(len(ids) - 1, i + (1 if down else -1)))
                        ui["sel"], focus_sid, ui["scroll"] = ids[i], focus.sid, 0
                        if ui["focus"]:
                            ui["focus"] = ui["sel"]
                    continue
                if pane == "agentes" and keys in (b"\r", b"\n"):
                    keys = b"F"   # Enter sobre un agente: modo foco
                if pane == "sesiones" and (first or last) and sessions:
                    focus_sid = (sessions[0] if first else sessions[-1]).sid
                    ui["sel"] = ui["focus"] = None
                    ui["scroll"] = 0
                    continue
                if pane == "detalle" and ui["dkind"] == "list" and keys == b"e":
                    # e: lo que cambió en el archivo bajo el cursor, según git
                    title = ui["dmarks"].get(ui["dcur"]) or ""
                    item = ui["ditems"].get(ui["dcur"])
                    if not (item and title.startswith("archivos")):
                        ui["flash"] = ("e muestra los cambios: pon el cursor sobre un archivo", now + 3)
                        continue
                    path = os.path.join(focus.cwd if focus else "", item)
                    against, note = change_source(path)
                    if against is None and not os.path.isfile(path):
                        ui["flash"] = (f"{os.path.basename(path)}: {note}", now + 3)
                    else:
                        if against is None:
                            note += ": solo el archivo, sin cambios marcados"
                        view_doc(path, cmd=f"{self_command()} changes {shlex.quote(path)}",
                                 note=f"{os.path.basename(path)}: {note}",
                                 title=f" cambios · {os.path.basename(path)} · {note} ")
                    continue
                if pane == "detalle" and ui["dkind"] == "list" and (
                        down or up or first or last or keys in (b"\r", b"\n", b"l", b" ", b"b", b"\x1b[6~", b"\x1b[5~")):
                    # actividad: un cursor por líneas; Enter despliega la lista bajo el cursor
                    n = max(1, ui["dlines"])
                    if keys in (b"\r", b"\n", b"l"):
                        title = ui["dmarks"].get(ui["dcur"])
                        if title:
                            ui["open"] ^= {title}
                    else:
                        step = 1 if down else -1 if up else page if keys in (b" ", b"\x1b[6~") else -page
                        ui["dcur"] = 0 if first else n - 1 if last else max(0, min(n - 1, ui["dcur"] + step))
                    continue
                if pane == "detalle" and (down or up or first or last):
                    # texto (respuesta, prompt, tareas, novedades): j/k desplazan línea a línea
                    if ui["tasks"] and ui["scroll"] < 0:
                        ui["scroll"] = ui["tasks_top"]
                    step = 1 if down else -1
                    if ui["news"] and not ui["sel"]:
                        step = -step
                    if first:
                        ui["scroll"] = 10 ** 6 if ui["news"] and not ui["sel"] else 0
                    elif last:
                        ui["scroll"] = 0 if ui["news"] and not ui["sel"] else 10 ** 6
                    else:
                        ui["scroll"] = max(0, ui["scroll"] + step)
                    continue
                if keys in (b"l", b"h", b"\x1b[C", b"\x1b[D"):
                    order = ([] if ui["focus"] else [None]) + ids   # en foco se pasa de un agente a otro
                    i = order.index(ui["sel"]) if ui["sel"] in order else 0
                    if order:
                        ui["sel"] = order[(i + (1 if keys in (b"l", b"\x1b[C") else -1)) % len(order)]
                    ui["scroll"] = 0
                    if ui["focus"]:
                        ui["focus"] = ui["sel"]
                    if ui["sel"]:
                        focus_sid = focus.sid  # que el modo auto no cambie de sesión mientras se lee
                elif keys == b"F":
                    if ui["focus"]:
                        ui["focus"] = None
                    elif ids:
                        ui["sel"] = ui["sel"] if ui["sel"] in ids else ids[0]
                        ui["focus"], focus_sid, ui["tab"], ui["scroll"] = ui["sel"], focus.sid, "act", 0
                        ui["tasks"] = ui["news"] = False
                    else:
                        ui["flash"] = ("no hay subagentes a la vista para enfocar (t muestra los terminados)", now + 3)
                elif keys == b"\x1b":
                    ui["sel"] = ui["focus"] = None
                elif keys in (b"p", b"r"):
                    if ui["sel"] is None and ids:
                        ui["sel"], focus_sid = ids[0], focus.sid
                    ui["tab"] = "prompt" if keys == b"p" else "resp"
                    ui["scroll"] = 0
                elif keys == b"i" and ui["focus"]:
                    ui["tab"], ui["scroll"] = "act", 0
                elif keys == b"i":
                    ui["info"] = not ui["info"]
                elif keys == b"t":
                    ui["all"] = not ui["all"]
                elif keys == b"f":
                    ui["only"] = not ui["only"]
                elif keys in (b"\r", b"\n"):
                    if focus:
                        ui["flash"] = (jump(store, focus), now + 4)
                elif keys in (b"T", b"m"):
                    if keys == b"T":
                        names = list(THEMES)
                        ui["theme"] = cfg["theme"] = names[(names.index(ui["theme"]) + 1) % len(names)]
                        apply_theme(ui["theme"])
                        ui["flash"] = (f"tema: {ui['theme']}", now + 2)
                    else:
                        ui["alerts"] = cfg["alerts"] = not ui["alerts"]
                        ui["flash"] = ("avisos activados" if ui["alerts"] else "avisos silenciados", now + 2)
                    try:
                        save_config(cfg)
                    except OSError:
                        pass
                elif keys == b"d":
                    ui["layout"] = LAYOUTS[(LAYOUTS.index(ui["layout"]) + 1) % len(LAYOUTS)]
                    cfg["layout"] = ui["layout"]
                    try:
                        save_config(cfg)
                    except OSError:
                        pass
                elif keys in (b" ", b"\x1b[6~", b"b", b"\x1b[5~"):
                    if ui["tasks"] and ui["scroll"] < 0:
                        ui["scroll"] = ui["tasks_top"]   # deja de seguir la tarea en curso
                    step = page if keys in (b" ", b"\x1b[6~") else -page
                    if ui["news"] and not ui["sel"]:
                        step = -step   # el registro se cuenta desde el final: RePág va hacia atrás
                    ui["scroll"] = max(0, ui["scroll"] + step)
                elif keys == b"a":
                    focus_sid, ui["sel"], ui["focus"] = None, None, None
                elif keys == b"c" and store.multi:
                    cycle = [None] + [a.name for a in store.active]
                    account = cycle[(cycle.index(account) + 1) % len(cycle)]
                    focus_sid, ui["sel"], ui["focus"] = None, None, None
                elif keys in (b"j", b"k", b"\x1b[A", b"\x1b[B") and sessions and pane == "sesiones":
                    step = -1 if keys in (b"k", b"\x1b[A") else 1
                    i = sessions.index(focus) if focus in sessions else 0
                    focus_sid, ui["sel"], ui["focus"] = sessions[(i + step) % len(sessions)].sid, None, None
                    ui["scroll"] = 0
                    if ui["session"] is not None:
                        ui["session"]["top"] = None
    except KeyboardInterrupt:
        pass
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
