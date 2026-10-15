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
from rich.live import Live

from .config import LAYOUTS, LIMITS, TEXTS, apply_limits, config_path, load_config, save_config
from .external import copy_text, jump, notify, open_doc, self_command, viewer_command
from .faces import FACES, reached_tier
from .game import EVO, agent_game, lead_game
from .model import Store
from .panels import log_cursor, log_events, query
from .repo import change_source
from .screen import boot, new_ui, render, shown_text, watch
from .settings import apply_config, change_setting, setting_entries
from .text import is_doc
from .theme import THEMES, apply_theme


CHEATS = ("iddqd", "idkfa")   # sí, esos


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


QUIT = object()   # lo que devuelve un manejador de teclas para salir del dashboard


def run(accounts, once, cfg):
    store = Store(accounts)
    store.off = set(cfg["disabled"])
    store.poll(time.time())
    store.ready = True
    dash = Dashboard(store, cfg)
    if once or not sys.stdout.isatty():   # un solo fotograma, sin avisos
        dash.console.print(render(dash.console, store, None, None, time.time(), dash.ui)[0])
        return
    dash.loop()


def cursor_target(keys, nav, at, page, end):
    """A dónde lleva un cursor la tecla: j/k una línea, RePág/AvPág (o b/espacio) una página,
    gg/G al principio y al final (`end`). None si la tecla no lo mueve."""
    down, up, first, last = nav
    if down or up:
        return at + (1 if down else -1)
    if keys in (b"b", b"\x1b[5~"):
        return at - page
    if keys in (b" ", b"\x1b[6~"):
        return at + page
    if first or last:
        return 0 if first else end
    return None


def scroll_text(view, key, nav):
    """j/k/gg/G desplazan un texto (view[key] es la primera línea visible). True si la tecla era de esas."""
    down, up, first, last = nav
    if down or up:
        view[key] = max(0, view[key] + (1 if down else -1))
    elif first or last:
        view[key] = 0 if first else 10 ** 6
    else:
        return False
    return True


