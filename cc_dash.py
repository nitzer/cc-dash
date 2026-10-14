#!/usr/bin/env python3
"""cc-dash — dashboard en vivo de las sesiones de Claude Code.

Por cada cuenta (un directorio de configuración de Claude Code) lee
sessions/*.json (sesiones abiertas) y projects/**/*.jsonl (modelo,
herramientas, subagentes, tokens). Solo lectura: no toca ni envía nada.

Dentro del dashboard, ? muestra todas las teclas.
Uso:    cc-dash [--once] [-a CUENTA]
        cc-dash accounts | add NOMBRE RUTA | remove NOMBRE | scan DIR | config
        cc-dash update
        cc-dash disable CUENTA | enable CUENTA
        cc-dash set CLAVE VALOR     (umbrales y opciones; `cc-dash set` las lista)
"""
import argparse
import base64
import glob
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
from collections import Counter, deque
from datetime import datetime

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

try:
    import select
    import termios
    import tty
except ImportError:  # Windows
    termios = None

__version__ = "0.40.0"

HOME = os.path.expanduser("~")
DEFAULT_DIR = os.path.join(HOME, ".claude")

LEAD = "#e8915a"
SWARM = "#6fb1ff"
TOOL = "#6fcf97"
ADVISOR = "#b48efc"
ERR = "#ef6b73"
DIM = "#5c6680"
LINE = "#3b4560"
WAIT = "#f2c94c"      # una sesión detenida esperando al usuario
CARD_BG = "#1c2740"   # fondo de la tarjeta de detalles de la sesión
FG = "#c9d1e4"

DONE = "#4f6f9f"      # barra de un subagente ya terminado

# Cada tema da un color a cada papel; dentro de un tema no se repiten.
THEMES = {
    "clasico": dict(LEAD="#e8915a", SWARM="#6fb1ff", TOOL="#6fcf97", ADVISOR="#b48efc", ERR="#ef6b73",
                    WAIT="#f2c94c", DIM="#5c6680", LINE="#3b4560", FG="#c9d1e4", CARD_BG="#1c2740",
                    DONE="#4f6f9f"),
    "neon": dict(LEAD="#ff2bd6", SWARM="#00e5ff", TOOL="#b6ff00", ADVISOR="#9d4dff", ERR="#ff3355",
                 WAIT="#fff200", DIM="#7a6fa8", LINE="#3d2a66", FG="#e8e6ff", CARD_BG="#1b0f33",
                 DONE="#1b8fa3"),
    "matrix": dict(LEAD="#39ff14", SWARM="#00ff9c", TOOL="#b6ff00", ADVISOR="#7dffb0", ERR="#ff4d4d",
                   WAIT="#eaff00", DIM="#2f8f3a", LINE="#17581f", FG="#b8ffc0", CARD_BG="#06220b",
                   DONE="#1f9a45"),
    "ambar": dict(LEAD="#ffb000", SWARM="#ffd166", TOOL="#ffe9a8", ADVISOR="#ff8c42", ERR="#ff4d2e",
                  WAIT="#ffffff", DIM="#9a7526", LINE="#5c4412", FG="#ffdca6", CARD_BG="#261c06",
                  DONE="#b3832a"),
}
# Marco tipo HUD para los temas no clásicos: solo esquinas, sin laterales.
HUD_BOX = box.Box("⌜─ ⌝\n    \n    \n    \n    \n    \n    \n⌞─ ⌟\n")
HUD_HEAVY = box.Box("▛▀▀▜\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▙▄▄▟\n")
THEME = dict(THEMES["clasico"])   # papel -> color del tema activo
HUD = False
PANEL_BOX, CARD_BOX = box.SQUARE, box.HEAVY


def apply_theme(name):
    """Cambia los colores y el marco de todo el dashboard."""
    global HUD, PANEL_BOX, CARD_BOX
    colors = THEMES.get(name, THEMES["clasico"])
    globals().update(colors)
    THEME.clear()
    THEME.update(colors)
    HUD = name in THEMES and name != "clasico"
    PANEL_BOX, CARD_BOX = (HUD_BOX, HUD_HEAVY) if HUD else (box.SQUARE, box.HEAVY)


def label(text, tag="SYS"):
    """Título de panel: en los temas HUD, en mayúsculas y con prefijo de sistema."""
    return f"{tag}//{text.upper()}" if HUD else text


AGENT_TOOLS = ("Agent", "Task")
ASK_TOOLS = {"AskUserQuestion": "responder una pregunta", "ExitPlanMode": "aprobar el plan"}
# estado -> (icono, texto, papel de color)
STATES = {"waiting": ("◆", "espera tu respuesta", "WAIT"), "busy": ("●", "trabajando", "LEAD"),
          "idle": ("○", "esperando prompt", "TOOL"), "ended": ("·", "cerrada", "DIM")}


def state_color(state):
    return THEME[STATES[state][2]]
# un color por subagente, el mismo en su tarjeta, su fila y el gráfico de actividad
AGENT_COLORS = ("#ff2a6d", "#05d9e8", "#39ff14", "#ffd319", "#b967ff", "#01ffc3", "#ff71ce", "#7aa2ff")
MIN_SLOTS = 3      # casillas de subagentes que se dibujan como mínimo
# Umbrales del detector de atascos (solo se evalúa mientras la sesión trabaja)
STUCK_ERRORS = 3     # resultados con error seguidos
STUCK_REPEAT = 4     # veces seguidas la misma llamada, con los mismos parámetros
STUCK_TOOL = 300     # segundos que una herramienta puede tardar en volver
STUCK_SILENT = 180   # segundos trabajando sin escribir nada en el transcript
TOAST_LIFE = 8       # segundos que un toast sigue en pantalla
TOAST_MAX = 4        # toasts visibles a la vez
FRESH = 4            # segundos que dura el efecto de una tarea recién terminada
# tipo de novedad -> (icono, título, papel de color, ruidosa: pitido + mensaje de tmux + log)
NEWS = {"wait": ("◆", "te espera", "WAIT", True), "stuck": ("⚠", "posible atasco", "ERR", True),
        "fail": ("✗", "falló una tarea", "ERR", True), "job": ("✓", "comando terminado", "SWARM", False), "done": ("■", "turno terminado", "TOOL", True),
        "cheat": ("★", "truco activado", "WAIT", False),
        "task": ("✓", "tarea terminada", "TOOL", False), "agent": ("✓", "subagente terminado", "SWARM", False),
        "evo": ("★", "evolucionó", "WAIT", False)}
CHEATS = ("iddqd", "idkfa")   # sí, esos
LINGER = 60        # segundos que un subagente terminado sigue a la vista
SLOT_WIDTH = 24    # ancho mínimo de una casilla; caben tantas como permita la terminal
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# ── helpers ──────────────────────────────────────────────────────────────

