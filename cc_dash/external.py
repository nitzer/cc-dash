"""Lo que sale del dashboard: tmux, visores, la vista de cambios y el portapapeles."""

import base64
import os
import re
import shlex
import shutil
import subprocess
import sys

from rich.console import Console
from rich.text import Text

from .config import VIEWERS, limits
from .repo import change_source, changed_lines
from .text import HOME, is_doc, trunc
from .theme import C, NEWS


def notify(store, ui, sid, kind, text, now):
    """Una novedad: toast y registro siempre; las ruidosas además suenan y quedan en el log."""
    s = store.sessions.get(sid)
    who = store.label(s) if s else ""
    store.news.append({"ts": now, "sid": sid, "kind": kind, "who": who, "text": text})
    if ui["toasts_on"]:
        ui["toasts"].append({"born": now, "kind": kind, "who": who, "text": text})
        del ui["toasts"][:-limits["toast_max"]]
    if not NEWS[kind][3]:
        return
    if ui["effects"]:
        ui["glitch"] = now + 0.6
    line = f"{who} {text}".strip()
    store.event(now, sid, NEWS[kind][2], "aviso", f"{NEWS[kind][1]}: {text}")
    if not ui["alerts"]:
        return
    sys.stdout.write("\a")
    sys.stdout.flush()
    if os.environ.get("TMUX"):
        try:
            subprocess.run(["tmux", "display-message", "-d", "4000", f"cc-dash: {NEWS[kind][1]} · {line}"],
                           capture_output=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def jump(store, s):
    """Lleva el cliente de tmux al panel donde corre la sesión. Devuelve el mensaje a mostrar."""
    where = re.match(r"(.*):(@\d+)\.(%\d+)$", (store.live.get(s.sid) or {}).get("tmux") or "")
    if not where:
        return "esa sesión no corre dentro de tmux (o ya está cerrada)"
    if not os.environ.get("TMUX"):
        return "cc-dash no está dentro de tmux: no puede cambiar de panel"
    session, window, pane = where.groups()
    try:
        subprocess.run(["tmux", "switch-client", "-t", session], capture_output=True, timeout=3)
        moved = subprocess.run(["tmux", "select-window", "-t", window], capture_output=True, text=True, timeout=3)
        if moved.returncode == 0:
            moved = subprocess.run(["tmux", "select-pane", "-t", pane], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"tmux no respondió: {exc}"
    return f"→ {store.label(s)}" if moved.returncode == 0 else f"tmux: {moved.stderr.strip()}"


CODE_VIEWERS = ("bat --paging=always", "batcat --paging=always", "less -R", "more")   # para lo que no es .md


def show_changes(path):
    """`cc-dash changes ARCHIVO`: el archivo entero con colores de sintaxis y lo que cambió resaltado.

    Las líneas nuevas van sobre verde, las cambiadas sobre ámbar y las borradas aparecen en rojo
    donde estaban. Cada bloque de cambios empieza con ◆: en el visor, n y N saltan entre ellos."""
    from rich.syntax import Syntax
    against, note = change_source(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            code = fh.read()
    except OSError as exc:
        sys.exit(f"cc-dash: {exc}")
    added, modified, removed = changed_lines(path, against) if against is not None else (set(), set(), {})
    if against == []:   # archivo nuevo: todo es añadido
        added = set(range(1, code.count("\n") + 2))
    try:
        lexer = Syntax.guess_lexer(path, code)
    except Exception:
        lexer = "text"
    lines = Syntax(code, lexer, theme="monokai", background_color="default").highlight(code).split("\n")
    if lines and not lines[-1].plain and code.endswith("\n"):
        lines = lines[:-1]
    width = shutil.get_terminal_size((120, 40)).columns
    green, amber, red = "#12341f", "#3a3410", "#3d1418"
    out = []
    head = Text(no_wrap=True)
    head.append(f" {path.replace(HOME, '~')}", style="bold")
    head.append(f"  ·  {note}  ·  ", style=C.DIM)
    head.append(f"+{len(added)} ", style=f"bold {C.TOOL}")
    head.append(f"~{len(modified)} ", style=f"bold {C.WAIT}")
    head.append(f"-{sum(len(v) for v in removed.values())}", style=f"bold {C.ERR}")
    head.append("   n/N siguiente/anterior cambio · q cierra", style=C.DIM)
    out += [head, Text("")]
    num_w = len(str(len(lines))) + 1
    changed = added | modified
    starts = {n for n in changed if n - 1 not in changed} | set(removed)   # donde empieza cada bloque
    for n, text in enumerate(lines, 1):
        for k, ghost in enumerate(removed.get(n, [])):
            row = Text(no_wrap=True)
            row.append(f"{'':>{num_w}} ", style=C.DIM)
            row.append("◆" if k == 0 else "▌", style=f"bold {C.ERR}")
            row.append(" " + ghost, style=f"{C.ERR} on {red}")
            row.pad_right(max(0, width - len(row.plain)))
            row.stylize(f"on {red}", num_w + 2)
            out.append(row)
        row = Text(no_wrap=True)
        new, mod = n in added, n in modified
        row.append(f"{n:>{num_w}} ", style=C.FG if new or mod else C.LINE)
        mark = "◆" if (new or mod) and n in starts and not removed.get(n) else "▌" if new or mod else " "
        row.append(mark, style=f"bold {C.TOOL if new else C.WAIT}")
        row.append(" ")
        row.append_text(text)
        if new or mod:
            row.pad_right(max(0, width - len(row.plain)))
            row.stylize(f"on {green if new else amber}", num_w + 2)
        out.append(row)
    for n in sorted(k for k in removed if k > len(lines)):   # borrado al final del archivo
        for ghost in removed[n]:
            out.append(Text(f"{'':>{num_w}} ◆ {ghost}", style=f"{C.ERR} on {red}"))
    console = Console(force_terminal=True, color_system="truecolor", width=width, highlight=False)
    with console.capture() as cap:
        for row in out:
            console.print(row, soft_wrap=True)
    data = cap.get()
    if sys.stdout.isatty() and shutil.which("less"):
        first = "-p◆" if starts or removed else ""
        # -j6: el primer cambio queda en la sexta fila, con la cabecera y algo de contexto encima
        subprocess.run(["less", "-RS", "-j6", *([first] if first else [])], input=data.encode(), check=False)
    else:
        sys.stdout.write(data)


def self_command():
    """Cómo volver a llamar a este mismo cc-dash (el intérprete y el módulo que están corriendo)."""
    return f"{shlex.quote(sys.executable)} -m cc_dash"


def viewer_command(cfg, path):
    """Orden de shell que abre `path`: la configurada o el primer visor instalado.

    Un visor solo de markdown (glow, mdcat) no sirve para código: ahí se usa bat o less."""
    cmd = cfg["viewer"]
    if not is_doc(path) and (not cmd or cmd.split()[0] in ("glow", "mdcat", "frogmouth")):
        cmd = next((v for v in CODE_VIEWERS if shutil.which(v.split()[0])), "")
    cmd = cmd or next((v for v in VIEWERS if shutil.which(v.split()[0])), "")
    if not cmd:
        return ""
    return cmd.replace("{file}", shlex.quote(path)) if "{file}" in cmd else f"{cmd} {shlex.quote(path)}"


def tmux_out(*args):
    try:
        got = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return got.stdout.strip() if got.returncode == 0 else None


def open_doc(cfg, path, pane=None, only_visible=False, cmd=None, title=None):
    """Abre un documento con el visor configurado (u otra orden, `cmd`), sin esperar a que se cierre.

    Devuelve "" si lo abrió; "tty" si el visor necesita una terminal y no hay tmux;
    "hidden" si `only_visible` y nadie está mirando ese panel; o el motivo del fallo.
    """
    cmd = cmd or viewer_command(cfg, path)
    if not cmd:
        return 'no hay visor instalado: elige uno con `cc-dash set viewer "PROGRAMA"`'
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE,
             "start_new_session": True}
    argv = cmd
    if cfg["viewer_popup"]:
        pane = pane or os.environ.get("TMUX_PANE")
        if not (os.environ.get("TMUX") and pane):
            return "tty"
        where = (tmux_out("display-message", "-p", "-t", pane,
                          "#{session_id} #{session_attached} #{window_active}") or "").split()
        if len(where) != 3:
            return "tmux no encontró el panel de la sesión"
        if only_visible and (where[1] == "0" or where[2] != "1"):
            return "hidden"
        # el popup sale en el cliente que usó esa sesión de tmux por última vez
        clients = sorted((tmux_out("list-clients", "-t", where[0], "-F", "#{client_activity} #{client_name}")
                          or "").splitlines())
        if not clients:
            return "hidden" if only_visible else "no hay ningún cliente de tmux en esa sesión"
        argv = ["tmux", "display-popup", "-c", clients[-1].split(" ", 1)[1], "-t", pane, "-E",
                "-w", "90%", "-h", "90%", "-T", title or f" {os.path.basename(path)} · {os.path.dirname(path).replace(HOME, '~')} ",
                "-d", os.path.dirname(path) or ".", cmd]
    try:
        proc = subprocess.Popen(argv, shell=isinstance(argv, str), **quiet)
        proc.wait(0.4)   # si sigue vivo, el visor está abierto
    except subprocess.TimeoutExpired:
        return ""
    except OSError as exc:
        return f"no se pudo abrir el visor: {exc}"
    if proc.returncode == 0:
        return ""
    return "el visor falló: " + (trunc(proc.stderr.read().decode(errors="replace"), 120) or f"código {proc.returncode}")


def copy_text(text):
    """Al portapapeles: por tmux si estamos dentro (también deja el texto en su búfer), si no por OSC 52."""
    data = text.encode()
    if os.environ.get("TMUX"):
        for cmd in (["tmux", "load-buffer", "-w", "-"], ["tmux", "load-buffer", "-"]):
            try:
                if subprocess.run(cmd, input=data, capture_output=True, timeout=3).returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
    sys.stdout.write("\x1b]52;c;" + base64.b64encode(data).decode() + "\a")
    sys.stdout.flush()