class Dashboard:
    """El dashboard en marcha: refresca la pantalla y reparte cada tecla al manejador que le toca."""

    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.console = Console()
        self.ui = ui = new_ui()
        ui["layout"], ui["theme"], ui["alerts"] = cfg["layout"], cfg["theme"], cfg["alerts"]
        ui["effects"], ui["toasts_on"], ui["cfg"] = cfg["effects"], cfg["toasts"], cfg
        apply_theme(ui["theme"])
        apply_limits(cfg)
        self.cfg_seen = [now_mtime(), 0.0]   # [mtime de la configuración, última vez que se miró]
        self.focus_sid = None    # sesión elegida a mano (None: la elige el modo automático)
        self.account = None      # filtro de cuenta (tecla c)
        self.seen = {}           # sid -> (estado, desde cuándo trabaja), para los avisos
        self.now = time.time()
        self.sessions, self.focus = [], None
        self.fd = self.saved = self.live = None
        # manejadores por orden de prioridad: las ventanas abiertas se quedan con las teclas
        self.handlers = (self.on_typing, self.on_help, self.on_gallery, self.on_entry, self.on_settings,
                         self.on_cheat, self.on_global, self.on_gg, self.on_panes, self.on_log, self.on_session,
                         self.on_agents_pane, self.on_sessions_pane, self.on_detail_list, self.on_detail_text,
                         self.on_command)
        self.global_keys = {
            b",": self.open_settings, b"/": self.start_search, b"y": self.copy_any, b"D": self.open_last_doc,
            b"x": self.clear_toasts, b"X": self.toggle_toasts, b"o": self.toggle_tasks, b"n": self.toggle_news,
            b"E": self.toggle_effects,
        }
        self.commands = {
            b"l": self.cmd_next_agent, b"\x1b[C": self.cmd_next_agent,
            b"h": self.cmd_prev_agent, b"\x1b[D": self.cmd_prev_agent,
            b"F": self.cmd_focus, b"\x1b": self.cmd_release,
            b"p": self.cmd_prompt, b"r": self.cmd_response, b"i": self.cmd_info,
            b"t": self.cmd_toggle_all, b"f": self.cmd_toggle_only,
            b"\r": self.cmd_jump, b"\n": self.cmd_jump,
            b"T": self.cmd_theme, b"m": self.cmd_alerts, b"d": self.cmd_layout,
            b" ": self.cmd_page_down, b"\x1b[6~": self.cmd_page_down,
            b"b": self.cmd_page_up, b"\x1b[5~": self.cmd_page_up,
            b"a": self.cmd_auto, b"c": self.cmd_account,
            b"j": self.cmd_next_session, b"\x1b[B": self.cmd_next_session,
            b"k": self.cmd_prev_session, b"\x1b[A": self.cmd_prev_session,
        }

    # ── ciclo ──
    def tick(self):
        """Lee lo nuevo, compone la pantalla y lanza los avisos. Devuelve lo que hay que dibujar."""
        store, ui = self.store, self.ui
        self.now = now = time.time()
        store.poll(now)
        root, self.sessions, self.focus = render(self.console, store, self.focus_sid, self.account, now, ui)
        for sid, kind, text in watch(store, self.sessions, self.seen, now):
            notify(store, ui, sid, kind, text, now)
        if self.focus is not None:
            store.git_info(self.focus, now, refresh=True)
            self.announce_evolutions()
        if reload_limits(self.cfg, ui, self.cfg_seen, now) and store.off != set(self.cfg["disabled"]):
            store.off = set(self.cfg["disabled"])
            store.t_glob = store.t_live = 0.0
        return root

    def announce_evolutions(self):
        """Cada 10 niveles el rostro evoluciona: se anuncia una vez por agente."""
        focus, ui = self.focus, self.ui
        agents = [(focus.sid, "el lead", lead_game(focus))] + [
            (v["id"], v["type"], agent_game(v)) for v in self.store.agents(focus)]
        for key, name, g in agents:
            before = ui["tiers"].get(key)
            ui["tiers"][key] = g["tier"]
            if before is not None and g["tier"] > before:
                notify(self.store, ui, focus.sid, "evo", f"{name} llegó al nivel {g['lvl']}: {EVO[g['tier']]}",
                       self.now)
                ui["glitch"] = self.now + 0.8

    def loop(self):
        self.fd = sys.stdin.fileno() if termios is not None and sys.stdin.isatty() else None
        self.saved = termios.tcgetattr(self.fd) if self.fd is not None else None
        queue = deque()  # teclas leídas y aún sin procesar
        try:
            if self.fd is not None:
                tty.setcbreak(self.fd)
            if self.ui["effects"]:
                boot(self.console, self.store)
            with Live(console=self.console, screen=True, auto_refresh=False) as self.live:
                while True:
                    root = self.tick()
                    if not queue:
                        self.live.update(root, refresh=True)
                        queue.extend(split_keys(read_keys(self.fd, 0.12)))
                    if queue and self.handle(queue.popleft()) is QUIT:
                        break
        except KeyboardInterrupt:
            pass
        finally:
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def handle(self, keys):
        """Pasa la tecla por los manejadores hasta que uno la atiende."""
        if keys == b"\x03":
            return QUIT
        for handler in self.handlers:
            done = handler(keys)
            if done is not None:
                return done
        return None

    # ── utilidades ──
    def save(self):
        try:
            save_config(self.cfg)
        except OSError:
            pass

    def flash(self, text, seconds=3):
        self.ui["flash"] = (text, self.now + seconds)

    def visible_ids(self):
        return [v["id"] for v in self.store.visible(self.focus, self.now, self.ui["all"], self.ui["sel"])]

    def chosen(self):
        """El subagente seleccionado, si sigue a la vista."""
        ui = self.ui
        return next((v for v in self.store.visible(self.focus, self.now, ui["all"], ui["sel"])
                     if v["id"] == ui["sel"]), None)

    def copy_shown(self, empty):
        text = shown_text(self.store, self.focus, self.ui, self.now)
        if text:
            copy_text(text)
        self.flash(f"copiado ({len(text)} caracteres)" if text else empty)

    def view_doc(self, doc, cmd=None, title=None, note=None):
        """Abre un archivo con el visor (u otra orden) en un popup de tmux, o en la terminal entera sin tmux."""
        why = open_doc(self.cfg, doc, cmd=cmd, title=title)
        if why == "tty":   # sin tmux: el visor toma la terminal hasta que se cierre
            self.live.stop()
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            subprocess.run(cmd or viewer_command(self.cfg, doc), shell=True)
            if self.fd is not None:
                tty.setcbreak(self.fd)
            self.live.start(refresh=True)
            why = ""
        self.ui["flash"] = (why or note or f"abierto {os.path.basename(doc)}", time.time() + 4)

    def open_entry(self, e):
        """Enter sobre una entrada del log: un .md se abre con el visor; lo demás, en una ventana flotante."""
        ref = e[6]
        path = (ref.get("input") or {}).get("file_path") if ref and ref["k"] == "tool" else None
        if is_doc(path):
            s = self.store.sessions.get(e[1])
            full = os.path.join(s.cwd if s else "", path)
            if os.path.isfile(full):
                self.view_doc(full)
                return
        self.ui["entry"] = {"e": e, "scroll": 0, "rows": 1, "pane": "out"}

    def nav(self, keys):
        """(abajo, arriba, primero, último) para las teclas de movimiento comunes a todos los paneles."""
        return (keys in (b"j", b"\x1b[B"), keys in (b"k", b"\x1b[A"),
                keys in (b"g", b"\x1b[H", b"\x1b[1~"), keys in (b"G", b"\x1b[F", b"\x1b[4~"))

    # ── ventanas y modos que se quedan con las teclas ──
    def on_typing(self, keys):
        """Escribiendo una búsqueda (/): todas las teclas son texto."""
        ui = self.ui
        if ui["typing"] is None:
            return None
        if keys in (b"\r", b"\n"):
            ui["find"], ui["typing"] = query(ui), None
        elif keys == b"\x1b":
            ui["find"], ui["typing"] = "", None
        elif keys in (b"\x7f", b"\x08"):
            ui["typing"] = query(ui)[:-1].encode()
        elif len(keys) == 1 and keys[0] >= 0x20:
            ui["typing"] += keys
        if ui["log"] is not None:
            ui["log"]["cur"], ui["log"]["scroll"] = None, 0
        return True

    def on_help(self, keys):
        """Ventana de teclas: j/k la desplazan, cualquier otra tecla la cierra; ? la abre."""
        ui = self.ui
        if ui["help"] is None:
            if keys == b"?" and ui["settings"] is None:
                ui["help"] = {"top": 0, "rows": 1}
                return True
            return None
        hv = ui["help"]
        if keys in (b"j", b"\x1b[B", b"\x1b[6~"):
            hv["top"] += 1 if keys != b"\x1b[6~" else hv["rows"]
        elif keys in (b"k", b"\x1b[A", b"\x1b[5~"):
            hv["top"] = max(0, hv["top"] - (1 if keys != b"\x1b[5~" else hv["rows"]))
        else:
            ui["help"] = None
        return True

    def on_gallery(self, keys):
        """Evoluciones (L): h/l cambian de personaje, j/k de evolución."""
        ui, store, focus = self.ui, self.store, self.focus
        gv = ui["gallery"]
        if gv is None:
            if keys == b"L" and ui["settings"] is None:
                sel_v = next((v for v in store.agents(focus) if v["id"] == ui["sel"]), None) if focus else None
                start = sum(map(ord, sel_v["type"])) % len(FACES) if sel_v else 0
                ui["gallery"] = {"face": start, "tier": agent_game(sel_v)["tier"] if sel_v else 0}
                return True
            return None
        if keys in (b"\x1b", b"q", b"L", b"\r", b"\n"):
            ui["gallery"] = None
        elif keys in (b"l", b"\x1b[C", b"h", b"\x1b[D"):
            gv["face"] = (gv["face"] + (1 if keys in (b"l", b"\x1b[C") else -1)) % len(FACES)
            gv["tier"] = 4   # abre en la evolución más alta descubierta
        elif keys in (b"k", b"\x1b[A"):
            gv["tier"] = min(reached_tier(store, focus, gv["face"] % len(FACES)), gv["tier"] + 1)
        elif keys in (b"j", b"\x1b[B"):
            gv["tier"] = max(0, gv["tier"] - 1)
        return True

    def on_entry(self, keys):
        """Ventana flotante con el detalle de una entrada del log."""
        ui = self.ui
        ev = ui["entry"]
        if ev is None:
            return None
        page = max(1, ev["rows"] - 2)
        if keys == b"g" and self.now - ui["g_at"] > 0.6:
            ui["g_at"] = self.now
            return True
        if keys in (b"\x1b", b"q", b"\r", b"\n", b"h"):
            ui["entry"] = None
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
            self.copy_shown("nada que copiar")
        return True

    def on_settings(self, keys):
        """Ventana de configuración: captura todas las teclas."""
        ui, store = self.ui, self.store
        if ui["settings"] is None:
            return None
        rows = [e for e in setting_entries(store) if isinstance(e, tuple)]
        at = ui["settings"]["at"] % len(rows)
        if keys in (b",", b"\x1b", b"q"):
            ui["settings"] = None
        elif keys in (b"j", b"\x1b[B", b"\t"):
            ui["settings"]["at"] = (at + 1) % len(rows)
        elif keys in (b"k", b"\x1b[A"):
            ui["settings"]["at"] = (at - 1) % len(rows)
        elif keys in (b"l", b"\x1b[C", b"\r", b"\n", b" ", b"h", b"\x1b[D", b"r"):
            change_setting(self.cfg, rows[at], 0 if keys == b"r" else -1 if keys in (b"h", b"\x1b[D") else 1)
            apply_config(self.cfg, ui, store)
            self.cfg_seen[0] = now_mtime()
            if self.account in store.off:   # el filtro apuntaba a una cuenta que se acaba de apagar
                self.account = None
            if self.focus_sid and getattr(store.sessions.get(self.focus_sid), "account", None) in store.off:
                self.focus_sid = None
        return True

    def on_cheat(self, keys):
        """Trucos: las letras también son atajos, así que al completarse uno se deshace lo que
        hicieron por el camino."""
        ui, now = self.ui, self.now
        if not (len(keys) == 1 and keys.isalpha()):
            return None
        if now - ui["cheat_at"] > 2:
            ui["cheat"] = ""
        ui["cheat_at"] = now
        typed = ui["cheat"] + keys.decode()
        ui["cheat"] = typed if any(c.startswith(typed) for c in CHEATS) else "i" if keys == b"i" else ""
        if ui["cheat"] == "i":
            ui["cheat_undo"] = (ui["layout"], ui["info"], ui["only"], self.focus_sid, ui["sel"])
        if ui["cheat"] in CHEATS:
            ui["layout"], ui["info"], ui["only"], self.focus_sid, ui["sel"] = ui["cheat_undo"]
            self.cfg["layout"] = ui["layout"]
            self.save()
            if ui["cheat"] == "iddqd":
                ui["god"] = not ui["god"]
                text = ("invulnerabilidad activada · los subagentes aún pueden fallar" if ui["god"]
                        else "god mode desactivado · vuelves a ser mortal")
            else:
                ui["rain_until"] = now + 20
                text = "todas las llaves, armas y munición · tormenta de 20 segundos"
            ui["toasts"].append({"born": now, "kind": "cheat", "who": ui["cheat"].upper(), "text": text})
            ui["glitch"], ui["cheat"] = now + 1.2, ""
            return True
        if ui["cheat"] == "iddq":   # esta q es del truco, no un "salir"
            return True
        return None

    def on_global(self, keys):
        """Teclas que valen en cualquier panel."""
        if keys == b"q":
            return QUIT
        action = self.global_keys.get(keys)
        return action() if action else None

    def on_gg(self, keys):
        """gg salta al primero: la primera g espera a la segunda; la segunda sigue como "primero"."""
        ui = self.ui
        if keys != b"g":
            return None
        if self.now - ui["g_at"] > 0.6:
            ui["g_at"] = self.now
            return True
        ui["g_at"] = 0.0
        return None

    def open_settings(self):
        self.ui["settings"] = {"at": 0}
        return True

    def start_search(self):
        self.ui["typing"] = self.ui["find"].encode()
        return True

    def copy_any(self):
        self.copy_shown("nada que copiar: abre una entrada del log (s) o un subagente")
        return True

    def open_last_doc(self):
        focus = self.focus
        doc = next((p for p in reversed(focus.docs) if os.path.isfile(p)), None) if focus else None
        if doc:
            self.view_doc(doc)
        else:
            self.flash("esta sesión no ha escrito ningún .md", 4)
        return True

    def clear_toasts(self):
        self.ui["toasts"].clear()
        return True

    def toggle_toasts(self):
        ui = self.ui
        ui["toasts_on"] = self.cfg["toasts"] = not ui["toasts_on"]
        ui["toasts"].clear()
        self.flash("toasts encendidos" if ui["toasts_on"]
                   else "toasts apagados · las novedades siguen en el registro (n)")
        self.save()
        return True

    def toggle_tasks(self):
        return self.toggle_side("tasks", "news")

    def toggle_news(self):
        return self.toggle_side("news", "tasks")

    def toggle_side(self, which, other):
        """o/n: tareas o novedades en el panel de la derecha."""
        ui = self.ui
        ui[which], ui[other] = not ui[which], False
        ui["scroll"], ui["tasks_of"] = 0, None
        if ui[which]:
            ui["sel"] = ui["log"] = None   # el panel de la derecha pasa a ser este
        return True

    def toggle_effects(self):
        ui = self.ui
        ui["effects"] = self.cfg["effects"] = not ui["effects"]
        self.flash("efectos activados" if ui["effects"] else "efectos apagados", 2)
        self.save()
        return True

    def on_panes(self, keys):
        """Tab rota el foco: sesiones → agentes → session log → panel derecho."""
        ui, focus = self.ui, self.focus
        if keys not in (b"\t", b"\x1b[Z") or ui["session"] is not None:
            return None
        panes = ui["panes"]
        cur = ui["pane"] if ui["pane"] in panes else panes[0]
        nxt = panes[(panes.index(cur) + (1 if keys == b"\t" else -1)) % len(panes)]
        if nxt == "log":
            ui["log"] = ui["log"] or {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}
            ui["log"]["pane"] = "list"
        else:
            ui["log"] = None
        if nxt == "agentes" and focus is not None:
            vis = self.visible_ids()
            if ui["sel"] is None and vis:
                ui["sel"], self.focus_sid, ui["scroll"] = vis[0], focus.sid, 0
        if nxt == "detalle":
            ui["dcur"] = 0
        ui["pane"] = nxt
        return True

    def on_log(self, keys):
        """Cursor en el log: la lista (j/k elige entrada) y su detalle (j/k desplaza); s lo abre."""
        ui = self.ui
        if ui["log"] is None:
            if ui["pane"] == "log" and any(self.nav(keys)):
                ui["log"] = {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}   # y sigue con la tecla
            elif keys in (b"s", b"S"):
                ui["log"] = {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}
                ui["session"], ui["pane"] = None, "log"
                return True
            else:
                return None
        lg = ui["log"]
        if keys in (b"s", b"S", b"\x1b"):
            ui["log"] = None
            return True
        events = log_events(self.store, self.focus, self.account, self.chosen(), ui["only"], query(ui))[0]
        at = log_cursor(events, lg)
        if keys in (b"l", b"\x1b[C", b"\r", b"\n", b" "):
            if events:
                self.open_entry(events[at])
            return True
        if lg["pane"] == "out" and scroll_text(lg, "scroll", self.nav(keys)):
            return True
        move = cursor_target(keys, self.nav(keys), at, max(1, lg["rows"] - 2), len(events))
        if move is None:
            return None
        if events:
            move = max(0, move)
            # en la última entrada el cursor queda libre y sigue a las nuevas
            lg["cur"] = None if move >= len(events) - 1 else events[move]
            lg["scroll"] = 0
        return True

    def on_session(self, keys):
        """Conversación (v): la lista (j/k elige sesión) y la conversación (j/k desplaza), al estilo vim."""
        ui = self.ui
        view = ui["session"]
        if view is None:
            if keys in (b"v", b"V"):
                if self.focus:
                    ui["session"] = {"top": None, "rows": 1, "last": 0, "pane": "list"}
                    ui["log"] = None
                return True
            return None
        down, up, first, last = self.nav(keys)
        page = max(1, view["rows"] - 2)
        here = view["last"] if view["top"] is None else view["top"]
        inside = view["pane"] == "out"
        if keys in (b"v", b"V", b"\x1b"):
            ui["session"] = None
        elif keys in (b"h", b"\x1b[D"):
            view["pane"] = "list"
        elif keys in (b"l", b"\x1b[C", b"\r", b"\n"):
            view["pane"] = "out"
        elif inside and (up or down):
            view["top"] = max(0, here + (1 if down else -1))
        elif keys in (b"b", b"\x1b[5~"):
            view["top"] = max(0, here - page)
        elif keys in (b" ", b"\x1b[6~"):
            view["top"] = here + page
        elif first:
            view["top"] = 0
        elif last:
            view["top"] = None
        else:
            return None
        return True

    def on_agents_pane(self, keys):
        """Panel de agentes: j/k recorren los subagentes; Enter entra en modo foco."""
        if self.ui["pane"] != "agentes":
            return None
        down, up, first, last = self.nav(keys)
        if down or up or first or last:
            self.move_agent(down, first, last)
            return True
        if keys in (b"\r", b"\n"):
            return self.cmd_focus()
        return None

    def on_sessions_pane(self, keys):
        """Panel de sesiones: gg y G van a la primera y a la última (j/k están en los atajos)."""
        ui = self.ui
        _, _, first, last = self.nav(keys)
        if ui["pane"] != "sesiones" or not (first or last) or not self.sessions:
            return None
        self.focus_sid = (self.sessions[0] if first else self.sessions[-1]).sid
        ui["sel"] = ui["focus"] = None
        ui["scroll"] = 0
        return True

    def on_detail_list(self, keys):
        """Actividad en el panel derecho: un cursor por líneas; Enter despliega la lista; e, los cambios."""
        ui = self.ui
        if ui["pane"] != "detalle" or ui["dkind"] != "list":
            return None
        if keys == b"e":
            self.show_file_changes()
            return True
        if keys in (b"\r", b"\n", b"l"):
            title = ui["dmarks"].get(ui["dcur"])
            if title:
                ui["open"] ^= {title}
            return True
        n = max(1, ui["dlines"])
        move = cursor_target(keys, self.nav(keys), ui["dcur"], max(1, ui["rows"] - 1), n - 1)
        if move is None:
            return None
        ui["dcur"] = max(0, min(n - 1, move))
        return True

    def on_detail_text(self, keys):
        """Texto en el panel derecho (respuesta, prompt, tareas, novedades): j/k desplazan línea a línea."""
        ui = self.ui
        down, up, first, last = self.nav(keys)
        if ui["pane"] != "detalle" or not (down or up or first or last):
            return None
        if ui["tasks"] and ui["scroll"] < 0:
            ui["scroll"] = ui["tasks_top"]
        backwards = ui["news"] and not ui["sel"]   # el registro se cuenta desde el final
        if first or last:
            ui["scroll"] = 10 ** 6 if first == backwards else 0
        else:
            ui["scroll"] = max(0, ui["scroll"] + (1 if down != backwards else -1))
        return True

    def move_agent(self, down, first, last):
        """j/k recorren los subagentes; en modo foco, el foco los acompaña."""
        ui, ids = self.ui, self.visible_ids()
        if not ids:
            return
        i = ids.index(ui["sel"]) if ui["sel"] in ids else (-1 if down else len(ids))
        i = 0 if first else len(ids) - 1 if last else max(0, min(len(ids) - 1, i + (1 if down else -1)))
        ui["sel"], self.focus_sid, ui["scroll"] = ids[i], self.focus.sid, 0
        if ui["focus"]:
            ui["focus"] = ui["sel"]

    def show_file_changes(self):
        """e: lo que cambió en el archivo bajo el cursor de la actividad, según git."""
        ui, focus = self.ui, self.focus
        title = ui["dmarks"].get(ui["dcur"]) or ""
        item = ui["ditems"].get(ui["dcur"])
        if not (item and title.startswith("archivos")):
            self.flash("e muestra los cambios: pon el cursor sobre un archivo")
            return
        path = os.path.join(focus.cwd if focus else "", item)
        name = os.path.basename(path)
        against, note = change_source(path)
        if against is None and not os.path.isfile(path):
            self.flash(f"{name}: {note}")
            return
        if against is None:
            note += ": solo el archivo, sin cambios marcados"
        self.view_doc(path, cmd=f"{self_command()} changes {shlex.quote(path)}", note=f"{name}: {note}",
                      title=f" cambios · {name} · {note} ")

    def on_command(self, keys):
        """Atajos sueltos (tecla -> acción)."""
        action = self.commands.get(keys)
        return action() if action else None

    # ── atajos ──
    def cmd_next_agent(self):
        return self.step_agent(1)

    def cmd_prev_agent(self):
        return self.step_agent(-1)

    def step_agent(self, step):
        """h/l eligen subagente; en foco se pasa de un agente a otro."""
        ui = self.ui
        order = ([] if ui["focus"] else [None]) + self.visible_ids()
        i = order.index(ui["sel"]) if ui["sel"] in order else 0
        if order:
            ui["sel"] = order[(i + step) % len(order)]
        ui["scroll"] = 0
        if ui["focus"]:
            ui["focus"] = ui["sel"]
        if ui["sel"]:
            self.focus_sid = self.focus.sid  # que el modo auto no cambie de sesión mientras se lee
        return True

    def cmd_focus(self):
        ui, ids = self.ui, self.visible_ids()
        if ui["focus"]:
            ui["focus"] = None
        elif ids:
            ui["sel"] = ui["sel"] if ui["sel"] in ids else ids[0]
            ui["focus"], self.focus_sid, ui["tab"], ui["scroll"] = ui["sel"], self.focus.sid, "act", 0
            ui["tasks"] = ui["news"] = False
        else:
            self.flash("no hay subagentes a la vista para enfocar (t muestra los terminados)")
        return True

    def cmd_release(self):
        self.ui["sel"] = self.ui["focus"] = None
        return True

    def cmd_prompt(self):
        return self.show_tab("prompt")

    def cmd_response(self):
        return self.show_tab("resp")

    def show_tab(self, tab):
        ui, ids = self.ui, self.visible_ids()
        if ui["sel"] is None and ids:
            ui["sel"], self.focus_sid = ids[0], self.focus.sid
        ui["tab"], ui["scroll"] = tab, 0
        return True

    def cmd_info(self):
        ui = self.ui
        if ui["focus"]:
            ui["tab"], ui["scroll"] = "act", 0
        else:
            ui["info"] = not ui["info"]
        return True

    def cmd_toggle_all(self):
        self.ui["all"] = not self.ui["all"]
        return True

    def cmd_toggle_only(self):
        self.ui["only"] = not self.ui["only"]
        return True

    def cmd_jump(self):
        if self.focus:
            self.flash(jump(self.store, self.focus), 4)
        return True

    def cmd_theme(self):
        ui, names = self.ui, list(THEMES)
        ui["theme"] = self.cfg["theme"] = names[(names.index(ui["theme"]) + 1) % len(names)]
        apply_theme(ui["theme"])
        self.flash(f"tema: {ui['theme']}", 2)
        self.save()
        return True

    def cmd_alerts(self):
        ui = self.ui
        ui["alerts"] = self.cfg["alerts"] = not ui["alerts"]
        self.flash("avisos activados" if ui["alerts"] else "avisos silenciados", 2)
        self.save()
        return True

    def cmd_layout(self):
        ui = self.ui
        ui["layout"] = self.cfg["layout"] = LAYOUTS[(LAYOUTS.index(ui["layout"]) + 1) % len(LAYOUTS)]
        self.save()
        return True

    def cmd_page_down(self):
        return self.page(1)

    def cmd_page_up(self):
        return self.page(-1)

    def page(self, direction):
        ui = self.ui
        if ui["tasks"] and ui["scroll"] < 0:
            ui["scroll"] = ui["tasks_top"]   # deja de seguir la tarea en curso
        step = max(1, ui["rows"] - 1) * direction
        if ui["news"] and not ui["sel"]:
            step = -step   # el registro se cuenta desde el final: RePág va hacia atrás
        ui["scroll"] = max(0, ui["scroll"] + step)
        return True

    def cmd_auto(self):
        self.focus_sid, self.ui["sel"], self.ui["focus"] = None, None, None
        return True

    def cmd_account(self):
        if not self.store.multi:
            return None
        cycle = [None] + [a.name for a in self.store.active]
        self.account = cycle[(cycle.index(self.account) + 1) % len(cycle)]
        self.focus_sid, self.ui["sel"], self.ui["focus"] = None, None, None
        return True

    def cmd_next_session(self):
        return self.step_session(1)

    def cmd_prev_session(self):
        return self.step_session(-1)

    def step_session(self, step):
        ui, sessions = self.ui, self.sessions
        if not (sessions and ui["pane"] == "sesiones"):
            return None
        i = sessions.index(self.focus) if self.focus in sessions else 0
        self.focus_sid, ui["sel"], ui["focus"] = sessions[(i + step) % len(sessions)].sid, None, None
        ui["scroll"] = 0
        if ui["session"] is not None:
            ui["session"]["top"] = None
        return True