def parse_ts(s):
    if not isinstance(s, str):
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def trunc(s, n):
    s = " ".join(str(s).split())
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def age(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def clock(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def ktok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def short_model(m):
    return (m or "?").replace("claude-", "")


def short_tool(name):
    """mcp__servidor__herramienta -> herramienta"""
    return name.split("__")[-1] if name.startswith("mcp__") else name


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "\n… (recortado)"


def clip_input(inp):
    """Parámetros de una herramienta como texto, acotados para no guardar archivos enteros."""
    if not isinstance(inp, dict):
        return {}
    return {k: clip(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False), 3000) for k, v in inp.items()}


def block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def tool_label(name, inp):
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return inp.get("description") or inp.get("command") or ""
    if name == "TaskCreate":
        return inp.get("subject") or ""
    if name == "TaskUpdate":
        return f"#{inp.get('taskId', '?')}" + (f" → {inp['status']}" if inp.get("status") else "")
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return os.path.basename(inp.get("file_path") or inp.get("notebook_path") or "")
    for key in ("description", "pattern", "query", "url", "skill", "prompt"):
        if inp.get(key):
            return inp[key]
    return ""


# ── cuentas y configuración ──────────────────────────────────────────────

class Account:
    """Una cuenta = un directorio de configuración de Claude Code."""

    def __init__(self, name, path):
        self.name = name
        self.path = os.path.abspath(os.path.expanduser(path))
        self.projects = os.path.join(self.path, "projects")
        self.live_dir = os.path.join(self.path, "sessions")

    def email(self):
        candidates = [os.path.join(self.path, ".claude.json")]
        if self.path == DEFAULT_DIR:
            candidates.append(DEFAULT_DIR + ".json")
        for path in candidates:
            try:
                with open(path) as fh:
                    return (json.load(fh).get("oauthAccount") or {}).get("emailAddress") or ""
            except (OSError, ValueError, AttributeError):
                continue
        return ""


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
    return os.path.join(base, "cc-dash", "config.toml")


# clave de configuración -> (constante que ajusta, valor por defecto, qué es)
LIMITS = {
    "stuck_errors": ("STUCK_ERRORS", 3, "errores seguidos para sospechar un atasco (0 = no mirar)"),
    "stuck_repeat": ("STUCK_REPEAT", 4, "veces seguidas la misma llamada (0 = no mirar)"),
    "stuck_tool": ("STUCK_TOOL", 300, "segundos que una herramienta puede tardar en volver (0 = no mirar)"),
    "stuck_silent": ("STUCK_SILENT", 180, "segundos trabajando sin actividad (0 = no mirar)"),
    "hide_after": ("LINGER", 60, "segundos que un subagente terminado sigue a la vista"),
    "toast_seconds": ("TOAST_LIFE", 8, "segundos que dura un toast"),
    "toast_max": ("TOAST_MAX", 4, "toasts visibles a la vez"),
}
SWITCHES = {"toasts": "mostrar toasts (tecla X)", "alerts": "pitido y aviso de tmux (tecla m)",
            "effects": "arranque, glitch y lluvia (tecla E)", "auto_update": "actualizarse al arrancar",
            "viewer_popup": "abrir los documentos en un popup de tmux (false: lanzar el visor tal cual)"}
# visores que se prueban, en orden, cuando `viewer` está vacío
VIEWERS = ("glow -p", "bat --paging=always", "batcat --paging=always", "less -R", "more")
SPEC_MATCH = "spec|plan|design|dise[ñn]o|prd|rfc|propuesta|proposal"
TEXTS = {
    "viewer": ("", "programa que abre los .md, p. ej. \"glow -p\" o \"nvim -R {file}\" (vacío = automático)"),
    "spec_match": (SPEC_MATCH, "regex: rutas de .md que se abren solas al terminar un turno"),
}


def apply_limits(cfg):
    """Pasa los umbrales de la configuración a las constantes que usa el dashboard."""
    globals().update({const: cfg[key] for key, (const, _, _) in LIMITS.items()})


def load_config():
    cfg = {"account": [], "scan": [], "include_default": True, "auto_update": True, "layout": "lista",
           "theme": "clasico", "alerts": True, "effects": True, "toasts": True, "disabled": [],
           "viewer_popup": True, **{key: default for key, (default, _) in TEXTS.items()},
           **{key: default for key, (_, default, _) in LIMITS.items()}}
    try:
        with open(config_path(), "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"cc-dash: no se pudo leer {config_path()}: {exc}")
    cfg["account"] = [a for a in raw.get("account", []) if a.get("name") and a.get("path")]
    cfg["scan"] = [d for d in raw.get("scan", []) if isinstance(d, str)]
    cfg["disabled"] = [n for n in raw.get("disabled", []) if isinstance(n, str)]
    cfg["include_default"] = bool(raw.get("include_default", True))
    cfg["auto_update"] = bool(raw.get("auto_update", True))
    cfg["layout"] = raw.get("layout") if raw.get("layout") in LAYOUTS else "lista"
    cfg["theme"] = raw.get("theme") if raw.get("theme") in THEMES else "clasico"
    cfg["alerts"] = bool(raw.get("alerts", True))
    cfg["effects"] = bool(raw.get("effects", True))
    cfg["toasts"] = bool(raw.get("toasts", True))
    cfg["viewer_popup"] = bool(raw.get("viewer_popup", True))
    for key in TEXTS:
        if isinstance(raw.get(key), str):
            cfg[key] = raw[key].strip()
    try:
        re.compile(cfg["spec_match"] or SPEC_MATCH)
    except re.error:
        cfg["spec_match"] = SPEC_MATCH
    for key in LIMITS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            cfg[key] = int(value)
    cfg["toast_max"] = max(1, cfg["toast_max"])
    return cfg


def save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = [
        "# cc-dash — cuentas de Claude Code a mostrar.",
        "# include_default: añade ~/.claude (o $CLAUDE_CONFIG_DIR) si no está listada.",
        "# scan: carpetas cuyas subcarpetas son cada una una cuenta.",
        "# auto_update: al abrir el dashboard, busca cambios en el repo y se reinstala.",
        f"include_default = {'true' if cfg['include_default'] else 'false'}",
        f"auto_update = {'true' if cfg['auto_update'] else 'false'}",
        "# layout: diseño del panel de agentes (lista, tabla, tarjetas, tiempo, diagrama); tecla d.",
        f"layout = {json.dumps(cfg['layout'])}",
        "# theme: clasico, neon, matrix o ambar (tecla T). alerts: pitido y aviso de tmux (tecla m).",
        f"theme = {json.dumps(cfg['theme'])}",
        f"alerts = {'true' if cfg['alerts'] else 'false'}",
        "# effects: arranque animado, glitch en los avisos y lluvia en reposo (tecla E).",
        f"effects = {'true' if cfg['effects'] else 'false'}",
        "# toasts: novedades como recuadros en la esquina (tecla X); el registro (n) sigue igual.",
        f"toasts = {'true' if cfg['toasts'] else 'false'}",
        "# viewer: programa que abre los .md de una sesión (tecla D y `cc-dash hook`); {file} es la ruta",
        "# y, si no aparece, se añade al final. Vacío: el primero instalado de glow, bat, less.",
        f"viewer = {json.dumps(cfg['viewer'], ensure_ascii=False)}",
        "# viewer_popup: lo abre en un popup de tmux; false lanza la orden tal cual (visores con ventana propia).",
        f"viewer_popup = {'true' if cfg['viewer_popup'] else 'false'}",
        "# spec_match: regex sobre la ruta; los .md que la cumplen se abren solos cuando el turno termina.",
        f"spec_match = {json.dumps(cfg['spec_match'], ensure_ascii=False)}",
        "# Umbrales. Se pueden cambiar aquí o con `cc-dash set CLAVE VALOR`; el dashboard los relee solo.",
        *[f"{key} = {cfg[key]}".ljust(22) + f"# {what}" for key, (_, _, what) in LIMITS.items()],
        "scan = [" + ", ".join(json.dumps(d) for d in cfg["scan"]) + "]",
        "# disabled: cuentas que no se muestran ni se leen (ventana de configuración, o enable/disable).",
        "disabled = [" + ", ".join(json.dumps(n) for n in cfg["disabled"]) + "]",
    ]
    for a in cfg["account"]:
        out += ["", "[[account]]", f"name = {json.dumps(a['name'])}", f"path = {json.dumps(a['path'])}"]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def resolve_accounts(cfg):
    found = [Account(a["name"], a["path"]) for a in cfg["account"]]
    for root in cfg["scan"]:
        root = os.path.expanduser(root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            sub = os.path.join(root, name)
            if os.path.isdir(os.path.join(sub, "projects")) or os.path.isfile(os.path.join(sub, ".claude.json")):
                found.append(Account(name, sub))
    if cfg["include_default"]:
        default = os.environ.get("CLAUDE_CONFIG_DIR") or DEFAULT_DIR
        if os.path.isdir(default):
            found.append(Account("default", default))
    accounts, paths, names = [], set(), set()
    for a in found:
        real = os.path.realpath(a.path)
        if real in paths:
            continue
        paths.add(real)
        base, n = a.name, 2
        while a.name in names:
            a.name = f"{base}-{n}"
            n += 1
        names.add(a.name)
        accounts.append(a)
    return accounts


# ── actualización ────────────────────────────────────────────────────────

UPDATE_EVERY = 6 * 3600  # segundos entre comprobaciones automáticas


def data_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), "cc-dash")


def source_dir():
    """Clon de git desde el que se instaló (lo anota install.sh)."""
    try:
        with open(os.path.join(data_dir(), "source")) as fh:
            src = fh.read().strip()
    except OSError:
        return None
    return src if os.path.isdir(os.path.join(src, ".git")) else None


def git(src, *args, timeout=30):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=8")
    return subprocess.run(["git", "-C", src, *args], capture_output=True, text=True, timeout=timeout, env=env)


def update(quiet=False):
    """Trae cambios del repo y reinstala. Devuelve True si instaló algo nuevo."""
    def say(msg, error=False):
        if error and not quiet:
            sys.exit(f"cc-dash: {msg}")
        if not error:
            print(f"cc-dash: {msg}")
        return False

    src = source_dir()
    if not src:
        return say("no hay un clon de git registrado; instala con ./install.sh desde un clon del repo", True)
    try:
        if git(src, "fetch", "--quiet").returncode:
            return say(f"no se pudo consultar el repositorio remoto de {src}", True)
        behind = git(src, "rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if behind in ("", "0"):
            return False if quiet else say(f"ya está al día ({__version__})")
        say(f"{behind} cambio(s) nuevo(s), actualizando…")
        pull = git(src, "pull", "--ff-only", "--quiet")
        if pull.returncode:
            return say(f"no se pudo actualizar {src} (¿cambios locales?): {pull.stderr.strip()}", True)
        inst = subprocess.run([os.path.join(src, "install.sh")], capture_output=True, text=True, timeout=600)
        if inst.returncode:
            return say(f"falló la reinstalación: {(inst.stderr or inst.stdout).strip()}", True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return say(f"no se pudo actualizar: {exc}", True)
    head = git(src, "log", "-1", "--format=%h %s").stdout.strip()
    say(f"actualizado a {head}")
    return True


def auto_update():
    """Comprueba como mucho cada UPDATE_EVERY; si actualiza, relanza el programa."""
    if os.environ.get("CC_DASH_NO_UPDATE") or not source_dir():
        return
    stamp = os.path.join(data_dir(), "last-check")
    try:
        if time.time() - os.path.getmtime(stamp) < UPDATE_EVERY:
            return
    except OSError:
        pass
    try:
        with open(stamp, "w"):
            pass
    except OSError:
        return
    if update(quiet=True):
        os.environ["CC_DASH_NO_UPDATE"] = "1"
        os.execv(sys.argv[0], sys.argv)


# ── estado ───────────────────────────────────────────────────────────────

class Session:
    def __init__(self, sid, account):
        self.sid = sid
        self.account = account
        self.title = ""
        self.cwd = ""
        self.branch = ""
        self.model = ""
        self.model_id = ""      # id completo, p. ej. "claude-opus-5[1m]"
        self.advisor = ""
        self.effort = ""
        self.perm = ""
        self.out_tokens = 0
        self.ctx = 0
        self.cache_pct = None
        self.cost = None
        self.lines = None       # (añadidas, quitadas)
        self.turns = 0
        self.turn_last = 0      # ms
        self.turn_total = 0     # ms
        self.turn_start = None  # inicio del turno en curso
        self.turn_spans = deque(maxlen=400)  # (inicio, fin) de los turnos ya cerrados
        self.tools = 0
        self.errors = 0
        self.tool_counts = Counter()
        self.skills = Counter()
        self.edits = Counter()  # ruta -> ediciones (Edit/Write/NotebookEdit)
        self.docs = []          # .md escritos o editados, el más reciente al final
        self.reads = set()
        self.mcp = Counter()
        self.hooks = Counter()
        self.advisor_calls = 0
        self.advisor_last = 0.0
        self.last = 0.0
        self.pending = {}   # tool_use_id -> (name, label, ts)
        self.agents = []    # lanzamientos vistos en el transcript principal
        self.side = {}      # agentId -> actividad leída del transcript del subagente
        self.err_streak = 0     # resultados con error seguidos
        self.err_last = ""
        self.rep_key = None     # última llamada (herramienta + parámetros) y cuántas veces seguidas
        self.rep_n = 0
        self.spend = deque(maxlen=3000)     # (momento, tokens de salida) para el ritmo de consumo
        self.ctx_marks = deque(maxlen=8)    # contexto al cierre de cada turno
        self.jobs = {}          # id de tarea -> comando en segundo plano {"tool", "label", "start", "end", "status"}
        self.todos = {}         # id -> {"subject", "active", "status"}: la lista de tareas de la sesión
        self.task_new = {}      # tool_use_id de un TaskCreate a la espera de su id
        self.first = 0.0        # primera actividad vista
        self.beats = deque(maxlen=1500)  # momento de cada herramienta usada: el pulso de la sesión
        self.requests = {}  # requestId -> tokens de salida ya contados
        self.items = []     # conversación: prompts, texto del asistente y herramientas
        self.open_items = {}  # tool_use_id -> item a la espera de su resultado
        self.rev = 0        # cambia con cada novedad en la conversación

    def add_item(self, item):
        self.items.append(item)
        self.rev += 1
        if len(self.items) > 3000:
            del self.items[:500]
        return item

    def window(self):
        """Ventana de contexto. El transcript no la guarda: 1M si el modelo lo indica
        (sufijo [1m]) o si el contexto ya supera 200k; si no, 200k."""
        return 1_000_000 if "[1m]" in self.model_id or self.ctx > 200_000 else 200_000

    def side_info(self, aid):
        return self.side.setdefault(aid, {"tools": 0, "tool": None, "text": "", "final": "",
                                          "out": 0, "done": None, "model": "", "last": 0.0,
                                          "beats": deque(maxlen=600), "tool_counts": Counter(),
                                          "skills": Counter(), "mcp": Counter(), "edits": Counter(),
                                          "reads": set(), "errors": 0, "rids": set(), "ctx": 0})

    def progress(self):
        """(hechas, total, texto de la tarea en curso) de la lista de tareas, o None si no hay."""
        if not self.todos:
            return None
        tasks = list(self.todos.values())
        doing = next((t for t in tasks if t["status"] == "in_progress"), None)
        return (sum(1 for t in tasks if t["status"] == "completed"), len(tasks),
                (doing["active"] or doing["subject"]) if doing else "")

    def token_rate(self, now, span=300):
        """Tokens de salida por minuto en los últimos `span` segundos."""
        return sum(n for t, n in self.spend if 0 <= now - t < span) * 60 / span

    def tokens_today(self, now):
        day = time.localtime(now)[:3]
        return sum(n for t, n in self.spend if time.localtime(t)[:3] == day)

    def turns_left(self):
        """Turnos que faltan para llenar el contexto, al crecimiento medio de los últimos."""
        marks = list(self.ctx_marks)
        growth = [b - a for a, b in zip(marks, marks[1:]) if b > a]   # una compactación no cuenta
        if not growth or not self.ctx:
            return None
        return max(0, int((self.window() - self.ctx) / (sum(growth) / len(growth))))


class Store:
    def __init__(self, accounts):
        self.accounts = accounts
        self.off = set()    # nombres de las cuentas deshabilitadas: ni se leen ni se muestran
        self.sessions = {}
        self.log = []       # (ts, sid, style, tag, text)
        self.offsets = {}
        self.files = []     # (ruta, nombre de cuenta)
        self.live = {}      # sid -> info de ~/.claude/sessions
        self.git_cache = {}      # carpeta -> (cuándo se consultó, datos | None)
        self.daily = Counter()   # (cuenta, (año, mes, día)) -> tokens de salida
        self.ready = False  # tras la carga inicial: desde ahí los fallos generan aviso
        self.alerts = []    # (sid, tipo, texto) pendientes de avisar
        self.news = deque(maxlen=300)   # registro de novedades: {"ts", "sid", "kind", "text"}
        self.aid_of = {}    # tool_use_id del lanzamiento -> agentId (de *.meta.json)
        self.agent_type = {}
        self.meta_seen = set()
        self.t_glob = self.t_live = self.t_tail = 0.0

    @property
    def active(self):
        return [a for a in self.accounts if a.name not in self.off]

    @property
    def multi(self):
        return len(self.active) > 1

    # -- lectura incremental de transcripts --
    def poll(self, now):
        if now - self.t_glob > 3:
            self.files = [(path, acct.name) for acct in self.active
                          for path in glob.glob(os.path.join(acct.projects, "**", "*.jsonl"), recursive=True)]
            self.t_glob = now
            for path, _ in self.files:
                if os.sep + "subagents" + os.sep in path and path not in self.meta_seen:
                    self.read_meta(path)
        if now - self.t_live > 1:
            self.read_live()
            self.t_live = now
        if now - self.t_tail > 0.5:
            self.t_tail = now
            grew = False
            for path, account in self.files:
                grew |= self.tail(path, account)
            if grew:
                self.log.sort(key=lambda e: e[0])
                del self.log[:-1000]

    def read_meta(self, path):
        """agent-<id>.meta.json enlaza el transcript del subagente con su lanzamiento."""
        aid = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        try:
            with open(path[:-len(".jsonl")] + ".meta.json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return
        self.meta_seen.add(path)
        if meta.get("toolUseId"):
            self.aid_of[meta["toolUseId"]] = aid
        self.agent_type[aid] = meta.get("agentType") or ""

    def tail(self, path, account):
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        pos = self.offsets.get(path, 0)
        if size < pos:
            pos = 0
        if size == pos:
            return False
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                data = fh.read(size - pos)
        except OSError:
            return False
        cut = data.rfind(b"\n")
        if cut < 0:
            return False
        self.offsets[path] = pos + cut + 1
        for raw in data[:cut].split(b"\n"):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict):
                self.ingest(rec, account)
        return True

    def read_live(self):
        live = {}
        for acct in self.active:
            for path in glob.glob(os.path.join(acct.live_dir, "*.json")):
                try:
                    with open(path) as fh:
                        info = json.load(fh)
                    pid = int(info["pid"])
                    sid = info["sessionId"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if termios is not None:  # en Windows os.kill(pid, 0) no es inocuo
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        pass
                info["_account"] = acct.name
                live[sid] = info
        self.live = live

    def event(self, ts, sid, style, tag, text, agent=None, ref=None):
        """`agent`: id del lanzamiento o agentId del subagente al que pertenece el evento.
        `ref`: de dónde sacar el detalle (un item de la conversación o {"k": "agent", "id": …})."""
        # se guarda el papel ("TOOL"), no el color, para que el log siga al tema
        style = next((role for role, color in THEME.items() if color == style), style)
        self.log.append((ts, sid, style, tag, text, agent, ref))

    def attach(self, s, b):
        """Guarda el resultado de una herramienta en el item que la lanzó."""
        item = s.open_items.pop(b.get("tool_use_id"), None)
        if item is not None:
            text = block_text(b.get("content"))
            out = [ln for ln in text.splitlines() if ln.strip()]
            item["res"], item["more"], item["err"] = out[:4], max(0, len(out) - 4), bool(b.get("is_error"))
            item["out"] = clip(text, 6000)
            s.rev += 1
        return item

    def count_tool(self, s, name, inp, ts=0.0, info=None):
        """Cuenta una herramienta en la sesión y, si es de un subagente, también en `info`."""
        s.tools += 1
        if ts:
            s.beats.append(ts)
        path = inp.get("file_path") or inp.get("notebook_path") if isinstance(inp, dict) else None
        for target in (s.__dict__, info) if info is not None else (s.__dict__,):
            target["tool_counts"][short_tool(name)] += 1
            if name == "Skill" and isinstance(inp, dict) and inp.get("skill"):
                target["skills"][inp["skill"]] += 1
            if name.startswith("mcp__"):
                target["mcp"][name.split("__")[1]] += 1
            if path and name in ("Edit", "Write", "NotebookEdit"):
                target["edits"][path] += 1
            elif path and name == "Read":
                target["reads"].add(path)
        if path and name in ("Edit", "Write", "NotebookEdit"):
            if is_doc(path):
                path = os.path.join(s.cwd or "", path)
                s.docs = [p for p in s.docs if p != path] + [path]

    def task_done(self, s, task):
        """Una tarea acaba de pasar a terminada: marca el momento (para el efecto) y avisa."""
        if not self.ready:
            return
        task["flash"] = time.time()
        tasks = list(s.todos.values())
        done = sum(1 for t in tasks if t["status"] == "completed")
        text = f"{task['subject']} · {done}/{len(tasks)}"
        if done == len(tasks):
            text += " · lista completa"
        self.alerts.append((s.sid, "task", text))

    def track_tasks(self, s, tid, name, inp):
        """Sigue la lista de tareas: TodoWrite la reemplaza entera; TaskCreate/TaskUpdate la editan."""
        if not isinstance(inp, dict):
            return
        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
            before = {t["subject"]: t for t in s.todos.values()}
            s.todos = {str(i): {"subject": t.get("content") or "", "active": t.get("activeForm") or "",
                                "status": t.get("status") or "pending"}
                       for i, t in enumerate(inp["todos"]) if isinstance(t, dict)}
            for task in s.todos.values():
                old = before.get(task["subject"])
                if old and old.get("flash"):
                    task["flash"] = old["flash"]
                if task["status"] == "completed" and old is not None and old["status"] != "completed":
                    self.task_done(s, task)
        elif name == "TaskCreate":
            s.task_new[tid] = {"subject": inp.get("subject") or "", "active": inp.get("activeForm") or "",
                               "status": "pending"}
        elif name == "TaskUpdate" and inp.get("taskId") is not None:
            key = str(inp["taskId"])
            if inp.get("status") == "deleted":
                s.todos.pop(key, None)
                return
            task = s.todos.setdefault(key, {"subject": f"tarea {key}", "active": "", "status": "pending"})
            was = task["status"]
            for src, dst in (("status", "status"), ("subject", "subject"), ("activeForm", "active")):
                if inp.get(src):
                    task[dst] = inp[src]
            if task["status"] == "completed" and was != "completed":
                self.task_done(s, task)

    def add_usage(self, s, o, msg, side=None):
        usage = msg.get("usage") or {}
        rid = o.get("requestId")
        if usage:
            # varios registros comparten requestId y el recuento crece: se suma solo la diferencia
            out = usage.get("output_tokens") or 0
            delta = out - s.requests.get(rid, 0)
            if delta > 0:
                s.requests[rid] = out
                s.out_tokens += delta
                ts = parse_ts(o.get("timestamp"))
                if ts:
                    s.spend.append((ts, delta))
                    self.daily[(s.account, time.localtime(ts)[:3])] += delta
                if side is not None:
                    side["out"] += delta
        return usage

    def ingest(self, o, account):
        sid = o.get("sessionId") or o.get("session_id")
        if not sid:
            return
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = Session(sid, account)
        kind = o.get("type")
        ts = parse_ts(o.get("timestamp"))
        if ts:
            s.last = max(s.last, ts)
            s.first = min(s.first or ts, ts)
        if o.get("cwd"):
            s.cwd = o["cwd"]
        if o.get("gitBranch") and o["gitBranch"] != "HEAD":
            s.branch = o["gitBranch"]
        side = bool(o.get("isSidechain"))
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = msg.get("content")

        if kind == "attachment":
            att = o.get("attachment") if isinstance(o.get("attachment"), dict) else {}
            if att.get("type") == "hook_success" and att.get("hookEvent"):
                s.hooks[att["hookEvent"]] += 1
            elif att.get("type") == "model" and not side and isinstance(att.get("identity"), dict):
                s.model_id = att["identity"].get("modelId") or s.model_id
            return
        if side and o.get("agentId"):
            if kind == "assistant":
                self.ingest_side(s, o["agentId"], o, msg, content, ts)
            elif kind == "user":
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        self.attach(s, b)
                        if b.get("is_error"):
                            s.side_info(o["agentId"])["errors"] += 1
            return

        if kind == "ai-title":
            s.title = o.get("aiTitle") or s.title
        elif kind == "permission-mode":
            s.perm = o.get("permissionMode") or s.perm
        elif kind == "cost-state":
            s.cost = o.get("totalCostUSD")
            s.lines = (o.get("totalLinesAdded") or 0, o.get("totalLinesRemoved") or 0)
        elif kind == "queue-operation":
            text = o.get("content") if isinstance(o.get("content"), str) else ""
            if o.get("operation") == "enqueue" and "<task-notification>" in text[:60]:
                self.agent_notice(s, text, ts)
        elif kind == "system" and o.get("subtype") == "turn_duration" and not side:
            s.turns += 1
            s.turn_last = o.get("durationMs") or 0
            s.turn_total += s.turn_last
            if s.ctx:
                s.ctx_marks.append(s.ctx)
            if ts:
                s.turn_spans.append((ts - s.turn_last / 1000, ts))
            s.turn_start = None
            s.pending.clear()
        elif kind == "assistant":
            if not side:
                if msg.get("model") and msg["model"] != "<synthetic>":
                    s.model = msg["model"]
                s.advisor = o.get("advisorModel") or s.advisor
                s.effort = o.get("effort") or s.effort
                if o.get("attributionSkill") and o["attributionSkill"] not in s.skills:
                    s.skills[o["attributionSkill"]] = 1
            usage = self.add_usage(s, o, msg)
            if usage and not side:
                read = usage.get("cache_read_input_tokens") or 0
                s.ctx = (usage.get("input_tokens") or 0) + read + (usage.get("cache_creation_input_tokens") or 0)
                s.cache_pct = round(100 * read / s.ctx) if s.ctx else None
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                name = b.get("name") or ""
                inp = b.get("input") or {}
                if b.get("type") == "text" and not side and (b.get("text") or "").strip():
                    s.add_item({"k": "text", "ts": ts, "text": b["text"].strip()})
                if b.get("type") == "tool_use":
                    self.count_tool(s, name, inp, ts)
                    label = tool_label(name, inp)
                    if not side:
                        shown = f"{inp.get('subagent_type') or 'general'}: {label}" if name in AGENT_TOOLS else label
                        item = s.open_items[b.get("id")] = s.add_item(
                            {"k": "tool", "ts": ts, "name": short_tool(name), "label": shown, "res": None,
                             "more": 0, "err": False, "out": None, "input": clip_input(inp)})
                        self.track_tasks(s, b.get("id"), name, inp)
                        key = (name, json.dumps(inp, sort_keys=True, default=str)[:600])
                        s.rep_n = s.rep_n + 1 if key == s.rep_key else 1
                        s.rep_key = key
                    else:
                        item = None
                    if name in AGENT_TOOLS and not side:
                        s.agents.append({
                            "id": b.get("id"), "aid": None, "start": ts, "end": None,
                            "type": inp.get("subagent_type") or "general",
                            "desc": inp.get("description") or "",
                            "prompt": inp.get("prompt") or "",
                            "model": inp.get("model") or "",
                            "result": "", "status": "",
                        })
                        self.event(ts, sid, SWARM, "Agent", f"▶ {label}", b.get("id"),
                                   {"k": "agent", "id": b.get("id")})
                    else:
                        s.pending[b.get("id")] = (short_tool(name), label, ts)
                        self.event(ts, sid, DIM if side else TOOL,
                                   ("↳ " if side else "") + short_tool(name), label, ref=item)
                elif "advisor" in name.lower() or "advisor" in str(b.get("type")):
                    if b.get("type", "").endswith("tool_use"):
                        s.advisor_calls += 1
                        s.advisor_last = ts
                        self.event(ts, sid, ADVISOR, "advisor", "consulta al advisor")
        elif kind == "user":
            if isinstance(content, str):
                if not side and s.turn_start is None:
                    s.turn_start = ts
                if not side and not o.get("isMeta") and not content.lstrip().startswith("<"):
                    s.pending.clear()
                    s.err_streak = s.rep_n = 0   # un prompt nuevo empieza de cero
                    s.rep_key = None
                    item = s.add_item({"k": "prompt", "ts": ts, "text": content.strip()})
                    self.event(ts, sid, LEAD, "prompt", "› " + content, ref=item)
                return
            result = o.get("toolUseResult") if isinstance(o.get("toolUseResult"), dict) else {}
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                item = self.attach(s, b)
                created = s.task_new.pop(tid, None)
                if created:
                    found = re.search(r"Task #(\S+) created", block_text(b.get("content")))
                    s.todos[found.group(1) if found else f"n{len(s.todos)}"] = created
                done = s.pending.pop(tid, None)
                if b.get("is_error"):
                    s.errors += 1
                    s.err_streak += 1
                    s.err_last = done[0] if done else ""
                else:
                    s.err_streak = 0
                if done and b.get("is_error"):
                    self.event(ts, sid, ERR, done[0], "✗ " + (done[1] or "error"), ref=item)
                if result.get("backgroundTaskId") and not any(a["id"] == tid for a in s.agents):
                    # un comando que quedó corriendo en segundo plano: termina con un aviso aparte
                    s.jobs[str(result["backgroundTaskId"])] = {
                        "tool": done[0] if done else "Bash", "label": done[1] if done else "",
                        "start": (done[2] if done else 0) or ts, "end": None, "status": ""}
                for a in s.agents:
                    if a["id"] != tid:
                        continue
                    a["aid"] = result.get("agentId") or a["aid"]
                    if result.get("resolvedModel"):
                        a["model"] = short_model(result["resolvedModel"])
                    # en segundo plano el resultado solo confirma el lanzamiento
                    if result.get("isAsync") or result.get("status") == "async_launched":
                        break
                    if a["end"] is None:
                        a["result"] = block_text(b.get("content"))
                        self.finish(s, a, ts, "error" if b.get("is_error") else "completed")

    def ingest_side(self, s, aid, o, msg, content, ts):
        """Un mensaje del propio subagente (transcript en <sesión>/subagents/)."""
        info = s.side_info(aid)
        info["last"] = max(info["last"], ts)
        if msg.get("model"):
            info["model"] = msg["model"]
        if o.get("requestId"):
            info["rids"].add(o["requestId"])   # cada llamada al modelo es una ronda del subagente
        usage = self.add_usage(s, o, msg, info)
        if usage:
            info["ctx"] = ((usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                           + (usage.get("cache_creation_input_tokens") or 0)) or info["ctx"]
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                info["text"] = b["text"]
            elif b.get("type") == "tool_use":
                name = b.get("name") or ""
                inp = b.get("input") or {}
                if name == "SubagentHandback":
                    info["final"] = str(inp.get("message") or "") if isinstance(inp, dict) else ""
                    info["done"] = ts
                    continue
                info["tools"] += 1
                info["tool"] = (short_tool(name), tool_label(name, inp))
                info["done"] = None
                self.count_tool(s, name, inp, ts, info)
                if ts:
                    info["beats"].append(ts)
                item = s.open_items[b.get("id")] = {
                    "k": "tool", "ts": ts, "name": info["tool"][0], "label": info["tool"][1], "res": None,
                    "more": 0, "err": False, "out": None, "input": clip_input(inp)}
                self.event(ts, s.sid, DIM, "↳ " + (self.agent_type.get(aid) or "agente"),
                           f"{info['tool'][0]}: {info['tool'][1]}", aid, item)

    def agent_notice(self, s, text, ts):
        task = re.search(r"<task-id>([^<]+)</task-id>", text)
        status = re.search(r"<status>([^<]+)</status>", text)
        if not task:
            return
        for a in s.agents:
            if (a["aid"] or self.aid_of.get(a["id"])) == task.group(1) and a["end"] is None:
                self.finish(s, a, ts, status.group(1) if status else "completed")
        job = s.jobs.get(task.group(1))
        if job and job["end"] is None:
            job["end"], job["status"] = ts or time.time(), status.group(1) if status else "completed"
            if self.ready:
                job["flash"] = time.time()
                self.alerts.append((s.sid, "job" if job["status"] == "completed" else "fail",
                                    f"{job['tool']}: {job['label']} ({clock(job['end'] - job['start'])})"))

    def finish(self, s, a, ts, status):
        a["end"] = ts or time.time()
        a["status"] = status
        ok = status == "completed"
        if self.ready:
            self.alerts.append((s.sid, "agent" if ok else "fail",
                                f"{a['type']}: {a['desc']} ({clock(a['end'] - a['start'])})"))
        self.event(ts, s.sid, SWARM if ok else ERR, "Agent",
                   f"{'✓' if ok else '✗'} {a['desc']} ({clock(a['end'] - a['start'])})", a["id"],
                   {"k": "agent", "id": a["id"]})

    # -- vistas --
    def state(self, s):
        info = self.live.get(s.sid)
        if not info:
            return "ended"
        status = info.get("status")
        # Claude Code escribe "waiting" cuando hay un permiso, pregunta o diálogo pendiente;
        # el segundo caso cubre versiones que no lo hacen.
        if status == "waiting" or (status == "busy" and any(p[0] in ASK_TOOLS for p in s.pending.values())):
            return "waiting"
        return "busy" if status == "busy" else "idle"

    def waiting_for(self, s):
        """Qué necesita del usuario una sesión en estado "waiting"."""
        raw = (self.live.get(s.sid) or {}).get("waitingFor") or ""
        tool = max(s.pending.values(), key=lambda p: p[2]) if s.pending else None
        if tool and tool[0] in ASK_TOOLS:
            return ASK_TOOLS[tool[0]]
        if raw == "permission prompt":
            return f"permiso para {tool[0]}" + (f": {tool[1]}" if tool[1] else "") if tool else "un permiso"
        return {"input needed": "una respuesta", "dialog open": "cerrar un diálogo"}.get(raw, raw or "tu respuesta")

    def agents(self, s):
        """Subagentes de la sesión con su actividad: en marcha primero, luego recientes."""
        alive = self.state(s) != "ended"
        out = []
        for n, a in enumerate(s.agents):
            aid = a["aid"] or self.aid_of.get(a["id"])
            side = s.side.get(aid) or {}
            end = a["end"] or side.get("done")
            out.append({
                **a, "aid": aid, "end": end, "running": end is None and alive,
                "tools": side.get("tools", 0), "tool": side.get("tool"), "out": side.get("out", 0),
                "color": AGENT_COLORS[n % len(AGENT_COLORS)],   # por orden de creación: no cambia
                "beats": side.get("beats") or (),
                "side": side,   # actividad propia: herramientas, archivos, skills, errores
                "rounds": len(side.get("rids") or ()), "ctx": side.get("ctx", 0),
                "model": a["model"] or (short_model(side["model"]) if side.get("model") else ""),
                "status": a["status"] or ("completed" if end else ""),
                "response": (side.get("final") or a["result"] or side.get("text") or "").strip(),
                # segundos sin dar señales; solo importa mientras está en marcha
                "quiet": time.time() - (side.get("last") or a["start"] or time.time()),
            })
        out.sort(key=lambda v: (not v["running"], -(v["start"] or 0)))
        return out

    def visible(self, s, now, show_all=False, sel=None):
        """Los subagentes que siguen en uso: en marcha, recién terminados o el seleccionado."""
        if s is None:
            return []
        return [v for v in self.agents(s)
                if show_all or v["running"] or v["id"] == sel or (v["end"] and now - v["end"] < LINGER)]

    def name(self, s):
        info = self.live.get(s.sid) or {}
        return info.get("name") or os.path.basename(s.cwd or "") or s.sid[:8]

    def label(self, s):
        return f"{s.account}:{self.name(s)}" if self.multi else self.name(s)

    def ordered(self, account=None):
        for sid, info in self.live.items():
            if sid not in self.sessions:
                s = self.sessions[sid] = Session(sid, info["_account"])
                s.cwd = info.get("cwd") or ""
        pool = [s for s in self.sessions.values() if account in (None, s.account) and s.account not in self.off]
        live = [s for s in pool if s.sid in self.live]
        live.sort(key=lambda s: (s.account, self.live[s.sid].get("startedAt") or 0))
        rest = [s for s in pool if s.sid not in self.live]
        rest.sort(key=lambda s: -s.last)
        return live + rest

    def git_info(self, s, now, refresh=False):
        """Estado de git de la carpeta de la sesión. Solo consulta con `refresh`, y cada 20 s como mucho."""
        if not s.cwd:
            return None
        checked, info = self.git_cache.get(s.cwd, (0, None))
        if not refresh or now - checked < 20:
            return info
        info = None
        try:
            status = git(s.cwd, "--no-optional-locks", "status", "--porcelain=v1", "-b", timeout=3)
            if status.returncode == 0:
                lines = status.stdout.splitlines()
                branch = lines[0][3:].split("...")[0] if lines and lines[0].startswith("## ") else ""
                files = [ln for ln in lines if not ln.startswith("## ")]
                since = (self.live.get(s.sid) or {}).get("startedAt")
                since = since / 1000 if since else s.first
                commits = git(s.cwd, "rev-list", "--count", f"--since={int(since)}", "HEAD", timeout=3) if since else None
                info = {"branch": branch,
                        "untracked": sum(1 for ln in files if ln.startswith("??")),
                        "changed": sum(1 for ln in files if not ln.startswith("??")),
                        "commits": int(commits.stdout.strip() or 0) if commits and commits.returncode == 0 else 0}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        self.git_cache[s.cwd] = (now, info)
        return info

    def stuck(self, s, now):
        """Motivos para sospechar que una sesión que trabaja está atascada (vacío = todo bien)."""
        if self.state(s) != "busy":
            return []
        why = []
        if STUCK_ERRORS and s.err_streak >= STUCK_ERRORS:
            why.append(f"{s.err_streak} errores seguidos" + (f" en {s.err_last}" if s.err_last else ""))
        if STUCK_REPEAT and s.rep_n >= STUCK_REPEAT and s.rep_key:
            why.append(f"repite {short_tool(s.rep_key[0])} {s.rep_n} veces igual")
        if s.pending:
            name, _, since = min(s.pending.values(), key=lambda p: p[2])
            if STUCK_TOOL and since and now - since > STUCK_TOOL:
                why.append(f"{name} lleva {age(now - since)} sin volver")
        elif STUCK_SILENT and s.last and now - s.last > STUCK_SILENT:
            why.append(f"sin actividad hace {age(now - s.last)}")
        return why

    def auto_focus(self, sessions):
        if not sessions:
            return None
        rank = {"waiting": 3, "busy": 2, "idle": 1, "ended": 0}
        now = time.time()
        return max(sessions, key=lambda s: (rank[self.state(s)], bool(self.stuck(s, now)), s.last))


# ── lienzo para el diagrama ──────────────────────────────────────────────

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
        lo, hi = min(xs + [trunk]), max(xs + [trunk])
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


def blend(a, b, k):
    """Color intermedio entre dos #rrggbb; k=0 da a, k=1 da b."""
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ca, cb))


def breath(color, now, offset=0.0):
    """Color que "respira" entre el tono de las líneas y `color`."""
    k = (1 - math.cos(2 * math.pi * (now + offset) / 2.4)) / 2
    return blend(LINE, color, 0.15 + 0.85 * k)


def pulse(beats, now, width, span=600):
    """Actividad de los últimos `span` segundos como línea braille tipo electro.
    Devuelve (caracteres, lista de cuáles tienen pico)."""
    n = width * 2
    counts = [0] * n
    for t in beats:
        back = now - t
        if 0 <= back < span:
            counts[n - 1 - int(back / span * n)] += 1
    top = max(counts) or 1
    levels = [1 + -(-3 * c // top) if c else 1 for c in counts]   # 1 = línea base, 2-4 = pico
    left, right = (0x40, 0x04, 0x02, 0x01), (0x80, 0x20, 0x10, 0x08)
    chars = "".join(chr(0x2800 + sum(left[:levels[i]]) + sum(right[:levels[i + 1]])) for i in range(0, n, 2))
    return chars, [levels[i] > 1 or levels[i + 1] > 1 for i in range(0, n, 2)]


def graph_rows(h):
    """Filas que ocupa el gráfico de actividad (leyenda incluida) en un panel de `h` filas."""
    return 4 if h >= 20 else 3


def activity_graph(cv, y, w, h, s, agents, now, span=600, focus=None):
    """Matriz de puntos con las herramientas usadas: una columna por intervalo, apilada por
    quién la usó (el lead y cada subagente en su color).

    Cuanto más uso, más intenso el color; la columna de ahora destella con cada herramienta nueva."""
    rows = h - 1
    side = Counter(t for v in agents for t in v["beats"])
    lead = list((Counter(s.beats) - side).elements())
    sources = [("lead", LEAD, lead)] + [(v["type"], v["color"], v["beats"]) for v in agents
                                        if any(0 <= now - t < span for t in v["beats"]) or v["running"]
                                        or v["id"] == focus]
    if focus is not None:   # modo foco: el agente enfocado primero (abajo) y el resto apagado
        mine = next((v for v in agents if v["id"] == focus), None)
        if mine is not None:
            others = [(n, blend(LINE, c, 0.3), b) for n, c, b in sources if b is not mine["beats"]]
            sources = [("◎ " + mine["type"], mine["color"], mine["beats"])] + others
    counts = [[0] * w for _ in sources]
    for k, (_, _, beats) in enumerate(sources):
        for t in beats:
            back = now - t
            if 0 <= back < span:
                counts[k][w - 1 - int(back / span * w)] += 1
    totals = [sum(c[x] for c in counts) for x in range(w)]
    top = max(totals) or 1
    last = max((t for _, _, b in sources for t in b), default=0)
    rate = sum(1 for t in (sources[0][2] if focus is not None else s.beats) if 0 <= now - t < 60)
    # leyenda: un latido que se acelera con el ritmo, quién aporta cuánto y el ritmo total
    beat = (now * (0.8 + min(3.0, rate / 10))) % 1 < 0.22
    x = segs(cv, 0, y, [("◆ " if beat else "◇ ", f"bold {LEAD}" if beat else LINE), ("ACTIVIDAD ", f"bold {FG}"),
                        ("10 min  ", DIM)])
    for (name, color, beats) in sources:
        n = sum(1 for t in beats if 0 <= now - t < span)
        part = f"■ {trunc(name, 14)} {n}  "
        if x + len(part) > w - 26:
            break
        x = segs(cv, x, y, [("■ ", color), (f"{trunc(name, 14)} ", FG), (f"{n}  ", DIM)])
    right = f"{rate} tools/min" + ("" if focus is not None else f" · {ktok(round(s.token_rate(now)))} tok/min")
    cv.put(max(x, w - len(right)), y, right, DIM)
    fresh = now - last < 1.2
    base = y + rows
    levels = (0x40 | 0x80, 0x04 | 0x20, 0x02 | 0x10, 0x01 | 0x08)   # filas de puntos, de abajo arriba
    for col in range(w):
        total = totals[col]
        tick = (w - 1 - col) % max(1, round(w / 5)) == 0   # una marca cada 2 minutos
        if not total:   # línea base de puntos, con las marcas de tiempo más claras
            cv.put(col, base, "⣀", DIM if tick else LINE)
            continue
        height = max(1, round(total / top * rows * 4))
        k = 0.4 + 0.6 * total / top
        bounds, acc = [], 0
        for c in counts:
            acc += c[col]
            bounds.append(acc / total * height)
        for r in range(rows):
            dots = min(4, height - 4 * r)
            if dots <= 0:
                break
            mid = 4 * r + dots / 2
            color = sources[next(i for i, b in enumerate(bounds) if mid <= b + 1e-9)][1]
            style = blend(LINE, color, k)
            if 4 * r + dots == height:   # el punto más alto, más claro: la cresta
                style = blend(style, "#ffffff", 0.25)
            if k > 0.8:
                style = f"bold {style}"
            if col == w - 1 and fresh:   # latido
                style = "bold #ffffff"
            cv.put(col, base - r, chr(0x2800 + sum(levels[:dots])), style)


def hpath(x0, x1, y):
    step = 1 if x1 >= x0 else -1
    return [(x, y) for x in range(x0, x1 + step, step)]


# ── diseño "diagrama": el lead y un rostro por subagente ────────────────

# Rostros en braille con tramado ordenado: cada uno es una función que da la luz de
# cada píxel (0 negro, 1 brillo, EYE ojo encendido, None fondo). Una celda de braille
# son 2x4 píxeles, así que un rostro de 6 filas tiene 24x24 píxeles.
EYE = -1.0
IMPLANT = -2.0   # píxel que brilla como los ojos: los implantes de la segunda evolución
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
HIGHLIGHT = "#d1f7ff"   # el brillo cian pálido de la piel / el metal


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
FACE_SIZE = {6: (24, 24), 4: (20, 16), 3: (12, 12)}   # filas -> píxeles (ancho, alto)
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
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#00f0ff" if r > 200 and g < 140 else BLOOD


def agent_face(v, phase, i, now, rows, tier=0):
    """Rostro tramado de un subagente, en su color; la expresión dice cómo va."""
    _, fn = FACES[sum(map(ord, v["type"])) % len(FACES)]
    px = face_pixels(fn, rows, tier)
    color = v["color"]
    failed = bool(v["end"]) and v["status"] != "completed"
    scan = None
    if v["running"]:
        palette = (blend(LINE, color, 0.5), color, "#ffd75e" if tier >= 4 else HIGHLIGHT)   # EVO MAX: brillos dorados
        k = (1 - math.cos(2 * math.pi * (now + i * 0.7) / 1.6)) / 2
        glow = eye_color(color)
        eyes = None if (phase + i * 11) % 41 == 0 else f"bold {blend(glow, '#ffffff', 0.5 * k)}"
        if fn is _visor:   # el visor barre de lado a lado
            w = len(px[0])
            at = (phase + i * 3) % (2 * w - 2)
            scan = at if at < w else 2 * w - 2 - at
    elif failed:
        palette, eyes = (blend(LINE, ERR, 0.4), blend(LINE, ERR, 0.75), blend(LINE, "#ffb3b8", 0.6)), f"bold {ERR}"
    elif v["end"]:
        palette = (blend(LINE, color, 0.2), blend(LINE, color, 0.4), blend(LINE, HIGHLIGHT, 0.45))
        eyes = blend(LINE, color, 0.55)
    else:   # interrumpido: apagado
        palette, eyes = (LINE, LINE, blend(LINE, FG, 0.3)), None
    cells = dither(px, palette, eyes, scan, crack=failed)
    if v["running"] and v["tool"] and (phase + i * 5) % 7 < 2:   # señal corrupta mientras usa una herramienta
        g = (phase // 3 + i) % len(cells)
        accent = "#ff2a6d" if color == "#05d9e8" else "#05d9e8"
        cells[g] = [(" ", "")] + [(ch, accent) for ch, _ in cells[g][:-1]]
    return cells


def lead_face(state, now, tier=0):
    px = face_pixels(_oni, 3, tier)
    if state == "ended":
        return dither(px, (LINE, LINE, blend(LINE, FG, 0.3)), None)
    k = (1 - math.cos(2 * math.pi * now / 1.6)) / 2
    eyes = f"bold {blend(WAIT if state == 'waiting' else BLOOD, '#ffffff', 0.3 * k)}"
    return dither(px, (blend(LINE, LEAD, 0.5), LEAD, "#ffe0c2"), eyes if state != "idle" else blend(LINE, BLOOD, 0.6))


BLOOD = "#ff2a3c"


def game(rounds, ctx, window):
    """HP = contexto libre; cada 10 rondas sube un nivel; cada 10 niveles el rostro evoluciona."""
    used = min(100, round(100 * ctx / window)) if ctx and window else 0
    lvl = rounds // 10 + 1
    return {"hp": 100 - used, "lvl": lvl, "xp": rounds % 10, "rounds": rounds, "tier": min(4, lvl // 10)}


def lead_game(s):
    return game(s.turns, s.ctx, s.window())


def agent_game(v):
    return game(v["rounds"], v["ctx"], 1_000_000 if v["ctx"] > 200_000 else 200_000)


EVO = ("", "EVO I", "EVO II", "EVO III", "EVO MAX")


def hp_color(hp):
    return TOOL if hp > 50 else WAIT if hp > 20 else ERR


def meter(value, total, width, color, empty=None):
    fill = round(width * value / total) if total else 0
    return [("▰" * fill, color), ("▱" * (width - fill), empty or LINE)]


def stat_rows(g, color, n, now):
    """Las barras que van bajo el rostro: HP, nivel (hacia la próxima evolución) y XP (rondas del nivel)."""
    hp = g["hp"]
    hpc = f"bold {breath(ERR, now * 2)}" if hp <= 10 else hp_color(hp)
    rows = [[("HP ", DIM), *meter(hp, 100, 8, hpc), (f" {hp}%", hpc)],
            [("LV ", DIM), (f"{g['lvl']:<3}", f"bold {color}"), *meter(g["lvl"] % 10, 10, 10, color)],
            [("XP ", DIM), *meter(g["xp"], 10, 8, HIGHLIGHT), (f" {g['xp']}/10", HIGHLIGHT)]]
    if n >= 3:
        return rows
    if n == 2:
        return [rows[0], [("LV", DIM), (f"{g['lvl']} ", f"bold {color}"), ("XP ", DIM),
                          *meter(g["xp"], 10, 5, HIGHLIGHT), (f" {g['xp']}/10", HIGHLIGHT)]]
    return [[("HP", DIM), (f"{hp}% ", hpc), ("LV", DIM), (f"{g['lvl']} ", f"bold {color}"),
             ("XP", DIM), (f"{g['xp']}", HIGHLIGHT)]]


def put_stats(cv, x, y, rows):
    for r, parts in enumerate(rows):
        segs(cv, x, y + r, parts)


def put_cells(cv, x, y, cells):
    for r, row in enumerate(cells):
        for c, (ch, style) in enumerate(row):
            if ch != " ":
                cv.put(x + c, y + r, ch, style)


def diagram(store, s, w, h, now, sel=None, show_all=False, idle_rain=False, focus=None):
    """El lead en una fila y, debajo, una fila por subagente: su rostro, quién es y, a la
    derecha, el log de lo que fue haciendo."""
    cv = Canvas(w, h)
    if s is None:
        cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", DIM)
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
    lst = WAIT if state == "waiting" else LEAD if state != "ended" else LINE
    lg = lead_game(s)
    cv.box(0, 0, w, lead_h, lst)
    # todas las filas comparten columnas: hueco fijo para el rostro y sus barras, luego el texto
    slot = 16
    tx = 2 + slot + 2
    put_cells(cv, 2 + (slot - 6) // 2, 1, lead_face(state, now, lg["tier"]))
    put_stats(cv, 2, 4, stat_rows(lg, lst if state != "ended" else DIM, stat_n, now))
    iw = w - tx - 2
    spin = SPIN[phase % len(SPIN)] if state == "busy" else "◆" if state == "waiting" else "■"
    right = f"{spin} {STATES[state][1]}"
    who = f"{short_model(s.model)} · lead · {store.label(s)}" + (f" · effort {s.effort}" if s.effort else "")
    cv.put(tx, 1, trunc(who, max(0, iw - len(right) - 2)), f"bold {lst}")
    cv.put(tx + iw - len(right), 1, right, f"bold {lst}")
    stats = f"{s.tools} tools · salida {ktok(s.out_tokens)}"
    if s.cost is not None:
        stats += f" · ${s.cost:.2f}"
    if s.advisor:
        stats += f" · advisor {short_model(s.advisor)}" + (f" ×{s.advisor_calls}" if s.advisor_calls else "")
    cv.put(tx, 2, trunc(stats, iw), DIM)
    why = store.stuck(s, now)
    plain = list(s.pending.values())
    if state == "waiting":
        doc = f" · D abre {os.path.basename(s.docs[-1])}" if s.docs else ""
        cv.put(tx, 3, trunc(f"◆ necesita {store.waiting_for(s)}{doc}", iw), f"bold {breath(WAIT, now)}")
    elif why:
        cv.put(tx, 3, trunc("⚠ posible atasco: " + " · ".join(why), iw), f"bold {breath(ERR, now)}")
    elif plain and state != "ended":
        name, lab, _ = max(plain, key=lambda p: p[2])
        more = f" (+{len(plain) - 1})" if len(plain) > 1 else ""
        cv.put(tx, 3, trunc(f"▶ {name}{more}: {lab}", iw), TOOL)
    else:
        cv.put(tx, 3, trunc(s.title or "—", iw), DIM)
    level = f"{lg['rounds']} ronda{'s' if lg['rounds'] != 1 else ''} · nivel {lg['lvl']}, sube en {10 - lg['xp']}"
    level += f" · {EVO[lg['tier']]}" if lg["tier"] else f" · evoluciona en el nivel {(lg['lvl'] // 10 + 1) * 10}"
    if stat_n >= 2:
        cv.put(tx, 4, trunc(level, iw), DIM)
    if stat_n >= 3:
        cv.put(tx, 5, trunc(f"HP = contexto libre: {lg['hp']}%" + (f" de {ktok(s.window())}" if s.ctx else ""), iw), DIM)

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
        cv.put(max(0, w - len(note)), h - 1, note, SWARM if live_hidden else DIM)

    if not shown:
        if idle_rain == "force" or (idle_rain and not everyone):
            rain(cv, top, now, h)
        mid = w // 2
        size = 6 if avail >= 8 else 4
        put_cells(cv, mid - FACE_SIZE[size][0] // 4, top + 1, dither(face_pixels(_visor, size),
                                                                    (LINE, LINE, blend(LINE, FG, 0.35)), None))
        msg = trunc("sin subagentes · aparecen aquí, uno por fila, a medida que el lead los invoca", w - 2)
        cv.put(max(0, mid - len(msg) // 2), top + size + 2, msg, DIM)
        return cv.text()
    if idle_rain == "force":
        rain(cv, top, now, h)

    gap = 2
    cw = (w - indent - gap * (per_row - 1)) // per_row
    inner_rows = card_h - 2
    rows_shown = -(-len(shown) // per_row)
    spare = 0
    if indent:   # guía tenue desde el lead hasta cada fila
        mids = [top + r * card_h + card_h // 2 for r in range(rows_shown)]
        for yy in range(top, mids[-1]):
            cv.put(1, yy, "│", LINE)
        for r, yy in enumerate(mids):
            cv.put(1, yy, ("└" if r == rows_shown - 1 else "├") + "──", LINE)
    for n, v in enumerate(shown):
        row, col = divmod(n, per_row)
        x, y = indent + col * (cw + gap), top + row * card_h
        this_h = card_h + (spare if row == rows_shown - 1 else 0)
        live, chosen = v["running"], v["id"] == sel
        failed = bool(v["end"]) and v["status"] != "completed"
        edge = "bold #ffffff" if chosen else ERR if failed else v["color"] if live else LINE
        cv.box(x, y, cw, this_h, edge, heavy=chosen)
        show_face = cw >= 64
        fw = FACE_SIZE[face_rows][0] // 2
        g = agent_game(v)
        if show_face:   # centrado en un hueco del mismo ancho para todos; las barras, debajo
            put_cells(cv, x + 2 + (slot - fw) // 2, y + 1, agent_face(v, phase, n, now, face_rows, g["tier"]))
            put_stats(cv, x + 2, y + 1 + face_rows, stat_rows(g, v["color"] if live else DIM, stat_n, now))
        ix = x + (tx if show_face else 2)
        iw = x + cw - 2 - ix   # el texto ocupa todo el ancho de la fila
        inner = this_h - 2
        icon, ist = agent_icon(v, phase, n)
        stat = (f"{g['rounds']} ronda{'s' if g['rounds'] != 1 else ''} · " + agent_time(v, now)
                + (f" · {v['tools']} tools" if v["tools"] else "") + (f" · {v['model']}" if v["model"] else ""))
        if g["tier"]:
            stat = f"{EVO[g['tier']]} · " + stat
        cv.put(ix, y + 1, icon, ist)
        cv.put(ix + 2, y + 1, trunc(v["type"], max(0, iw - len(stat) - 4)), f"bold {v['color']}" if live else "bold" if chosen else FG)
        cv.put(ix + iw - len(stat), y + 1, stat, v["color"] if live else DIM)
        lines = []   # (texto, estilo) bajo la cabecera, por orden de importancia
        desc = textwrap.wrap(v["desc"], iw) or [""]
        lines.append((desc[0] if len(desc) == 1 or inner < 5 else desc[0], FG if live or chosen else DIM))
        if len(desc) > 1 and inner >= 5:
            lines.append((trunc(" ".join(desc[1:]), iw), FG if live or chosen else DIM))
        done_ok = bool(v["end"]) and not failed
        if not (done_ok and v["response"]):   # terminado con respuesta: se muestra la respuesta abajo
            text, tst = agent_now(v)
            lines.insert(1 if inner < 5 else len(lines), (trunc(text, iw), tst))
        if v["response"] and (v["running"] or v["end"]):   # lo último que dijo, si queda sitio
            for part in textwrap.wrap(" ".join(v["response"].split()), iw - 2)[:max(0, inner - 1 - len(lines))]:
                lines.append(("“ " + part, blend(LINE, FG, 0.6)))
        for k, (text, style) in enumerate(lines[:inner - 1]):
            cv.put(ix, y + 2 + k, trunc(text, iw), style)
    return cv.text()


# ── diseños alternativos del panel de agentes (tecla d) ──────────────────

LAYOUTS = ("lista", "tabla", "tarjetas", "tiempo", "diagrama")


def segs(cv, x, y, parts):
    """Escribe tramos (texto, estilo) seguidos y devuelve la columna final."""
    for text, style in parts:
        cv.put(x, y, text, style)
        x += len(text)
    return x


def agent_icon(v, phase, i=0):
    if v["running"]:
        return SPIN[(phase + i * 3) % len(SPIN)], f"bold {v['color']}"
    if v["end"] and v["status"] != "completed":
        return "✗", f"bold {ERR}"
    if v["end"]:
        return "✓", f"bold {TOOL}"
    return "■", DIM


def agent_now(v):
    """Qué hace el subagente ahora mismo, o cómo terminó."""
    if STUCK_TOOL and v["running"] and v["quiet"] > STUCK_TOOL:
        return (f"⚠ sin actividad hace {age(v['quiet'])}", ERR)
    if v["running"]:
        return (f"▶ {v['tool'][0]}: {v['tool'][1]}", TOOL) if v["tool"] else ("pensando…", DIM)
    if v["end"] and v["status"] != "completed":
        return (v["status"] or "error", ERR)
    if not v["end"]:
        return ("interrumpido", DIM)
    return (v["response"] or "sin respuesta", DIM)


def n_tools(n):
    return f"{n} tool{'s' if n != 1 else ''}"


def agent_time(v, now):
    if v["running"]:
        return clock(now - v["start"])
    return clock(v["end"] - v["start"]) if v["end"] else "—"


def agent_bar_style(v, now):
    if v["running"]:
        return f"bold {breath(v['color'], now)}"
    return ERR if v["end"] and v["status"] != "completed" else blend(LINE, v["color"], 0.45)


def lead_block(cv, store, s, w, now, phase, compact=False):
    """Cabecera del lead (3 líneas, o 1 en `compact`). Devuelve la siguiente fila libre."""
    state = store.state(s)
    color = WAIT if state == "waiting" else LEAD if state != "ended" else DIM
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
        cv.put(0, 1, trunc(f"◆ necesita {store.waiting_for(s)}{doc}", w), f"bold {breath(WAIT, now)}")
    elif why:
        cv.put(0, 1, trunc("⚠ posible atasco: " + " · ".join(why), w), f"bold {breath(ERR, now)}")
    elif s.pending and state != "ended":
        name, lab, _ = max(s.pending.values(), key=lambda p: p[2])
        more = f" (+{len(s.pending) - 1})" if len(s.pending) > 1 else ""
        cv.put(0, 1, trunc(f"▶ {name}{more}: {lab}", w), TOOL)
    else:
        cv.put(0, 1, trunc(s.title or "—", w), DIM)
    g = lead_game(s)
    x = segs(cv, 0, 2, [("HP ", DIM), *meter(g["hp"], 100, 8, hp_color(g["hp"])), (f" {g['hp']}%  ", hp_color(g["hp"])),
                        ("LV ", DIM), (f"{g['lvl']} ", f"bold {color}"), *meter(g["lvl"] % 10, 10, 10, color),
                        ("  XP ", DIM), *meter(g["xp"], 10, 8, HIGHLIGHT), (f" {g['xp']}/10", HIGHLIGHT),
                        (f"  {g['rounds']} rondas", DIM)])
    extra = [f"salida {ktok(s.out_tokens)}"]
    if s.cache_pct is not None:
        extra.append(f"caché {s.cache_pct}%")
    if s.cost is not None:
        extra.append(f"${s.cost:.2f}")
    if s.advisor:
        extra.append(f"advisor {short_model(s.advisor)}")
    cv.put(x + 3, 2, trunc(" · ".join(extra), max(0, w - x - 3)), DIM)
    tasks = s.progress()
    if tasks:
        done, total, doing = tasks
        fill = round(done / total * 12)
        just = next((t for t in s.todos.values() if now - t.get("flash", 0) < FRESH), None)
        glow = f"bold {breath(TOOL, now * 3)}"
        x = segs(cv, 0, 3, [("tareas   ", DIM), ("▰" * fill, glow if just else TOOL), ("▱" * (12 - fill), LINE),
                            (f" {done}/{total}", FG)])
        if just:   # una tarea acaba de terminar: se anuncia aquí unos segundos
            cv.put(x + 3, 3, trunc(f"✦ terminada: {just['subject']}", max(0, w - x - 3)), glow)
        else:
            cv.put(x + 3, 3, trunc(f"▶ {doing}" if doing else "", max(0, w - x - 3)), TOOL)
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
    cv.put(x + used, y, "ahora", DIM)
    free = 0  # primera columna libre, para que las etiquetas no se pisen
    for frac in (0, 1 / 3, 2 / 3):
        label = time.strftime(fmt, time.localtime(t0 + (t1 - t0) * frac))
        at = int(w * frac)
        if at >= free and at + len(label) + 1 < used:
            cv.put(x + at, y, label, DIM)
            free = at + len(label) + 2


def time_bar(cv, x, y, w, t0, t1, start, end, style, running):
    span = max(1.0, t1 - t0)
    a = min(w - 1, max(0, int((start - t0) / span * w)))
    b = w if running else min(w, max(a + 1, int(((end or t1) - t0) / span * w)))
    cv.put(x, y, "·" * a, LINE)
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
    x = segs(cv, 0, y, [("SUBAGENTES  ", f"bold {SWARM}"), (f"{live} en marcha · {done} terminados", FG)])
    notes = []
    if len(agents) > len(shown):
        notes.append(f"+{len(agents) - len(shown)} más · ←→")
    gone = len(everyone) - len(agents)
    if gone:
        notes.append(f"{gone} oculto{'s' if gone != 1 else ''} · t")
    if notes:
        cv.put(x + 3, y, trunc(" · ".join(notes), max(0, room - x - 3)), DIM)


def board(store, s, w, h, now, sel=None, show_all=False, layout="lista", idle_rain=False, focus=None):
    """Panel del lead y sus subagentes sin flechas, en el diseño elegido."""
    cv = Canvas(w, h)
    if s is None:
        cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", DIM)
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
        flex = max(20, w - 4 - 12 - 11 - 7 - 6)
        c_task, c_now = flex // 2, flex - flex // 2
        cols = [(4, "TIPO", 12), (16, "MODELO", 11), (27, "TAREA", c_task), (27 + c_task, "AHORA", c_now)]
        for x, name, _ in cols:
            cv.put(x, y + 1, name, DIM)
        cv.put(w - 12, y + 1, "TIEMPO TOOLS", DIM)
        for i, v in enumerate(shown):
            row, chosen = y + 2 + i, v["id"] == sel
            icon, ist = agent_icon(v, phase, i)
            text, tst = agent_now(v)
            base = "bold" if chosen else FG if v["running"] else DIM
            if chosen:
                cv.put(0, row, "▸" + " " * (w - 1), f"bold {LEAD} on {CARD_BG}")
            bg = f" on {CARD_BG}" if chosen else ""
            cv.put(2, row, icon, ist + bg)
            cv.put(4, row, trunc(v["type"], 11), (f"bold {v['color']}" if v["running"] else base) + bg)
            cv.put(16, row, trunc(v["model"] or "—", 10), base + bg)
            cv.put(27, row, trunc(v["desc"], c_task - 1), base + bg)
            cv.put(27 + c_task, row, trunc(text, c_now - 1), tst + bg)
            cv.put(w - 12, row, f"{agent_time(v, now):>6} {v['tools']:>5}", base + bg)
        if not shown:
            cv.put(2, y + 2, empty, DIM)

    elif layout == "tarjetas":
        per_row = max(1, min(4, w // 34))
        cw = (w - (per_row - 1) * 2) // per_row
        shown = pick(agents, max(1, (h - y - 1) // 5) * per_row, sel)
        agents_header(cv, y, w, everyone, agents, shown, w)
        for i, v in enumerate(shown):
            x, top = (i % per_row) * (cw + 2), y + 1 + (i // per_row) * 5
            chosen = v["id"] == sel
            failed = bool(v["end"]) and v["status"] != "completed"
            edge = "bold #ffffff" if chosen else breath(v["color"], now) if v["running"] else ERR if failed else LINE
            cv.box(x, top, cw, 5, edge, heavy=chosen)
            icon, ist = agent_icon(v, phase, i)
            head = trunc(v["type"] + (f" · {v['model']}" if v["model"] else ""), cw - 9)
            segs(cv, x + 2, top, [(" ", ""), (icon, ist), (f" {head} ", f"bold {v['color']}" if v["running"] else "bold")])
            text, tst = agent_now(v)
            cv.put(x + 2, top + 1, trunc(v["desc"], cw - 4), FG if v["running"] or chosen else DIM)
            cv.put(x + 2, top + 2, trunc(text, cw - 4), tst)
            cv.put(x + 2, top + 3, trunc(f"{agent_time(v, now)} · {n_tools(v['tools'])}", cw - 4),
                   v["color"] if v["running"] else DIM)
        if not shown:
            cv.put(2, y + 2, empty, DIM)

    elif layout == "tiempo":
        lw = min(20, max(12, w // 5))
        bw = w - lw - 1
        shown = pick(agents, max(1, (h - y - 4) // 2), sel)
        t0, t1 = time_window(shown, now)
        time_axis(cv, lw, y, bw, t0, t1)
        cv.put(lw, y + 1, "".join("┊" if i % max(1, bw // 3) == 0 else " " for i in range(bw)), LINE)
        cv.put(0, y + 2, "lead", f"bold {LEAD}")
        cells = ["░"] * bw
        spans = list(s.turn_spans) + ([(s.turn_start, now)] if s.turn_start and store.state(s) in ("busy", "waiting") else [])
        for a, b in spans:
            if b < t0:
                continue
            lo = max(0, int((a - t0) / (t1 - t0) * bw))
            hi = min(bw, max(lo + 1, int((b - t0) / (t1 - t0) * bw)))
            cells[lo:hi] = ["█"] * (hi - lo)
        for i, c in enumerate(cells):
            cv.put(lw + i, y + 2, c, LEAD if c == "█" else LINE)
        for i, v in enumerate(shown):
            row, chosen = y + 4 + i * 2, v["id"] == sel
            icon, ist = agent_icon(v, phase, i)
            segs(cv, 0, row, [("▸ " if chosen else "  ", f"bold {LEAD}"), (icon + " ", ist),
                              (trunc(v["type"], lw - 5), f"bold {v['color']}" if v["running"] else "bold" if chosen else DIM)])
            time_bar(cv, lw, row, bw, t0, t1, v["start"], v["end"], agent_bar_style(v, now), v["running"])
            text, tst = agent_now(v)
            cv.put(lw, row + 1, trunc(f"{v['desc']} · {text} · {agent_time(v, now)}", bw),
                   FG if chosen else tst if v["running"] else DIM)
        note = empty if not shown else ""
        if len(agents) > len(shown):
            note = f"+{len(agents) - len(shown)} más · ←→"
        elif len(everyone) > len(agents):
            gone = len(everyone) - len(agents)
            note = (note + " · " if note else "") + f"{gone} oculto{'s' if gone != 1 else ''} · t"
        cv.put(2 if not shown else 0, h - 1 if shown else y + 4, trunc(note, w), DIM)

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
            base = "bold" if chosen else FG if v["running"] else DIM
            x = segs(cv, 0, row, [("▸ " if chosen else "  ", f"bold {LEAD}"), (icon + " ", ist),
                                  (f"{trunc(v['type'], 11):<12}", f"bold {v['color']}" if v["running"] else base),
                                  (f"{trunc(v['model'] or '—', 9):<10}", base)])
            cv.put(x, row, trunc(v["desc"], max(0, bx - x - 2)), base)
            time_bar(cv, bx, row, bw, t0, t1, v["start"], v["end"], agent_bar_style(v, now), v["running"])
            text, tst = agent_now(v)
            tail = f" · {n_tools(v['tools'])} · {agent_time(v, now)}"
            cv.put(x, row + 1, trunc(text, max(0, w - x - len(tail))), tst)
            cv.put(x + len(trunc(text, max(0, w - x - len(tail)))), row + 1, tail, DIM)
        if not shown:
            cv.put(2, y + 2, empty, DIM)
    if idle_rain == "force":
        rain(cv, y, now, h)
    elif idle_rain and not agents:
        rain(cv, y + 4, now, h)
    return cv.text()


# ── paneles ──────────────────────────────────────────────────────────────

def glitched(text, now):
    """El texto con parte de sus caracteres corrompidos: el parpadeo de un aviso."""
    rng = random.Random(int(now * 12))
    out = Text(justify=text.justify)
    for ch in text.plain:
        if ch != " " and rng.random() < 0.22:
            out.append(rng.choice("▓▒░#%&@$<>/\\|"), style=f"bold {rng.choice((ERR, WAIT, SWARM))}")
        else:
            out.append(ch, style=f"bold {FG}")
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
                cv.put(x, top + y, glyph, f"bold {FG}" if k == 0 else blend(TOOL, LINE, k / tail))


def header(store, sessions, account, now, glitch=False, god=False, focus=None):
    busy = sum(1 for s in sessions if store.state(s) == "busy")
    open_ = sum(1 for s in sessions if store.state(s) != "ended")
    title = Text(justify="center")
    sep = "  //  " if HUD else "  ·  "
    title.append("▚▚ CLAUDE CODE" if HUD else "CLAUDE CODE", style=f"bold {LEAD}" if HUD else "bold")
    if god:
        title.append("  ★ GOD MODE ★", style=f"bold {breath(WAIT, now * 2)}")
    if focus is not None:
        title.append(sep, style=DIM)
        title.append(f"◎ FOCO {focus['type'].upper()}", style=f"bold {breath(focus['color'], now)}")
    if store.multi:
        title.append(sep, style=DIM)
        title.append(f"CUENTA {account.upper()}" if account else f"{len(store.active)} CUENTAS",
                     style=f"bold {ADVISOR}")
    title.append(sep, style=DIM)
    title.append(f"{open_} SESIÓN ABIERTA" if open_ == 1 else f"{open_} SESIONES ABIERTAS", style=f"bold {LEAD}")
    title.append(sep, style=DIM)
    title.append(f"{busy} TRABAJANDO", style=f"bold {SWARM if busy else DIM}")
    waiting = sum(1 for s in sessions if store.state(s) == "waiting")
    if waiting:
        title.append(sep, style=DIM)
        title.append(f"◆ {waiting} ESPERA{'N' if waiting != 1 else ''} TU RESPUESTA", style=f"bold {breath(WAIT, now)}")
    jammed = sum(1 for s in sessions if store.stuck(s, now))
    if jammed:
        title.append(sep, style=DIM)
        title.append(f"⚠ {jammed} ATASCADA{'S' if jammed != 1 else ''}", style=f"bold {breath(ERR, now)}")
    title.append(sep, style=DIM)
    title.append(time.strftime("%H:%M:%S", time.localtime(now)), style=DIM)
    legend = Text(justify="center")
    for color, name in ((LEAD, "lead"), (SWARM, "subagentes"), (TOOL, "herramientas"), (ADVISOR, "advisor")):
        legend.append("■ ", style=color)
        legend.append(name + "     ", style=DIM)
    return Group(glitched(title, now) if glitch else title, Rule(style=LINE), legend)


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
            color = f"bold {breath(WAIT, now)}"
        elif why:
            dot, color = "⚠", f"bold {breath(ERR, now)}"
        mark = "▸" if s is focus else " "
        when = age(now - s.last) if s.last else "—"
        name = trunc(store.label(s), width - 6 - len(when))
        body.append(f"{mark} ", style=f"bold {LEAD}")
        body.append(f"{dot} ", style=color)
        body.append(name, style="bold reverse" if s is focus else (FG if state != "ended" else DIM))
        body.append(" " * max(1, width - 4 - len(name) - len(when)) + when + "\n", style=DIM)
        text = ("necesita " + store.waiting_for(s) if state == "waiting" else why[0] if why
                else s.title or s.cwd.replace(HOME, "~") or "—")
        body.append("    " + trunc(text, width - 4) + "\n",
                    style=WAIT if state == "waiting" else ERR if why else DIM)
    return Panel(body, title=label("sesiones"), title_align="left", box=PANEL_BOX,
                 border_style=LEAD if active else LINE)


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
    t.append(trunc(s.title or "sin título", width) + "\n", style=FG)
    for n, (key, value) in enumerate(rows):
        t.append(f"{key:<10}", style=DIM)
        t.append(value + ("\n" if n < len(rows) - 1 else ""), style=FG)
    panel = Panel(t, title=label("detalles", "DATA"), title_align="left", box=CARD_BOX, border_style=LEAD,
                  style=f"on {CARD_BG}")
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


def log_panel(store, focus, events, title, narrowed, rows, width, cursor=None, border=LINE):
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
            line.append("▸ " if picked else "  ", style=f"bold {LEAD}")
        line.append(time.strftime("%H:%M:%S", time.localtime(ts)) + " ", style=DIM)
        if col:
            name = trunc(store.label(s), col) if s else sid[:8]
            line.append(f"{name:<{col}} ", style=FG if s is focus else DIM)
        bad = THEME.get(style, style) == ERR
        line.append(f"{trunc(tag, 10):<10} ", style=f"bold {tint}" if tint and not bad else THEME.get(style, style))
        line.append(trunc(text, max(10, width - 25 - col - gutter)), style=FG if s is focus or picked else DIM)
        if picked:
            line.pad_right(max(0, width - 4 - len(line.plain)))
            line.stylize(f"bold on {CARD_BG}")
        body.append_text(line)
        if i < len(chunk) - 1:
            body.append("\n")
    if not events:
        body.append("sin eventos todavía", style=DIM)
    return Panel(body, title=trunc(title, max(10, width - 6)), title_align="left", box=PANEL_BOX,
                 border_style=border)


def event_panel(store, e, view, rows, width, now):
    """Recuadro de color con el detalle completo de la entrada del log bajo el cursor."""
    inside = view["pane"] == "out"
    frame = dict(box=CARD_BOX, border_style=LEAD if inside else SWARM, style=f"on {CARD_BG}",
                 title_align="left", subtitle_align="right")
    if e is None:
        return Panel(Text("el log está vacío", style=DIM), title="detalle", **frame)
    ts, sid, style, tag, text, _, ref = e
    s = store.sessions.get(sid)
    inner = max(20, width - 4)
    lines = []

    def section(name):
        if lines:
            lines.append(("", ""))
        lines.append((name.upper(), f"bold {SWARM}"))

    # `lines` empieza vacía: la primera sección o línea no lleva separación

    def add(body, st=FG):
        for raw in (body or "").expandtabs(4).splitlines() or [""]:
            for n, part in enumerate(textwrap.wrap(raw, inner - 2, replace_whitespace=False,
                                                   drop_whitespace=False) or [""]):
                lines.append(("  " + (part.lstrip() if n else part), st))

    kind = ref["k"] if ref else None
    status, sst = "", DIM
    if kind == "prompt":
        section("prompt")
        add(ref["text"])
    elif kind == "tool":
        if ref["err"]:
            status, sst = "✗ error", ERR
        elif ref["out"] is not None:
            status, sst = "✓ terminado", TOOL
        else:
            status, sst = "en curso", SWARM
        if ref["label"]:
            add(ref["label"], "bold")
        section("salida")
        if ref["out"] is None:
            add("todavía sin resultado" if s and store.state(s) == "busy" else "sin resultado registrado", DIM)
        else:
            add(ref["out"].strip() or "(sin salida)", ERR if ref["err"] else FG)
        section("entrada")
        for key, value in ref["input"].items():
            lines.append((f"  {key}", DIM))
            add(value)
    elif kind == "agent":
        v = next((v for v in store.agents(s) if v["id"] == ref["id"]), None) if s else None
        if v:
            failed = bool(v["end"]) and v["status"] != "completed"
            status, sst = ("en marcha", SWARM) if v["running"] else ("✗ " + v["status"], ERR) if failed else \
                          ("✓ terminado", TOOL) if v["end"] else ("interrumpido", DIM)
            section(f"subagente {v['type']}" + (f" · {v['model']}" if v["model"] else ""))
            add(v["desc"])
            section("prompt")
            add(v["prompt"] or "(prompt vacío)")
            section("respuesta")
            add(v["response"] or ("todavía sin respuesta" if v["running"] else "(sin respuesta registrada)"),
                FG if v["response"] else DIM)
    else:
        add(text)

    head = Text(no_wrap=True, overflow="ellipsis")
    head.append(time.strftime("%H:%M:%S", time.localtime(ts)), style="bold")
    head.append(f" · {store.label(s) if s else sid[:8]}", style=FG)
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


def short_path(path, cwd, n):
    """Ruta relativa a la carpeta de la sesión; si no cabe, se recorta por la izquierda."""
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        path = path[len(cwd.rstrip("/")) + 1:]
    path = path.replace(HOME, "~")
    return path if len(path) <= n else "…" + path[-(n - 1):]


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
             style=DIM)
    cap = max(12, min(40, width - 24))
    shown_names = [names(name, cap) if names else trunc(name, cap) for name, _ in items[:limit]]
    name_w = max([8] + [len(x) for x in shown_names])
    top = max((n for _, n in items), default=1)
    for shown, (name, n) in zip(shown_names, items[:limit]):
        if ctx is not None:
            ctx.setdefault("items", {})[t.plain.count("\n")] = name   # para abrir el archivo con Enter
        t.append(f"  {shown:<{name_w}}", style=FG)
        t.append(f" {n:>4}  ", style=color)
        t.append("▮" * max(1, round(8 * n / top)), style=blend(LINE, color, 0.7))
        t.append("\n")
    if len(items) > limit:
        t.append(f"  +{len(items) - limit} más · Enter despliega\n", style=DIM)
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
        lines[cur].stylize(f"on {LINE}")
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

    def row(label, value, style=FG):
        t.append(f"{label:<11}", style=DIM)
        t.append(value + "\n", style=style)

    failed = bool(v["end"]) and v["status"] != "completed"
    state = "en marcha" if v["running"] else ("✗ " + (v["status"] or "error")) if failed else \
        "✓ terminado" if v["end"] else "interrumpido"
    row("estado", state, v["color"] if v["running"] else ERR if failed else TOOL if v["end"] else DIM)
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
        listing("skills", side["skills"].most_common(), ADVISOR)
    edits, reads = side.get("edits") or Counter(), side.get("reads") or set()
    if edits:
        only_read = len(reads - set(edits))
        listing("archivos editados", edits.most_common(), LEAD, total=len(edits),
                    extra=f"{only_read} solo leído{'s' if only_read != 1 else ''}" if only_read else "",
                    names=lambda p, n: short_path(p, s.cwd, n))
    if reads - set(edits):
        listing("archivos leídos", [(p, 1) for p in sorted(reads - set(edits))], DIM,
                    names=lambda p, n: short_path(p, s.cwd, n))
    if side.get("mcp"):
        listing("mcp", side["mcp"].most_common(), FG)


def stats_panel(store, s, now, ui=None, rows=40, width=60):
    """Actividad de la sesión enfocada: lo que se muestra sin subagente seleccionado."""
    t = Text(no_wrap=True, overflow="ellipsis")
    if s is None:
        return Panel(t, title=label("actividad", "STAT"), title_align="left", box=PANEL_BOX, border_style=LINE)
    ctx = {"open": ui["open"] if ui is not None else set(), "marks": {}, "items": {}}
    if ui is not None:
        ui["dkind"], ui["dmarks"], ui["ditems"] = "list", ctx["marks"], ctx["items"]

    def row(label, value, style=FG):
        t.append(f"{label:<11}", style=DIM)
        t.append(value + "\n", style=style)

    def listing(*args, **kw):
        add_listing(t, width, *args, ctx=ctx, **kw)

    row("modelo", " · ".join(x for x in (short_model(s.model), s.effort and f"effort {s.effort}",
                                         s.perm and f"permisos {s.perm}") if x))
    g = lead_game(s)
    t.append(f"{'nivel':<11}", style=DIM)
    t.append(f"LV {g['lvl']} · {g['rounds']} rondas · {g['xp']}/10 para subir"
             + (f" · {EVO[g['tier']]}" if g["tier"] else "") + "\n", style=FG)
    if s.ctx:
        pct = min(100, round(100 * s.ctx / s.window()))
        fill = round(pct / 100 * 12)
        t.append(f"{'HP':<11}", style=DIM)
        t.append("█" * fill, style=TOOL if pct < 60 else LEAD if pct < 85 else ERR)
        t.append("░" * (12 - fill), style=LINE)
        left = s.turns_left()
        t.append(f" {100 - pct}% libre · {ktok(s.ctx)} de {ktok(s.window())}"
                 + (f" · ~{left} turnos para llenarse" if left is not None else "") + "\n", style=FG)
    why = store.stuck(s, now)
    if why:
        row("atasco", "⚠ " + " · ".join(why), ERR)
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
        t.append(f"{'tareas':<11}", style=DIM)
        t.append("▰" * fill, style=TOOL)
        t.append("▱" * (12 - fill), style=LINE)
        t.append(f" {done}/{total}" + (f" · ▶ {doing}" if doing else "") + "\n", style=FG)
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
        listing("tools", s.tool_counts.most_common(), TOOL, extra=f"{s.errors} con error" if s.errors else "")
    if s.skills:
        listing("skills", s.skills.most_common(), ADVISOR)
    if s.edits or s.reads:
        only_read = len(s.reads - set(s.edits))
        listing("archivos editados", s.edits.most_common(), LEAD, total=len(s.edits),
                extra=f"{only_read} solo leído{'s' if only_read != 1 else ''}" if only_read else "",
                names=lambda p, n: short_path(p, s.cwd, n))
    if s.mcp:
        listing("mcp", s.mcp.most_common(), FG)
    agents = store.agents(s)
    if agents:
        live = sum(1 for v in agents if v["running"])
        listing("subagentes", Counter(v["type"] for v in agents).most_common(), SWARM,
                extra=f"{live} en marcha" if live else "")
    if s.hooks:
        listing("hooks", s.hooks.most_common(), DIM)
    # el panel se desplaza con AvPág/RePág cuando las listas no caben
    body, pos = scrolled(t, ui, rows, cursor=ui is not None and ui["pane"] == "detalle", width=width)
    return Panel(body, title=label("actividad", "STAT"), title_align="left", box=PANEL_BOX, border_style=LINE,
                 subtitle=pos, subtitle_align="right")


def tasks_panel(store, s, ui, rows, width, now):
    """Las tareas de la sesión: lo que corre en segundo plano y, si la lleva, su lista de pendientes."""
    frame = dict(title_align="left", subtitle_align="right", box=PANEL_BOX, border_style=TOOL)
    if s is None:
        return Panel(Text("sin sesión", style=DIM), title=label("tareas", "TODO"), **frame)
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
            line.append(" " * max(1, inner - len(body) - len(right)) + right, style=DIM)
        lines.append(line)

    def section(title, summary):
        if lines:
            lines.append(Text(""))
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(title, style=f"bold {SWARM}")
        head.append(f"  {summary}", style=DIM)
        lines.append(head)

    def finished(mark_ok, text, fresh, right):
        """Fila de algo terminado: recién terminado lleva un barrido y brilla; después queda tachado."""
        if fresh < FRESH:
            body = trunc(text, inner - len(right) - 1)
            swept = len(body) if fresh >= 0.7 else int(fresh / 0.7 * len(body))
            glow = f"bold {breath(TOOL, now * 3)}"
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append(" ✦ " if int(now * 4) % 2 else f" {mark_ok} ", style=glow)
            line.append(body[:swept], style=f"bold {CARD_BG} on {TOOL}" if fresh < 0.7 else glow)
            line.append(body[swept:], style=FG)
            line.append(" " * max(1, inner - len(body) - len(right)) + right, style=DIM)
            lines.append(line)
        else:
            row(mark_ok, f"bold {TOOL}", text, f"{DIM} strike", right)

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
                row(SPIN[int(now * 9) % len(SPIN)], f"bold {SWARM}", text, f"bold {FG}", took)
            elif failed:
                row("✗", f"bold {ERR}", text, ERR, w["status"])
            elif end:
                finished("✓", text, fresh, took)
            else:
                row("■", DIM, text, DIM, "interrumpida")

    # la lista de pendientes que escribe el propio agente, si tiene esas herramientas
    tasks = list(s.todos.values())
    if tasks:
        done = sum(1 for t in tasks if t["status"] == "completed")
        fill = round(done / len(tasks) * 12)
        section("PENDIENTES", f"{done} de {len(tasks)} hechas")
        bar = Text(no_wrap=True)
        bar.append(" " + "▰" * fill, style=TOOL)
        bar.append("▱" * (12 - fill), style=LINE)
        lines.append(bar)
        for task in tasks:
            if task["status"] == "completed":
                finished("✓", task["subject"], now - task.get("flash", 0), "")
            elif task["status"] == "in_progress":
                follow = len(lines)
                row("▶", f"bold {LEAD}", task["active"] or task["subject"], f"bold {LEAD}")
            else:
                row("☐", FG, task["subject"], FG)
    if not lines:
        note = Text("esta sesión no tiene tareas: no ha lanzado subagentes ni comandos en segundo plano, "
                    "y no lleva una lista de pendientes", style=DIM)
        return Panel(note, title=label("tareas", "TODO"), **frame)
    if not tasks:
        lines.append(Text(""))
        lines.append(Text(" esta sesión no lleva lista de pendientes", style=DIM))

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
        t.append(" r respuesta ", style=DIM)
        t.append(" ")
        t.append(" p prompt ", style=DIM)
        t.append("\n\n")
        ctx = {"open": ui["open"], "marks": {}, "items": {}}
        ui["dkind"], ui["dmarks"], ui["ditems"] = "list", ctx["marks"], ctx["items"]
        agent_stats(t, s, v, now, width, ctx)
        body, pos = scrolled(t, ui, rows, cursor=ui["pane"] == "detalle", width=width)
        return Panel(body, title=trunc(f"◎ {head} · {v['desc']}", max(10, width - 6)), title_align="left",
                     subtitle=pos, subtitle_align="right", box=PANEL_BOX, border_style=v["color"])
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
        tabs.append(" i actividad ", style=DIM)
        tabs.append("  ")
    tabs.append(" r respuesta ", style="reverse bold" if not prompt else DIM)
    tabs.append(" ")
    tabs.append(" p prompt ", style="reverse bold" if prompt else DIM)
    state = "en marcha" if v["running"] else "terminado" if v["status"] == "completed" else v["status"] or "interrumpido"
    tabs.append(f"   {state}", style=SWARM if v["running"] else DIM)
    body = Text("\n".join(lines[top: top + body_rows]), style=FG, no_wrap=True, overflow="ellipsis")
    head = ("◎ " if focused else "") + head
    more = len(lines) - top - body_rows
    pos = f"{top + 1}–{min(len(lines), top + body_rows)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return Panel(Group(tabs, Text(""), body), title=trunc(f"{head} · {v['desc']}", max(10, width - 6)),
                 title_align="left", subtitle=pos if len(lines) > body_rows else None,
                 subtitle_align="right", box=PANEL_BOX, border_style=v["color"] if focused else SWARM)


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
            out.append((f"── {stamp} " + "─" * max(0, width - len(stamp) - 4), LINE))
            add(it["text"], f"bold {LEAD}", "› ", "  ")
            out.append(("", ""))
        elif it["k"] == "text":
            add(it["text"], FG, "  ", "  ")
            out.append(("", ""))
        else:
            out.append(("  " + trunc(f"▶ {it['name']}  {it['label']}", width - 2), TOOL))
            if it["res"] is None:
                continue
            style = ERR if it["err"] else DIM
            for n, line in enumerate(it["res"]):
                out.append((("    ⎿ " if n == 0 else "      ") + trunc(line, width - 6), style))
            if it["more"]:
                out.append((f"      … +{it['more']} líneas", DIM))
    _lines_cache.update(key=key, lines=out)
    return out


def session_panel(store, s, view, rows, width, now):
    """Conversación de la sesión, en el lugar del diagrama. view["top"] None = seguir en vivo."""
    if s is None:
        return Panel(Text("sin sesión", style=DIM), box=PANEL_BOX, border_style=LEAD)
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
        body.append("esta sesión todavía no tiene conversación", style=DIM)
    state = STATES[store.state(s)][1]
    title = f"sesión · {store.label(s)} · {state}" + (f" · {s.title}" if s.title else "")
    if view["top"] is None:
        pos = "siguiendo en vivo" if store.state(s) != "ended" else "final"
    else:
        pos = f"líneas {top + 1}–{min(len(lines), top + rows)} de {len(lines)} · Fin para seguir en vivo"
    return Panel(body, title=trunc(title, max(10, width - 6)), title_align="left", subtitle=pos,
                 subtitle_align="right", box=PANEL_BOX, border_style=LEAD if view["pane"] == "out" else LINE)


def footer(store, s, auto, account, now, sel=False, hint=None, flash=None, width=200):
    t = Text(no_wrap=True, overflow="ellipsis")
    if s is None:
        t.append("q salir", style=DIM)
        return t
    state = store.state(s)
    t.append((s.cwd or "?").replace(HOME, "~") + " $ ", style=f"bold {LEAD}")
    t.append("▌ " if int(now * 2) % 2 else "  ", style=FG)

    def field(label, value, color):
        t.append(f" {label} ", style=DIM)
        t.append(f"[{value}]", style=f"bold {color}")

    agents = store.agents(s)
    field("lead", state, state_color(state))
    field("subagents", f"{sum(1 for v in agents if v['running'])}/{len(agents)}", SWARM)
    if width >= 110:   # en terminales estrechas se prioriza la ayuda de teclas
        field("advisor", "on call" if s.advisor else "off", ADVISOR)
        field("tools", len(s.pending) if state != "ended" else 0, TOOL)
    if store.multi:
        field("cuenta", account or "todas", ADVISOR)
    if flash:
        t.append(f"   ◆ {flash}", style=f"bold {WAIT}")
        return t
    if hint:
        t.append(f"   {hint}", style=DIM)
        return t
    if sel:
        keys = "←→ subagente · r respuesta · p prompt · Esc soltar · ? ayuda"
    else:
        keys = "? ayuda · tab panel · , config · q salir"
    t.append(f"   {'auto' if auto else 'fija'} · {keys}", style=DIM)
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
            out.append(Text(title.upper(), style=f"bold {SWARM}"))
            for keys, what in rows:
                line = Text(no_wrap=True, overflow="ellipsis")
                line.append(f"{keys:<{key_w}}", style=f"bold {LEAD}")
                line.append(trunc(what, col_w - key_w), style=FG)
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
    for n, line in enumerate(chunk):
        body.append_text(line)
        body.append("\n")
    more = len(lines) > room
    body.append("\n" + ("j/k desplaza · " if more else "") + "cualquier otra tecla cierra", style=DIM)
    return Panel(body, title=label("teclas", "HELP"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {SWARM}", style=f"on {CARD_BG}", width=width,
                 subtitle=f"{top + 1}–{top + len(chunk)} de {len(lines)}" if more else None, subtitle_align="right")


# (clave, nombre, tipo, opciones o paso, valor por defecto, ayuda); None separa secciones
SETTINGS = [
    ("theme", "Tema", "choice", list(THEMES), "clasico", "colores y marco de todo el dashboard"),
    ("layout", "Diseño de agentes", "choice", list(LAYOUTS), "lista", "cómo se dibuja el panel del lead y sus subagentes"),
    ("toasts", "Toasts", "bool", None, True, "novedades como recuadros en la esquina; el registro (n) sigue igual"),
    ("alerts", "Pitido y aviso de tmux", "bool", None, True, "suena y avisa en tmux con las novedades importantes"),
    ("effects", "Efectos visuales", "bool", None, True, "arranque animado, glitch en los avisos y lluvia en reposo"),
    ("auto_update", "Actualizarse al arrancar", "bool", None, True, "busca cambios en el repo al abrir el dashboard"),
    ("viewer", "Visor de documentos", "viewer", None, "", "programa que abre los .md (tecla D); otro: cc-dash set viewer \"ORDEN\""),
    ("viewer_popup", "Visor en popup de tmux", "bool", None, True, "no: lanza el visor tal cual, para programas con ventana propia"),
    None,
    ("stuck_errors", "Errores seguidos", "int", 1, 3, "atasco: resultados con error seguidos (0 = no mirar)"),
    ("stuck_repeat", "Llamadas repetidas", "int", 1, 4, "atasco: veces seguidas la misma llamada (0 = no mirar)"),
    ("stuck_tool", "Herramienta sin volver", "int", 30, 300, "atasco: segundos que puede tardar una herramienta (0 = no mirar)"),
    ("stuck_silent", "Sin actividad", "int", 30, 180, "atasco: segundos trabajando sin escribir nada (0 = no mirar)"),
    None,
    ("hide_after", "Ocultar subagente tras", "int", 10, 60, "segundos que un subagente terminado sigue a la vista"),
    ("toast_seconds", "Duración de un toast", "int", 1, 8, "segundos que un toast sigue en pantalla"),
    ("toast_max", "Toasts a la vez", "int", 1, 4, "cuántos toasts caben apilados"),
]


def setting_entries(store):
    """Filas de la ventana: los ajustes fijos y, debajo, un interruptor por cuenta."""
    rows = list(SETTINGS) + [None, "Cuentas"]
    for a in store.accounts:
        where = a.path.replace(HOME, "~")
        rows.append((f"account:{a.name}", a.name, "account", None, True,
                     f"{where} · al deshabilitarla no se lee ni se muestra"))
    return rows


def setting_value(cfg, item):
    key = item[0]
    return key.split(":", 1)[1] not in cfg["disabled"] if item[2] == "account" else cfg[key]



def settings_panel(cfg, store, view, width, max_rows):
    """Ventana flotante de configuración: cada cambio se aplica y se guarda al momento."""
    entries = setting_entries(store)
    items = [e for e in entries if isinstance(e, tuple)]
    view["at"] %= len(items)
    chosen = items[view["at"]]
    # si no caben todas las filas, la ventana se desplaza para no perder la elegida
    room = max(5, max_rows - 6)
    at = entries.index(chosen)
    first = max(0, min(at - room // 2, len(entries) - room)) if len(entries) > room else 0
    body = Text(no_wrap=True, overflow="ellipsis")
    name_w = 26
    for item in entries[first: first + room]:
        if item is None:
            body.append("\n")
            continue
        if isinstance(item, str):
            body.append(f" {item.upper()}\n", style=f"bold {SWARM}")
            continue
        key, name, kind, _, default, _ = item
        picked = item is chosen
        value = setting_value(cfg, item)
        if kind == "account":
            shown = "activa" if value else "deshabilitada"
        elif kind == "bool":
            shown = "sí" if value else "no"
        elif kind == "viewer":
            shown = value or f"automático ({viewer_choices(cfg)[1] if len(viewer_choices(cfg)) > 1 else 'ninguno instalado'})"
        elif kind == "int" and value == 0 and key.startswith("stuck"):
            shown = "apagado"
        elif kind == "int" and key not in ("stuck_errors", "stuck_repeat", "toast_max"):
            shown = f"{value} s"
        else:
            shown = str(value)
        line = Text(no_wrap=True)
        line.append(" ▸ " if picked else "   ", style=f"bold {LEAD}")
        line.append(f"{trunc(name, name_w - 1):<{name_w}}", style="bold" if picked else FG)
        line.append("◂ " if picked else "  ", style=LEAD)
        color = (TOOL if value else ERR) if kind in ("bool", "account") else SWARM
        line.append(shown, style=f"bold {color}")
        line.append(" ▸" if picked else "", style=LEAD)
        if value != default:
            line.append("  ·  modificado", style=DIM)
        if picked:
            line.pad_right(max(0, width - 4 - len(line.plain)))
            line.stylize(f"on {LINE}")
        body.append_text(line)
        body.append("\n")
    body.append("\n " + trunc(chosen[5], width - 6) + "\n", style=DIM)
    body.append(" " + trunc("j/k mover · h/l cambiar · Enter alterna · r por defecto · Esc cerrar", width - 6), style=DIM)
    more = len(entries) > room
    return Panel(body, title=label("configuración", "CFG"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {LEAD}", style=f"on {CARD_BG}", width=width,
                 subtitle=f"fila {view['at'] + 1} de {len(items)} · se guarda al cambiar" if more
                 else "se guarda al cambiar", subtitle_align="right")


def viewer_choices(cfg):
    """Automático, los visores instalados y, si es otro, el que haya en la configuración."""
    found = [v for v in (*VIEWERS, "nvim -R", "vim -R") if shutil.which(v.split()[0])]
    return ["", *found, *([cfg["viewer"]] if cfg["viewer"] and cfg["viewer"] not in found else [])]


def change_setting(cfg, item, step):
    """Mueve un ajuste un paso (step=±1), o lo devuelve a su valor por defecto (step=0)."""
    key, _, kind, extra, default, _ = item
    if kind == "account":
        name = key.split(":", 1)[1]
        if name in cfg["disabled"]:
            cfg["disabled"].remove(name)
        elif step != 0:   # "por defecto" es activa
            cfg["disabled"].append(name)
    elif step == 0:
        cfg[key] = default
    elif kind == "bool":
        cfg[key] = not cfg[key]
    elif kind == "viewer":
        options = viewer_choices(cfg)
        cfg[key] = options[(options.index(cfg[key]) + step) % len(options)]
    elif kind == "choice":
        cfg[key] = extra[(extra.index(cfg[key]) + step) % len(extra)]
    else:
        cfg[key] = max(1 if key == "toast_max" else 0, cfg[key] + step * extra)


def apply_config(cfg, ui, store=None):
    """Lleva la configuración al dashboard en marcha y la guarda."""
    if store is not None:
        store.off = set(cfg["disabled"])
        store.t_glob = store.t_live = 0.0   # que la próxima pasada relea archivos y sesiones
    ui["theme"], ui["layout"] = cfg["theme"], cfg["layout"]
    ui["toasts_on"], ui["alerts"], ui["effects"] = cfg["toasts"], cfg["alerts"], cfg["effects"]
    if not ui["toasts_on"]:
        ui["toasts"].clear()
    apply_theme(ui["theme"])
    apply_limits(cfg)
    try:
        save_config(cfg)
    except OSError:
        pass


def toast_panel(toast, width, now):
    icon, title, role, _ = NEWS[toast["kind"]]
    color = THEME[role]
    left = max(0.0, 1 - (now - toast["born"]) / TOAST_LIFE)
    body = Text(no_wrap=True, overflow="ellipsis")
    if toast["who"]:
        body.append(trunc(toast["who"], width - 4) + "\n", style=DIM)
    wrapped = textwrap.wrap(toast["text"], width - 4)[:2] or [""]
    body.append("\n".join(wrapped) + "\n", style=f"bold {FG}")
    bar = round(left * (width - 4))
    body.append("▔" * bar, style=color)   # se va acortando: lo que le queda en pantalla
    return Panel(body, title=f"{icon} {title}", title_align="left", box=box.HEAVY,
                 border_style=f"bold {color}", style=f"on {CARD_BG}", width=width)


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
        icon, title, role, _ = NEWS[item["kind"]]
        body.append(time.strftime("%H:%M:%S", time.localtime(item["ts"])) + " ", style=DIM)
        body.append(f"{icon} ", style=f"bold {THEME[role]}")
        text = f"{item['who']} · {item['text']}" if item["who"] else item["text"]
        body.append(trunc(text, max(10, width - 16)) + ("\n" if n < len(chunk) - 1 else ""), style=FG)
    if not items:
        body.append("todavía no hay novedades", style=DIM)
    pos = f"{max(0, last - rows) + 1}–{last} de {len(items)}" if len(items) > rows else None
    return Panel(body, title=label("novedades", "NEWS"), title_align="left", subtitle=pos,
                 subtitle_align="right", box=PANEL_BOX, border_style=WAIT)


def mode_hint(ui):
    if ui["help"] is not None:
        return "teclas · cualquier tecla cierra"
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
                        border=LEAD if lg["pane"] == "list" else LINE)
    else:
        log = log_panel(store, focus, events, title, narrowed, rows, log_w,
                        border=SWARM if chosen is not None else LINE)
    # el panel con el foco del teclado (Tab) se marca con un borde de color
    ui["panes"] = (["sesiones"] if wide else []) + (["agentes"] if ui["session"] is None else []) + ["log", "detalle"]
    if ui["pane"] not in ui["panes"]:
        ui["pane"] = ui["panes"][0]
    hot = f"bold {LEAD}"
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
        diag = Panel(body, box=PANEL_BOX, border_style=f"bold {LEAD}" if ui["pane"] == "agentes" else LINE,
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
    ui["toasts"][:] = [t for t in ui["toasts"] if now - t["born"] < TOAST_LIFE]
    modal, modal_w = None, 0
    if ui["help"] is not None:
        modal_w = min(112, width - 2)
        modal = help_panel(ui["help"], modal_w, height - 4)
    elif ui["settings"] is not None:
        modal_w = min(66, width - 2)
        modal = settings_panel(ui["cfg"], store, ui["settings"], modal_w, height - 4)
    elif ui["entry"] is not None:   # detalle de una entrada del log, en ventana flotante
        modal_w = min(120, width - 4)
        modal = event_panel(store, ui["entry"]["e"], ui["entry"], max(8, height - 6), modal_w, now)
    if ui["toasts"] or modal is not None:
        tw = max(30, min(46, width // 2))
        root = Overlay(root, [toast_panel(t, tw, now) for t in ui["toasts"]], tw,
                       modal=modal, modal_width=modal_w)
    return root, sessions, focus


# ── bucle principal ──────────────────────────────────────────────────────

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


def notify(store, ui, sid, kind, text, now):
    """Una novedad: toast y registro siempre; las ruidosas además suenan y quedan en el log."""
    s = store.sessions.get(sid)
    who = store.label(s) if s else ""
    store.news.append({"ts": now, "sid": sid, "kind": kind, "who": who, "text": text})
    if ui["toasts_on"]:
        ui["toasts"].append({"born": now, "kind": kind, "who": who, "text": text})
        del ui["toasts"][:-TOAST_MAX]
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


def is_doc(path):
    return isinstance(path, str) and path.lower().endswith((".md", ".markdown", ".mdx"))


CODE_VIEWERS = ("bat --paging=always", "batcat --paging=always", "less -R", "more")   # para lo que no es .md


def change_source(path):
    """Contra qué comparar `path` en git. Devuelve (argumentos de git diff o None, nota).

    Cambios sin commitear; si es nuevo y git no lo sigue, todo es añadido ([]); si no hay
    cambios pendientes, el último commit que lo tocó. (None, motivo) si no se puede."""
    where = os.path.dirname(path) or "."

    def run(*args):
        try:
            return git(where, *args, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None

    top = run("rev-parse", "--show-toplevel")
    if not os.path.isdir(where) or top is None or top.returncode != 0:
        return None, "no está en un repositorio git"
    known = run("ls-files", "--error-unmatch", "--", path)
    if known is None or known.returncode != 0:
        if not os.path.isfile(path):
            return None, "ya no existe y git no lo conoce"
        return [], "archivo nuevo, sin seguimiento"
    pending = run("diff", "--quiet", "HEAD", "--", path)
    if pending is not None and pending.returncode == 1:
        return ["HEAD"], "cambios sin commitear"
    last = run("log", "-1", "--format=%H", "--", path)
    sha = last.stdout.strip() if last is not None else ""
    if not sha:
        return [], "sin historial"
    parent = run("rev-parse", "--verify", "--quiet", f"{sha}^")
    if parent is None or parent.returncode != 0:
        return [], "creado en el primer commit"
    return [f"{sha}^", sha], f"sin cambios pendientes: último commit {sha[:7]}"


def changed_lines(path, against):
    """Líneas añadidas y cambiadas (número en el archivo actual) y las borradas, por posición."""
    added, modified, removed = set(), set(), {}   # removed: línea antes de la que iban -> [texto]
    if not against:
        return added, modified, removed
    where = os.path.dirname(path) or "."
    try:
        out = git(where, "diff", "-U0", "--no-color", *against, "--", path, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return added, modified, removed
    gone, at = [], 0
    for line in out.splitlines():
        hunk = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk:
            if gone:
                removed.setdefault(at, []).extend(gone)
            old_n = int(hunk.group(1) if hunk.group(1) is not None else 1)
            start, new_n = int(hunk.group(2)), int(hunk.group(3) if hunk.group(3) is not None else 1)
            gone, at = [], start if new_n else start + 1
            for n in range(start, start + new_n):
                (modified if old_n else added).add(n)
        elif line.startswith("-") and not line.startswith("---"):
            gone.append(line[1:])
    if gone:
        removed.setdefault(at, []).extend(gone)
    return added, modified, removed


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
    head.append(f"  ·  {note}  ·  ", style=DIM)
    head.append(f"+{len(added)} ", style=f"bold {TOOL}")
    head.append(f"~{len(modified)} ", style=f"bold {WAIT}")
    head.append(f"-{sum(len(v) for v in removed.values())}", style=f"bold {ERR}")
    head.append("   n/N siguiente/anterior cambio · q cierra", style=DIM)
    out += [head, Text("")]
    num_w = len(str(len(lines))) + 1
    changed = added | modified
    starts = {n for n in changed if n - 1 not in changed} | set(removed)   # donde empieza cada bloque
    for n, text in enumerate(lines, 1):
        for k, ghost in enumerate(removed.get(n, [])):
            row = Text(no_wrap=True)
            row.append(f"{'':>{num_w}} ", style=DIM)
            row.append("◆" if k == 0 else "▌", style=f"bold {ERR}")
            row.append(" " + ghost, style=f"{ERR} on {red}")
            row.pad_right(max(0, width - len(row.plain)))
            row.stylize(f"on {red}", num_w + 2)
            out.append(row)
        row = Text(no_wrap=True)
        new, mod = n in added, n in modified
        row.append(f"{n:>{num_w}} ", style=FG if new or mod else LINE)
        mark = "◆" if (new or mod) and n in starts and not removed.get(n) else "▌" if new or mod else " "
        row.append(mark, style=f"bold {TOOL if new else WAIT}")
        row.append(" ")
        row.append_text(text)
        if new or mod:
            row.pad_right(max(0, width - len(row.plain)))
            row.stylize(f"on {green if new else amber}", num_w + 2)
        out.append(row)
    for n in sorted(k for k in removed if k > len(lines)):   # borrado al final del archivo
        for ghost in removed[n]:
            out.append(Text(f"{'':>{num_w}} ◆ {ghost}", style=f"{ERR} on {red}"))
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
    return f"{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))}"


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


def turn_docs(transcript, cwd):
    """Los .md que la sesión escribió desde el último mensaje del usuario, el más reciente al final."""
    docs = []
    try:
        with open(transcript, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - 8_000_000))
            if fh.tell():
                fh.readline()   # línea a medias
            for raw in fh:
                if b'"tool_use"' not in raw and b'"user"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if entry.get("isSidechain"):
                    continue
                content = (entry.get("message") or {}).get("content")
                if entry.get("type") == "user":
                    typed = isinstance(content, str) or any(
                        isinstance(b, dict) and b.get("type") == "text" for b in content or [])
                    if typed and not entry.get("isMeta"):
                        docs = []
                    continue
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Write", "Edit", "MultiEdit"):
                        path = (b.get("input") or {}).get("file_path")
                        if is_doc(path):
                            path = os.path.join(cwd or "", path)
                            docs = [p for p in docs if p != path] + [path]
    except OSError:
        pass
    return [p for p in docs if os.path.isfile(p)]


def run_hook(cfg):
    """`cc-dash hook`: Claude Code lo llama al preguntar o al terminar un turno; abre el documento a revisar."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    docs = turn_docs(event.get("transcript_path") or "", event.get("cwd") or "")
    wanted = re.compile(cfg["spec_match"] or SPEC_MATCH, re.I)
    specs = [p for p in docs if wanted.search(p)]
    # al terminar el turno solo se abren las rutas que parecen un spec; ante una pregunta, cualquier .md
    doc = (specs or (docs if event.get("hook_event_name") != "Stop" else []))[-1:]
    if not doc:
        return
    doc = doc[0]
    seen_path = os.path.join(data_dir(), "shown.json")
    try:
        with open(seen_path) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    stamp = os.path.getmtime(doc)
    if seen.get(doc) == stamp:   # esa versión ya se mostró
        return
    why = open_doc(cfg, doc, only_visible=True)
    if why:
        note = f"cc-dash: documento a revisar: {doc}" + ("" if why in ("tty", "hidden") else f" ({why})")
        print(json.dumps({"systemMessage": note}, ensure_ascii=False))
    seen.pop(doc, None)
    seen[doc] = stamp
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(seen_path, "w") as fh:
            json.dump(dict(list(seen.items())[-100:]), fh)
    except OSError:
        pass


def hook_command():
    exe = os.path.abspath(sys.argv[0])
    if os.path.basename(exe) != "cc-dash":
        exe = shutil.which("cc-dash") or f"{sys.executable} {shlex.quote(os.path.abspath(__file__))}"
    return f"{exe} hook"


def is_ours(group):
    return any(re.search(r"cc[-_]dash(\.py)?['\"]? hook$", h.get("command", "")) for h in group.get("hooks", []))


def manage_hooks(accounts, action):
    """Instala, quita o muestra el hook de cc-dash en el settings.json de cada cuenta."""
    ours = {"type": "command", "command": hook_command(), "timeout": 10}
    wanted = {"PreToolUse": {"matcher": "AskUserQuestion", "hooks": [ours]}, "Stop": {"hooks": [ours]}}
    for a in accounts:
        path = os.path.realpath(os.path.join(a.path, "settings.json"))
        try:
            with open(path) as fh:
                settings = json.load(fh)
        except FileNotFoundError:
            settings = {}
        except (OSError, ValueError) as exc:
            print(f"{a.name}: no se pudo leer {path}: {exc}")
            continue
        hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
        has = all(any(is_ours(g) for g in hooks.get(event, [])) for event in wanted)
        if action == "status":
            print(f"{a.name}: {'instalado' if has else 'sin instalar'} · {path}")
            continue
        for event, group in wanted.items():
            kept = [g for g in hooks.get(event, []) if not is_ours(g)]
            if action == "install":
                kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if hooks:
            settings["hooks"] = hooks
        else:
            settings.pop("hooks", None)
        try:
            with open(path + ".cc-dash.tmp", "w") as fh:
                json.dump(settings, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(path + ".cc-dash.tmp", path)
        except OSError as exc:
            print(f"{a.name}: no se pudo escribir {path}: {exc}")
            continue
        print(f"{a.name}: hook {'instalado' if action == 'install' else 'quitado'} · {path}")


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


def boot(console, store):
    """Secuencia de arranque de un segundo, con los datos reales de lo que se cargó."""
    live = [s for s in store.sessions.values() if s.sid in store.live]
    steps = [
        (f"▚▚ CC-DASH v{__version__} // INIT", f"bold {LEAD}"),
        (f"   cuentas ........ {len(store.active)}" + (f" (+{len(store.off)} deshabilitadas)" if store.off else ""), FG),
        (f"   transcripts .... {len(store.files)}", FG),
        (f"   sesiones ....... {len(store.sessions)} · {len(live)} enlazadas", FG),
        (f"   eventos ........ {len(store.log)}", FG),
        ("   ENLACE ESTABLECIDO", f"bold {TOOL}"),
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


def cmd_accounts(accounts, disabled=()):
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {LEAD}")
    for col in ("cuenta", "estado", "ruta", "email", "abiertas", "transcripts"):
        table.add_column(col)
    for a in accounts:
        ok = os.path.isdir(a.path)
        open_ = len(glob.glob(os.path.join(a.live_dir, "*.json")))
        total = len(glob.glob(os.path.join(a.projects, "*", "*.jsonl")))
        table.add_row(a.name, "deshabilitada" if a.name in disabled else "activa",
                      a.path.replace(HOME, "~") + ("" if ok else "  (no existe)"),
                      a.email() or "—", str(open_), str(total))
    console = Console()
    console.print(table)
    console.print(f"config: {config_path()}", style=DIM)


def main():
    ap = argparse.ArgumentParser(prog="cc-dash", description="Dashboard en vivo de sesiones de Claude Code.")
    ap.add_argument("--version", action="version", version=f"cc-dash {__version__}")
    ap.add_argument("--once", action="store_true", help="imprime un solo fotograma y sale")
    ap.add_argument("--no-update", action="store_true", help="no busca actualizaciones al arrancar")
    ap.add_argument("--layout", choices=LAYOUTS, help="diseño del panel de agentes para esta ejecución")
    ap.add_argument("--theme", choices=list(THEMES), help="tema de colores para esta ejecución")
    ap.add_argument("-a", "--account", action="append", metavar="CUENTA",
                    help="muestra solo esta cuenta (repetible)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("accounts", help="lista las cuentas configuradas y detectadas")
    for verb, text in (("disable", "deja de leer y mostrar una cuenta"), ("enable", "vuelve a mostrar una cuenta")):
        p = sub.add_parser(verb, help=text)
        p.add_argument("name")
    p = sub.add_parser("add", help="registra una cuenta")
    p.add_argument("name")
    p.add_argument("path", help="directorio de configuración de Claude Code (su CLAUDE_CONFIG_DIR)")
    p = sub.add_parser("remove", help="quita una cuenta registrada")
    p.add_argument("name")
    p = sub.add_parser("scan", help="registra una carpeta cuyas subcarpetas son cuentas")
    p.add_argument("dir")
    sub.add_parser("config", help="muestra la ruta del archivo de configuración")
    sub.add_parser("update", help="trae los cambios del repo y reinstala")
    p = sub.add_parser("view", help="abre un archivo con el visor configurado")
    p.add_argument("file")
    p = sub.add_parser("changes", help="muestra un archivo con colores y lo que cambió según git resaltado")
    p.add_argument("file")
    p = sub.add_parser("hook", help="hook de Claude Code que abre el documento a revisar (install, remove, status)")
    p.add_argument("action", nargs="?", choices=("install", "remove", "status"))
    p = sub.add_parser("set", help="cambia un umbral u opción; sin argumentos los lista")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "config":
        print(config_path())
        return
    if args.cmd == "set":
        if args.key is None:
            for key, (_, default, what) in LIMITS.items():
                print(f"{key:<14} {cfg[key]:<6} {what} (por defecto {default})")
            for key, what in SWITCHES.items():
                print(f"{key:<14} {'true' if cfg[key] else 'false':<6} {what}")
            for key, (_, what) in TEXTS.items():
                print(f"{key:<14} {json.dumps(cfg[key], ensure_ascii=False)}  {what}")
            return
        if args.value is None:
            sys.exit("cc-dash: falta el valor; uso: cc-dash set CLAVE VALOR")
        if args.key in TEXTS:
            cfg[args.key] = args.value.strip()
            if args.key == "spec_match":
                try:
                    re.compile(cfg[args.key])
                except re.error as exc:
                    sys.exit(f"cc-dash: spec_match no es una expresión regular válida: {exc}")
                cfg[args.key] = cfg[args.key] or SPEC_MATCH
        elif args.key in LIMITS:
            if not args.value.isdigit():
                sys.exit(f"cc-dash: {args.key} necesita un número entero mayor o igual que 0")
            cfg[args.key] = max(1, int(args.value)) if args.key == "toast_max" else int(args.value)
        elif args.key in SWITCHES:
            if args.value.lower() not in ("true", "false", "on", "off", "si", "sí", "no"):
                sys.exit(f"cc-dash: {args.key} necesita true o false")
            cfg[args.key] = args.value.lower() in ("true", "on", "si", "sí")
        else:
            sys.exit(f"cc-dash: clave desconocida '{args.key}'; `cc-dash set` lista las disponibles")
        save_config(cfg)
        print(f"{args.key} = {str(cfg[args.key]).lower() if isinstance(cfg[args.key], bool) else cfg[args.key]}")
        return
    if args.cmd == "hook" and args.action is None:
        run_hook(cfg)
        return
    if args.cmd == "changes":
        show_changes(os.path.abspath(os.path.expanduser(args.file)))
        return
    if args.cmd == "view":
        path = os.path.abspath(os.path.expanduser(args.file))
        if not os.path.isfile(path):
            sys.exit(f"cc-dash: {path} no es un archivo")
        why = open_doc(cfg, path)
        if why == "tty":
            sys.exit(subprocess.run(viewer_command(cfg, path), shell=True).returncode)
        if why:
            sys.exit(f"cc-dash: {why}")
        return
    if args.cmd == "update":
        update()
        return
    if args.cmd == "add":
        path = os.path.abspath(os.path.expanduser(args.path))
        if not os.path.isdir(path):
            sys.exit(f"cc-dash: {path} no es un directorio")
        cfg["account"] = [a for a in cfg["account"] if a["name"] != args.name]
        cfg["account"].append({"name": args.name, "path": path})
        save_config(cfg)
        print(f"cuenta '{args.name}' → {path}")
        return
    if args.cmd == "remove":
        kept = [a for a in cfg["account"] if a["name"] != args.name]
        if len(kept) == len(cfg["account"]):
            sys.exit(f"cc-dash: '{args.name}' no está en {config_path()}")
        cfg["account"] = kept
        save_config(cfg)
        print(f"cuenta '{args.name}' quitada")
        return
    if args.cmd == "scan":
        path = os.path.abspath(os.path.expanduser(args.dir))
        if not os.path.isdir(path):
            sys.exit(f"cc-dash: {path} no es un directorio")
        if path not in cfg["scan"]:
            cfg["scan"].append(path)
            save_config(cfg)
        print(f"escaneando cuentas en {path}")
        return

    accounts = resolve_accounts(cfg)
    if args.cmd == "accounts":
        cmd_accounts(accounts, cfg["disabled"])
        return
    if args.cmd == "hook":
        chosen = [a for a in accounts if not args.account or a.name in args.account]
        manage_hooks(chosen, args.action)
        return
    if args.cmd in ("disable", "enable"):
        if args.name not in {a.name for a in accounts}:
            sys.exit(f"cc-dash: cuenta desconocida: {args.name} (hay: {', '.join(a.name for a in accounts) or 'ninguna'})")
        cfg["disabled"] = [n for n in cfg["disabled"] if n != args.name]
        if args.cmd == "disable":
            cfg["disabled"].append(args.name)
        save_config(cfg)
        print(f"cuenta '{args.name}' {'deshabilitada' if args.cmd == 'disable' else 'activa'}")
        return
    if args.account:
        known = {a.name for a in accounts}
        missing = [n for n in args.account if n not in known]
        if missing:
            sys.exit(f"cc-dash: cuenta desconocida: {', '.join(missing)} (hay: {', '.join(sorted(known)) or 'ninguna'})")
        accounts = [a for a in accounts if a.name in args.account]
    if not accounts:
        sys.exit("cc-dash: no hay cuentas; registra una con `cc-dash add NOMBRE RUTA`")
    if cfg["auto_update"] and not args.no_update and not args.once and sys.stdout.isatty():
        auto_update()
    if args.layout:
        cfg["layout"] = args.layout
    if args.theme:
        cfg["theme"] = args.theme
    run(accounts, args.once, cfg)


if __name__ == "__main__":
    main()
