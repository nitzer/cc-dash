#!/usr/bin/env python3
"""cc-dash — dashboard en vivo de las sesiones de Claude Code.

Por cada cuenta (un directorio de configuración de Claude Code) lee
sessions/*.json (sesiones abiertas) y projects/**/*.jsonl (modelo,
herramientas, subagentes, tokens). Solo lectura: no toca ni envía nada.

Teclas: ↑/↓ o j/k cambia de sesión · c filtra por cuenta · a modo auto · q sale
Uso:    cc-dash [--once] [-a CUENTA]
        cc-dash accounts | add NOMBRE RUTA | remove NOMBRE | scan DIR | config
"""
import argparse
import glob
import json
import os
import sys
import time
import tomllib
from datetime import datetime

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

try:
    import select
    import termios
    import tty
except ImportError:  # Windows
    termios = None

__version__ = "0.2.0"

HOME = os.path.expanduser("~")
DEFAULT_DIR = os.path.join(HOME, ".claude")

LEAD = "#e8915a"
SWARM = "#6fb1ff"
TOOL = "#6fcf97"
ADVISOR = "#b48efc"
ERR = "#ef6b73"
DIM = "#5c6680"
LINE = "#3b4560"
FG = "#c9d1e4"

AGENT_TOOLS = ("Agent", "Task")
SLOTS = 3
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


def tool_label(name, inp):
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return inp.get("description") or inp.get("command") or ""
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


def load_config():
    cfg = {"account": [], "scan": [], "include_default": True}
    try:
        with open(config_path(), "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"cc-dash: no se pudo leer {config_path()}: {exc}")
    cfg["account"] = [a for a in raw.get("account", []) if a.get("name") and a.get("path")]
    cfg["scan"] = [d for d in raw.get("scan", []) if isinstance(d, str)]
    cfg["include_default"] = bool(raw.get("include_default", True))
    return cfg


def save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = [
        "# cc-dash — cuentas de Claude Code a mostrar.",
        "# include_default: añade ~/.claude (o $CLAUDE_CONFIG_DIR) si no está listada.",
        "# scan: carpetas cuyas subcarpetas son cada una una cuenta.",
        f"include_default = {'true' if cfg['include_default'] else 'false'}",
        "scan = [" + ", ".join(json.dumps(d) for d in cfg["scan"]) + "]",
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


# ── estado ───────────────────────────────────────────────────────────────

class Session:
    def __init__(self, sid, account):
        self.sid = sid
        self.account = account
        self.title = ""
        self.cwd = ""
        self.model = ""
        self.advisor = ""
        self.effort = ""
        self.out_tokens = 0
        self.ctx = 0
        self.cost = None
        self.turns = 0
        self.tools = 0
        self.advisor_calls = 0
        self.advisor_last = 0.0
        self.last = 0.0
        self.pending = {}   # tool_use_id -> (name, label, ts)
        self.agents = []    # {id, type, desc, model, start, end}
        self.requests = set()

    def running_agents(self):
        return [a for a in self.agents if a["end"] is None]


class Store:
    def __init__(self, accounts):
        self.accounts = accounts
        self.multi = len(accounts) > 1
        self.sessions = {}
        self.log = []       # (ts, sid, style, tag, text)
        self.offsets = {}
        self.files = []     # (ruta, nombre de cuenta)
        self.live = {}      # sid -> info de ~/.claude/sessions
        self.t_glob = self.t_live = self.t_tail = 0.0

    # -- lectura incremental de transcripts --
    def poll(self, now):
        if now - self.t_glob > 3:
            self.files = [(path, acct.name) for acct in self.accounts
                          for path in glob.glob(os.path.join(acct.projects, "**", "*.jsonl"), recursive=True)]
            self.t_glob = now
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
                del self.log[:-400]

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
        for acct in self.accounts:
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

    def event(self, ts, sid, style, tag, text):
        self.log.append((ts, sid, style, tag, text))

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
        if o.get("cwd"):
            s.cwd = o["cwd"]
        side = bool(o.get("isSidechain"))
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = msg.get("content")

        if kind == "ai-title":
            s.title = o.get("aiTitle") or s.title
        elif kind == "cost-state":
            s.cost = o.get("totalCostUSD")
        elif kind == "system" and o.get("subtype") == "turn_duration" and not side:
            s.turns += 1
            s.pending.clear()
        elif kind == "assistant":
            if not side:
                if msg.get("model") and msg["model"] != "<synthetic>":
                    s.model = msg["model"]
                s.advisor = o.get("advisorModel") or s.advisor
                s.effort = o.get("effort") or s.effort
            usage = msg.get("usage") or {}
            rid = o.get("requestId")
            if usage and rid not in s.requests:
                s.requests.add(rid)
                s.out_tokens += usage.get("output_tokens") or 0
            if usage and not side:
                s.ctx = ((usage.get("input_tokens") or 0)
                         + (usage.get("cache_read_input_tokens") or 0)
                         + (usage.get("cache_creation_input_tokens") or 0))
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                name = b.get("name") or ""
                inp = b.get("input") or {}
                if b.get("type") == "tool_use":
                    s.tools += 1
                    label = tool_label(name, inp)
                    if name in AGENT_TOOLS and not side:
                        s.agents.append({
                            "id": b.get("id"), "start": ts, "end": None,
                            "type": inp.get("subagent_type") or "general",
                            "desc": inp.get("description") or "",
                            "model": inp.get("model") or "",
                        })
                        self.event(ts, sid, SWARM, "Agent", f"▶ {label}")
                    else:
                        s.pending[b.get("id")] = (name, label, ts)
                        self.event(ts, sid, DIM if side else TOOL,
                                   ("↳ " if side else "") + name, label)
                elif "advisor" in name.lower() or "advisor" in str(b.get("type")):
                    if b.get("type", "").endswith("tool_use"):
                        s.advisor_calls += 1
                        s.advisor_last = ts
                        self.event(ts, sid, ADVISOR, "advisor", "consulta al advisor")
        elif kind == "user":
            if isinstance(content, str):
                if not side and not o.get("isMeta") and not content.lstrip().startswith("<"):
                    s.pending.clear()
                    self.event(ts, sid, LEAD, "prompt", "› " + content)
                return
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                done = s.pending.pop(tid, None)
                if done and b.get("is_error"):
                    self.event(ts, sid, ERR, done[0], "✗ " + (done[1] or "error"))
                for a in s.agents:
                    if a["id"] == tid and a["end"] is None:
                        a["end"] = ts or time.time()
                        self.event(ts, sid, SWARM, "Agent",
                                   f"✓ {a['desc']} ({clock(a['end'] - a['start'])})")

    # -- vistas --
    def state(self, s):
        info = self.live.get(s.sid)
        if not info:
            return "ended"
        return "busy" if info.get("status") == "busy" else "idle"

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
        pool = [s for s in self.sessions.values() if account in (None, s.account)]
        live = [s for s in pool if s.sid in self.live]
        live.sort(key=lambda s: (s.account, self.live[s.sid].get("startedAt") or 0))
        rest = [s for s in pool if s.sid not in self.live]
        rest.sort(key=lambda s: -s.last)
        return live + rest

    def auto_focus(self, sessions):
        if not sessions:
            return None
        rank = {"busy": 2, "idle": 1, "ended": 0}
        return max(sessions, key=lambda s: (rank[self.state(s)], s.last))


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

    def box(self, x, y, w, h, style):
        self.put(x, y, "┌" + "─" * (w - 2) + "┐", style)
        for r in range(1, h - 1):
            self.put(x, y + r, "│", style)
            self.put(x + w - 1, y + r, "│", style)
        self.put(x, y + h - 1, "└" + "─" * (w - 2) + "┘", style)

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


def hpath(x0, x1, y):
    step = 1 if x1 >= x0 else -1
    return [(x, y) for x in range(x0, x1 + step, step)]


def diagram(store, s, w, compact, now):
    h = 14 if compact else 20
    cv = Canvas(w, h)
    if s is None:
        cv.put(2, 2, "sin sesiones todavía — abre Claude Code en otra terminal", DIM)
        return cv.text()
    phase = int(now * 9)
    state = store.state(s)
    busy = state == "busy"

    # advisor
    aw = 22 if w >= 72 else 0
    if aw:
        hot = now - s.advisor_last < 12
        ast = ADVISOR if s.advisor else LINE
        cv.box(0, 0, aw, 5, ast if hot else LINE)
        cv.put(2, 1, "■ advisor", f"bold {ast}")
        cv.put(2, 2, trunc(short_model(s.advisor) if s.advisor else "sin advisor", aw - 4), FG if s.advisor else DIM)
        cv.put(2, 3, trunc(f"{s.advisor_calls} consultas" if s.advisor_calls else
                           ("on call" if s.advisor else "—"), aw - 4), DIM)
        gap = 6
        cv.put(aw, 2, "┄" * (gap - 1) + "▶", ast if s.advisor else LINE)
        if hot:
            cv.put(aw + phase % (gap - 1), 2, "●", f"bold {ADVISOR}")
        lx = aw + gap
    else:
        lx = 0

    # lead
    lw = w - lx
    lst = LEAD if state != "ended" else LINE
    cv.box(lx, 0, lw, 5, lst)
    inner = lw - 4
    spin = SPIN[phase % len(SPIN)] if busy else "■"
    cv.put(lx + 2, 1, trunc(f"{spin} {short_model(s.model)} · lead", inner), f"bold {lst}")
    label = {"busy": "trabajando", "idle": "esperando prompt", "ended": "cerrada"}[state]
    meta = f"{store.label(s)} · {label}" + (f" · effort {s.effort}" if s.effort else "")
    cv.put(lx + 2, 2, trunc(meta, inner), FG)
    plain = [p for p in s.pending.values()]
    if plain and state != "ended":
        name, lab, t0 = max(plain, key=lambda p: p[2])
        more = f" (+{len(plain) - 1})" if len(plain) > 1 else ""
        cv.put(lx + 2, 3, trunc(f"▶ {name}{more}: {lab}", inner), TOOL)
    else:
        cv.put(lx + 2, 3, trunc(s.title or "—", inner), DIM)

    # subagentes
    agents = sorted(s.agents, key=lambda a: (a["end"] is not None, -(a["start"] or 0)))
    running = s.running_agents() if state != "ended" else []
    shown = agents[:SLOTS]
    sgap = 2
    sw = (w - sgap * (SLOTS - 1)) // SLOTS
    xs = [i * (sw + sgap) + sw // 2 for i in range(SLOTS)]
    trunk = lx + lw // 2
    btrunk = w // 2
    cv.put(trunk, 5, "│", LINE)
    cv.branch(6, xs, trunk, LINE, down=True)
    if len(running) > SLOTS:
        cv.put(max(0, w - 10), 5, f"+{len(running) - SLOTS} más", SWARM)
    if not compact:
        cv.branch(14, xs, btrunk, LINE, down=False)
        cv.put(btrunk, 15, "▼", LINE)
    for i in range(SLOTS):
        x = i * (sw + sgap)
        a = shown[i] if i < len(shown) else None
        live = a is not None and a["end"] is None and state != "ended"
        st = SWARM if live else LINE
        cv.put(xs[i], 7, "▼", st)
        cv.box(x, 8, sw, 5, st)
        if not compact:
            cv.put(xs[i], 13, "│", LINE)
        if a is None:
            cv.put(x + 2, 10, trunc("· libre ·", sw - 4), DIM)
            continue
        head = a["type"] + (f" · {a['model']}" if a["model"] else "")
        cv.put(x + 2, 9, trunc(head, sw - 4), f"bold {SWARM}" if live else DIM)
        cv.put(x + 2, 10, trunc(a["desc"], sw - 4), FG if live else DIM)
        if live:
            cv.put(x + 2, 11, trunc(f"● {clock(now - a['start'])}", sw - 4), SWARM)
            path = [(trunk, 5)] + hpath(trunk, xs[i], 6) + [(xs[i], 7)]
            px, py = path[(phase + i * 3) % len(path)]
            cv.put(px, py, "●", f"bold {SWARM}")
        else:
            end = a["end"] or now
            status = "✓ " + clock(end - a["start"]) if a["end"] else "interrumpido"
            cv.put(x + 2, 11, trunc(status, sw - 4), DIM)
            if not compact and a["end"] and now - a["end"] < 8:
                path = [(xs[i], 13)] + hpath(xs[i], btrunk, 14) + [(btrunk, 15)]
                px, py = path[phase % len(path)]
                cv.put(px, py, "●", f"bold {LEAD}")

    # vuelta al lead
    if not compact:
        bw = min(56, w)
        bx = btrunk - bw // 2
        cv.box(bx, 16, bw, 4, lst)
        done = sum(1 for a in s.agents if a["end"])
        cv.put(bx + 2, 17, trunc(f"de vuelta al lead · {done} subagentes terminados", bw - 4), lst)
        stats = f"{s.turns} turnos · {s.tools} tools · out {ktok(s.out_tokens)} · ctx {ktok(s.ctx)}"
        if s.cost is not None:
            stats += f" · ${s.cost:.2f}"
        cv.put(bx + 2, 18, trunc(stats, bw - 4), DIM)
    return cv.text()


# ── paneles ──────────────────────────────────────────────────────────────

def header(store, sessions, account, now):
    busy = sum(1 for s in sessions if store.state(s) == "busy")
    open_ = sum(1 for s in sessions if store.state(s) != "ended")
    title = Text(justify="center")
    title.append("CLAUDE CODE", style="bold")
    if store.multi:
        title.append("  ·  ", style=DIM)
        title.append(f"CUENTA {account.upper()}" if account else f"{len(store.accounts)} CUENTAS",
                     style=f"bold {ADVISOR}")
    title.append("  ·  ", style=DIM)
    title.append(f"{open_} SESIÓN ABIERTA" if open_ == 1 else f"{open_} SESIONES ABIERTAS", style=f"bold {LEAD}")
    title.append("  ·  ", style=DIM)
    title.append(f"{busy} TRABAJANDO", style=f"bold {SWARM if busy else DIM}")
    title.append("  ·  ", style=DIM)
    title.append(time.strftime("%H:%M:%S", time.localtime(now)), style=DIM)
    legend = Text(justify="center")
    for color, name in ((LEAD, "lead"), (SWARM, "subagentes"), (TOOL, "herramientas"), (ADVISOR, "advisor")):
        legend.append("■ ", style=color)
        legend.append(name + "     ", style=DIM)
    return Group(title, Rule(style=LINE), legend)


def side_panel(store, sessions, focus, rows, now):
    body = Text()
    width = 30
    for s in sessions[: max(1, rows // 2)]:
        state = store.state(s)
        dot, color = {"busy": ("●", LEAD), "idle": ("○", TOOL), "ended": ("·", DIM)}[state]
        mark = "▸" if s is focus else " "
        when = age(now - s.last) if s.last else "—"
        name = trunc(store.label(s), width - 6 - len(when))
        body.append(f"{mark} ", style=f"bold {LEAD}")
        body.append(f"{dot} ", style=color)
        body.append(name, style="bold" if s is focus else (FG if state != "ended" else DIM))
        body.append(" " * max(1, width - 4 - len(name) - len(when)) + when + "\n", style=DIM)
        body.append("    " + trunc(s.title or s.cwd.replace(HOME, "~") or "—", width - 4) + "\n", style=DIM)
    return Panel(body, title="sesiones", title_align="left", box=box.SQUARE, border_style=LINE)


def log_panel(store, focus, account, rows, width, now):
    body = Text(no_wrap=True, overflow="ellipsis")
    col = 22 if store.multi else 14
    events = [e for e in store.log if account in (None, getattr(store.sessions.get(e[1]), "account", None))]
    events = events[-rows:] if rows > 0 else []
    for i, (ts, sid, style, tag, text) in enumerate(events):
        s = store.sessions.get(sid)
        name = trunc(store.label(s), col) if s else sid[:8]
        body.append(time.strftime("%H:%M:%S", time.localtime(ts)) + " ", style=DIM)
        body.append(f"{name:<{col}} ", style=FG if s is focus else DIM)
        body.append(f"{trunc(tag, 10):<10} ", style=style)
        body.append(trunc(text, max(10, width - 26 - col)), style=FG if s is focus else DIM)
        if i < len(events) - 1:
            body.append("\n")
    return Panel(body, title="session log", title_align="left", box=box.SQUARE, border_style=LINE)


def footer(store, s, auto, account, now):
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

    field("lead", state, LEAD if state == "busy" else TOOL if state == "idle" else DIM)
    field("subagents", f"{len(s.running_agents()) if state != 'ended' else 0}/{SLOTS}", SWARM)
    field("advisor", "on call" if s.advisor else "off", ADVISOR)
    field("tools", len(s.pending) if state != "ended" else 0, TOOL)
    if store.multi:
        field("cuenta", account or "todas", ADVISOR)
    keys = "↑↓ sesión · c cuenta · a auto · q salir" if store.multi else "↑↓ sesión · a auto · q salir"
    t.append(f"   {'auto' if auto else 'fija'} · {keys}", style=DIM)
    return t


def render(console, store, focus_sid, account, now):
    width, height = console.size
    sessions = store.ordered(account)
    by_id = {s.sid: s for s in sessions}
    focus = by_id.get(focus_sid) or store.auto_focus(sessions)
    compact = height < 32
    dh = 14 if compact else 20
    wide = width >= 108
    side_w = 34 if wide else 0
    log_rows = max(1, height - 3 - (dh + 2) - 1 - 2)

    root = Layout()
    root.split_column(
        Layout(header(store, sessions, account, now), name="head", size=3),
        Layout(name="mid", size=dh + 2),
        Layout(log_panel(store, focus, account, log_rows, width, now), name="log"),
        Layout(footer(store, focus, focus_sid is None, account, now), name="foot", size=1),
    )
    diag = Panel(diagram(store, focus, max(40, width - side_w - 4), compact, now),
                 box=box.SQUARE, border_style=LINE,
                 title=trunc(focus.title, 60) if focus and focus.title else None, title_align="left")
    if wide:
        root["mid"].split_row(
            Layout(side_panel(store, sessions, focus, dh, now), size=side_w),
            Layout(diag),
        )
    else:
        root["mid"].update(diag)
    return root, sessions, focus


# ── bucle principal ──────────────────────────────────────────────────────

def read_keys(fd, timeout):
    if termios is None:
        import msvcrt
        time.sleep(timeout)
        keys = ""
        while msvcrt.kbhit():
            keys += msvcrt.getwch()
        return {"\xe0H": b"k", "\xe0P": b"j"}.get(keys, keys.encode("utf-8", "ignore"))
    if fd is None:
        time.sleep(timeout)
        return b""
    ready, _, _ = select.select([fd], [], [], timeout)
    return os.read(fd, 64) if ready else b""


def run(accounts, once):
    console = Console()
    store = Store(accounts)
    store.poll(time.time())
    account = None
    if once or not sys.stdout.isatty():
        console.print(render(console, store, None, account, time.time())[0])
        return

    fd = sys.stdin.fileno() if termios is not None and sys.stdin.isatty() else None
    saved = termios.tcgetattr(fd) if fd is not None else None
    focus_sid = None
    try:
        if fd is not None:
            tty.setcbreak(fd)
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                now = time.time()
                store.poll(now)
                root, sessions, focus = render(console, store, focus_sid, account, now)
                live.update(root, refresh=True)
                keys = read_keys(fd, 0.12)
                if not keys:
                    continue
                if b"q" in keys or b"\x03" in keys:
                    break
                if b"a" in keys:
                    focus_sid = None
                if b"c" in keys and store.multi:
                    cycle = [None] + [a.name for a in accounts]
                    account = cycle[(cycle.index(account) + 1) % len(cycle)]
                    focus_sid = None
                step = 0
                if keys in (b"j", b"\t", b"\x1b[B"):
                    step = 1
                elif keys in (b"k", b"\x1b[A"):
                    step = -1
                if step and sessions:
                    i = sessions.index(focus) if focus in sessions else 0
                    focus_sid = sessions[(i + step) % len(sessions)].sid
    except KeyboardInterrupt:
        pass
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def cmd_accounts(accounts):
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {LEAD}")
    for col in ("cuenta", "ruta", "email", "abiertas", "transcripts"):
        table.add_column(col)
    for a in accounts:
        ok = os.path.isdir(a.path)
        open_ = len(glob.glob(os.path.join(a.live_dir, "*.json")))
        total = len(glob.glob(os.path.join(a.projects, "*", "*.jsonl")))
        table.add_row(a.name, a.path.replace(HOME, "~") + ("" if ok else "  (no existe)"),
                      a.email() or "—", str(open_), str(total))
    console = Console()
    console.print(table)
    console.print(f"config: {config_path()}", style=DIM)


def main():
    ap = argparse.ArgumentParser(prog="cc-dash", description="Dashboard en vivo de sesiones de Claude Code.")
    ap.add_argument("--version", action="version", version=f"cc-dash {__version__}")
    ap.add_argument("--once", action="store_true", help="imprime un solo fotograma y sale")
    ap.add_argument("-a", "--account", action="append", metavar="CUENTA",
                    help="muestra solo esta cuenta (repetible)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("accounts", help="lista las cuentas configuradas y detectadas")
    p = sub.add_parser("add", help="registra una cuenta")
    p.add_argument("name")
    p.add_argument("path", help="directorio de configuración de Claude Code (su CLAUDE_CONFIG_DIR)")
    p = sub.add_parser("remove", help="quita una cuenta registrada")
    p.add_argument("name")
    p = sub.add_parser("scan", help="registra una carpeta cuyas subcarpetas son cuentas")
    p.add_argument("dir")
    sub.add_parser("config", help="muestra la ruta del archivo de configuración")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "config":
        print(config_path())
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
        cmd_accounts(accounts)
        return
    if args.account:
        known = {a.name for a in accounts}
        missing = [n for n in args.account if n not in known]
        if missing:
            sys.exit(f"cc-dash: cuenta desconocida: {', '.join(missing)} (hay: {', '.join(sorted(known)) or 'ninguna'})")
        accounts = [a for a in accounts if a.name in args.account]
    if not accounts:
        sys.exit("cc-dash: no hay cuentas; registra una con `cc-dash add NOMBRE RUTA`")
    run(accounts, args.once)


if __name__ == "__main__":
    main()
