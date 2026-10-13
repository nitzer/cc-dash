#!/usr/bin/env python3
"""cc-dash — dashboard en vivo de las sesiones de Claude Code.

Por cada cuenta (un directorio de configuración de Claude Code) lee
sessions/*.json (sesiones abiertas) y projects/**/*.jsonl (modelo,
herramientas, subagentes, tokens). Solo lectura: no toca ni envía nada.

Teclas: ↑/↓ o j/k cambia de sesión · c filtra por cuenta · a modo auto · q sale
        ←/→ selecciona un subagente · r su última respuesta · p su prompt
        AvPág/RePág desplaza el detalle · Esc deselecciona · i actividad
Uso:    cc-dash [--once] [-a CUENTA]
        cc-dash accounts | add NOMBRE RUTA | remove NOMBRE | scan DIR | config
        cc-dash update
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import textwrap
import time
import tomllib
from collections import Counter
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

__version__ = "0.6.1"

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
MIN_SLOTS = 3      # casillas de subagentes que se dibujan como mínimo
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
    cfg = {"account": [], "scan": [], "include_default": True, "auto_update": True}
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
    cfg["auto_update"] = bool(raw.get("auto_update", True))
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
        self.tools = 0
        self.errors = 0
        self.tool_counts = Counter()
        self.skills = Counter()
        self.edits = Counter()  # ruta -> ediciones (Edit/Write/NotebookEdit)
        self.reads = set()
        self.mcp = Counter()
        self.hooks = Counter()
        self.advisor_calls = 0
        self.advisor_last = 0.0
        self.last = 0.0
        self.pending = {}   # tool_use_id -> (name, label, ts)
        self.agents = []    # lanzamientos vistos en el transcript principal
        self.side = {}      # agentId -> actividad leída del transcript del subagente
        self.requests = {}  # requestId -> tokens de salida ya contados

    def window(self):
        """Ventana de contexto. El transcript no la guarda: 1M si el modelo lo indica
        (sufijo [1m]) o si el contexto ya supera 200k; si no, 200k."""
        return 1_000_000 if "[1m]" in self.model_id or self.ctx > 200_000 else 200_000

    def side_info(self, aid):
        return self.side.setdefault(aid, {"tools": 0, "tool": None, "text": "", "final": "",
                                          "out": 0, "done": None, "model": ""})


class Store:
    def __init__(self, accounts):
        self.accounts = accounts
        self.multi = len(accounts) > 1
        self.sessions = {}
        self.log = []       # (ts, sid, style, tag, text)
        self.offsets = {}
        self.files = []     # (ruta, nombre de cuenta)
        self.live = {}      # sid -> info de ~/.claude/sessions
        self.aid_of = {}    # tool_use_id del lanzamiento -> agentId (de *.meta.json)
        self.agent_type = {}
        self.meta_seen = set()
        self.t_glob = self.t_live = self.t_tail = 0.0

    # -- lectura incremental de transcripts --
    def poll(self, now):
        if now - self.t_glob > 3:
            self.files = [(path, acct.name) for acct in self.accounts
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
                del self.log[:-400]

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

    def count_tool(self, s, name, inp):
        s.tools += 1
        s.tool_counts[short_tool(name)] += 1
        if name == "Skill" and isinstance(inp, dict) and inp.get("skill"):
            s.skills[inp["skill"]] += 1
        if name.startswith("mcp__"):
            s.mcp[name.split("__")[1]] += 1
        path = inp.get("file_path") or inp.get("notebook_path") if isinstance(inp, dict) else None
        if path and name in ("Edit", "Write", "NotebookEdit"):
            s.edits[path] += 1
        elif path and name == "Read":
            s.reads.add(path)

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
                self.task_done(s, text, ts)
        elif kind == "system" and o.get("subtype") == "turn_duration" and not side:
            s.turns += 1
            s.turn_last = o.get("durationMs") or 0
            s.turn_total += s.turn_last
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
                if b.get("type") == "tool_use":
                    self.count_tool(s, name, inp)
                    label = tool_label(name, inp)
                    if name in AGENT_TOOLS and not side:
                        s.agents.append({
                            "id": b.get("id"), "aid": None, "start": ts, "end": None,
                            "type": inp.get("subagent_type") or "general",
                            "desc": inp.get("description") or "",
                            "prompt": inp.get("prompt") or "",
                            "model": inp.get("model") or "",
                            "result": "", "status": "",
                        })
                        self.event(ts, sid, SWARM, "Agent", f"▶ {label}")
                    else:
                        s.pending[b.get("id")] = (short_tool(name), label, ts)
                        self.event(ts, sid, DIM if side else TOOL,
                                   ("↳ " if side else "") + short_tool(name), label)
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
            result = o.get("toolUseResult") if isinstance(o.get("toolUseResult"), dict) else {}
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                if b.get("is_error"):
                    s.errors += 1
                done = s.pending.pop(tid, None)
                if done and b.get("is_error"):
                    self.event(ts, sid, ERR, done[0], "✗ " + (done[1] or "error"))
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
        if msg.get("model"):
            info["model"] = msg["model"]
        self.add_usage(s, o, msg, info)
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
                self.count_tool(s, name, inp)
                self.event(ts, s.sid, DIM, "↳ " + (self.agent_type.get(aid) or "agente"),
                           f"{info['tool'][0]}: {info['tool'][1]}")

    def task_done(self, s, text, ts):
        task = re.search(r"<task-id>([^<]+)</task-id>", text)
        status = re.search(r"<status>([^<]+)</status>", text)
        if not task:
            return
        for a in s.agents:
            if (a["aid"] or self.aid_of.get(a["id"])) == task.group(1) and a["end"] is None:
                self.finish(s, a, ts, status.group(1) if status else "completed")

    def finish(self, s, a, ts, status):
        a["end"] = ts or time.time()
        a["status"] = status
        ok = status == "completed"
        self.event(ts, s.sid, SWARM if ok else ERR, "Agent",
                   f"{'✓' if ok else '✗'} {a['desc']} ({clock(a['end'] - a['start'])})")

    # -- vistas --
    def state(self, s):
        info = self.live.get(s.sid)
        if not info:
            return "ended"
        return "busy" if info.get("status") == "busy" else "idle"

    def agents(self, s):
        """Subagentes de la sesión con su actividad: en marcha primero, luego recientes."""
        alive = self.state(s) != "ended"
        out = []
        for a in s.agents:
            aid = a["aid"] or self.aid_of.get(a["id"])
            side = s.side.get(aid) or {}
            end = a["end"] or side.get("done")
            out.append({
                **a, "aid": aid, "end": end, "running": end is None and alive,
                "tools": side.get("tools", 0), "tool": side.get("tool"), "out": side.get("out", 0),
                "model": a["model"] or (short_model(side["model"]) if side.get("model") else ""),
                "status": a["status"] or ("completed" if end else ""),
                "response": (side.get("final") or a["result"] or side.get("text") or "").strip(),
            })
        out.sort(key=lambda v: (not v["running"], -(v["start"] or 0)))
        return out

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


def hpath(x0, x1, y):
    step = 1 if x1 >= x0 else -1
    return [(x, y) for x in range(x0, x1 + step, step)]


def diagram(store, s, w, compact, now, sel=None):
    h = 15 if compact else 21
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
            cv.tint(hpath(aw, aw + gap - 1, 2), f"bold {breath(ADVISOR, now)}")
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
    agents = store.agents(s)
    slots = max(MIN_SLOTS, min(len(agents), (w + 2) // (SLOT_WIDTH + 2)))
    idx = next((i for i, v in enumerate(agents) if v["id"] == sel), None)
    first = max(0, idx - slots + 1) if idx is not None else 0
    shown = agents[first:first + slots]
    sgap = 2
    sw = (w - sgap * (slots - 1)) // slots
    xs = [i * (sw + sgap) + sw // 2 for i in range(slots)]
    trunk = lx + lw // 2
    btrunk = w // 2
    cv.put(trunk, 5, "│", LINE)
    cv.branch(6, xs, trunk, LINE, down=True)
    if len(agents) > slots:
        hidden = agents[:first] + agents[first + slots:]
        live = sum(1 for v in hidden if v["running"])
        note = f"+{len(hidden)} más" + (f", {live} en marcha" if live else "") + " · ←→ para verlos"
        cv.put(max(0, w - len(note)), 5, note, SWARM if live else DIM)
    if not compact:
        cv.branch(15, xs, btrunk, LINE, down=False)
        cv.put(btrunk, 16, "▼", LINE)
    glow = []
    for i in range(slots):
        x = i * (sw + sgap)
        v = shown[i] if i < len(shown) else None
        live = v is not None and v["running"]
        chosen = v is not None and v["id"] == sel
        cv.put(xs[i], 7, "▼", SWARM if live else LINE)
        cv.box(x, 8, sw, 6, "bold #ffffff" if chosen else SWARM if live else LINE, heavy=chosen)
        if not compact:
            cv.put(xs[i], 14, "│", LINE)
        if v is None:
            cv.put(x + 2, 10, trunc("· libre ·", sw - 4), DIM)
            continue
        inner = sw - 4
        failed = bool(v["end"]) and v["status"] != "completed"
        if live:
            icon, ist = SPIN[(phase + i * 3) % len(SPIN)], f"bold {SWARM}"
        elif failed:
            icon, ist = "✗", f"bold {ERR}"
        elif v["end"]:
            icon, ist = "✓", f"bold {TOOL}"
        else:
            icon, ist = "■", DIM
        head = v["type"] + (f" · {v['model']}" if v["model"] else "")
        cv.put(x + 2, 9, icon, ist)
        cv.put(x + 4, 9, trunc(head, inner - 2), f"bold {SWARM}" if live else "bold" if chosen else FG)
        cv.put(x + 2, 10, trunc(v["desc"], inner), FG if live or chosen else DIM)
        if live and v["tool"]:
            cv.put(x + 2, 11, trunc(f"▶ {v['tool'][0]}: {v['tool'][1]}", inner), TOOL)
        elif live:
            cv.put(x + 2, 11, trunc("pensando…", inner), DIM)
        else:
            cv.put(x + 2, 11, trunc(v["response"] or "sin respuesta", inner), FG if chosen else DIM)
        if live or v["end"]:
            stats = clock((now if live else v["end"]) - v["start"])
        else:
            stats = "interrumpido"
        stats += f" · {v['tools']} tools" if v["tools"] else ""
        cv.put(x + 2, 12, trunc(stats, inner), SWARM if live else DIM)
        if live:
            glow.append(([(trunk, 5)] + hpath(trunk, xs[i], 6) + [(xs[i], 7)], SWARM))
        elif not compact and v["end"] and now - v["end"] < 8:
            glow.append(([(xs[i], 14)] + hpath(xs[i], btrunk, 15) + [(btrunk, 16)], LEAD))
    # los caminos activos "respiran"; se tiñen al final para que no los pise otra tarjeta
    for cells, color in glow:
        cv.tint(cells, f"bold {breath(color, now)}")

    # vuelta al lead
    if not compact:
        bw = min(56, w)
        bx = btrunk - bw // 2
        cv.box(bx, 17, bw, 4, lst)
        done = sum(1 for v in agents if v["end"])
        cv.put(bx + 2, 18, trunc(f"de vuelta al lead · {done} subagente{'s' if done != 1 else ''} terminado{'s' if done != 1 else ''}", bw - 4), lst)
        stats = f"{s.turns} turnos · {s.tools} tools · out {ktok(s.out_tokens)} · ctx {ktok(s.ctx)}"
        if s.ctx:
            stats += f" ({min(100, round(100 * s.ctx / s.window()))}%)"
        if s.cost is not None:
            stats += f" · ${s.cost:.2f}"
        cv.put(bx + 2, 19, trunc(stats, bw - 4), DIM)
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


def stats_panel(store, s, now):
    """Actividad de la sesión enfocada: lo que se muestra sin subagente seleccionado."""
    t = Text()
    if s is None:
        return Panel(t, title="actividad", title_align="left", box=box.SQUARE, border_style=LINE)

    def row(label, value, style=FG):
        t.append(f"{label:<11}", style=DIM)
        t.append(value + "\n", style=style)

    def counts(c, n=5):
        return " · ".join(f"{k} ×{v}" for k, v in c.most_common(n)) + (f" · +{len(c) - n} más" if len(c) > n else "")

    row("modelo", " · ".join(x for x in (short_model(s.model), s.effort and f"effort {s.effort}",
                                         s.perm and f"permisos {s.perm}") if x))
    if s.ctx:
        pct = min(100, round(100 * s.ctx / s.window()))
        fill = round(pct / 100 * 12)
        t.append(f"{'contexto':<11}", style=DIM)
        t.append("█" * fill, style=TOOL if pct < 60 else LEAD if pct < 85 else ERR)
        t.append("░" * (12 - fill), style=LINE)
        t.append(f" {pct}% · {ktok(s.ctx)} de {ktok(s.window())}\n", style=FG)
    usage = []
    if s.cache_pct is not None:
        usage.append(f"caché {s.cache_pct}%")
    usage.append(f"salida {ktok(s.out_tokens)}")
    if s.cost is not None:
        usage.append(f"${s.cost:.2f}")
    row("tokens", " · ".join(usage))
    turns = str(s.turns)
    if s.turns:
        turns += f" · último {age(s.turn_last / 1000)} · total {age(s.turn_total / 1000)}"
    row("turnos", turns)
    tools = counts(s.tool_counts) or "ninguna"
    row("tools", tools + (f" · {s.errors} con error" if s.errors else ""), TOOL if s.tool_counts else DIM)
    row("skills", counts(s.skills) or "ninguna", ADVISOR if s.skills else DIM)
    if s.edits or s.reads:
        names = Counter()
        for path, n in s.edits.items():
            names[os.path.basename(path)] += n
        only_read = len(s.reads - set(s.edits))
        files = counts(names, 4) if names else "ninguno editado"
        row("archivos", files + (f" · {only_read} solo leído{'s' if only_read != 1 else ''}" if only_read else ""), LEAD if names else DIM)
    if s.mcp:
        row("mcp", counts(s.mcp))
    agents = store.agents(s)
    if agents:
        live = sum(1 for v in agents if v["running"])
        row("subagentes", counts(Counter(v["type"] for v in agents)) + (f" · {live} en marcha" if live else ""), SWARM)
    else:
        row("subagentes", "ninguno", DIM)
    if s.hooks:
        row("hooks", counts(s.hooks))
    code = []
    if s.lines and any(s.lines):
        code.append(f"+{s.lines[0]} −{s.lines[1]} líneas")
    if s.branch:
        code.append(f"rama {s.branch}")
    if code:
        row("código", " · ".join(code))
    return Panel(t, title="actividad", title_align="left", box=box.SQUARE, border_style=LINE)


def detail_panel(store, s, ui, rows, width, now):
    """A la derecha del log: el subagente seleccionado, o la actividad de la sesión."""
    agents = store.agents(s) if s else []
    i = next((n for n, v in enumerate(agents) if v["id"] == ui["sel"]), None)
    if i is None:
        ui["sel"] = None
        return stats_panel(store, s, now)
    v = agents[i]
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
    tabs.append(" r respuesta ", style="reverse bold" if not prompt else DIM)
    tabs.append(" ")
    tabs.append(" p prompt ", style="reverse bold" if prompt else DIM)
    state = "en marcha" if v["running"] else "terminado" if v["status"] == "completed" else v["status"] or "interrumpido"
    tabs.append(f"   {state}", style=SWARM if v["running"] else DIM)
    body = Text("\n".join(lines[top: top + body_rows]), style=FG, no_wrap=True, overflow="ellipsis")
    head = f"subagente {i + 1}/{len(agents)} · {v['type']}" + (f" · {v['model']}" if v["model"] else "")
    more = len(lines) - top - body_rows
    pos = f"{top + 1}–{min(len(lines), top + body_rows)} de {len(lines)}" + (" ↓" if more > 0 else "")
    return Panel(Group(tabs, Text(""), body), title=trunc(f"{head} · {v['desc']}", max(10, width - 6)),
                 title_align="left", subtitle=pos if len(lines) > body_rows else None,
                 subtitle_align="right", box=box.SQUARE, border_style=SWARM)


def footer(store, s, auto, account, now, sel=False):
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
    field("lead", state, LEAD if state == "busy" else TOOL if state == "idle" else DIM)
    field("subagents", f"{sum(1 for v in agents if v['running'])}/{len(agents)}", SWARM)
    field("advisor", "on call" if s.advisor else "off", ADVISOR)
    field("tools", len(s.pending) if state != "ended" else 0, TOOL)
    if store.multi:
        field("cuenta", account or "todas", ADVISOR)
    if sel:
        keys = "←→ subagente · r respuesta · p prompt · AvPág/RePág · Esc soltar · q salir"
    else:
        keys = "←→ subagentes · ↑↓ sesión · " + ("c cuenta · " if store.multi else "") + "a auto · q salir"
    t.append(f"   {'auto' if auto else 'fija'} · {keys}", style=DIM)
    return t


def new_ui():
    return {"sel": None, "tab": "resp", "scroll": 0, "info": False, "rows": 1}


def render(console, store, focus_sid, account, now, ui=None):
    ui = ui if ui is not None else new_ui()
    width, height = console.size
    sessions = store.ordered(account)
    by_id = {s.sid: s for s in sessions}
    focus = by_id.get(focus_sid) or store.auto_focus(sessions)
    compact = height < 34
    dh = 15 if compact else 21
    wide = width >= 108
    side_w = 34 if wide else 0
    rows = max(1, height - 3 - (dh + 2) - 1 - 2)
    split = width >= 100
    detail_w = max(44, width * 2 // 5) if split else width

    detail = detail_panel(store, focus, ui, rows, detail_w, now)
    log = log_panel(store, focus, account, rows, width - detail_w if split else width, now)
    bottom = Layout(name="bottom")
    if split:
        bottom.split_row(Layout(log), Layout(detail, size=detail_w))
    else:
        bottom.update(detail if ui["sel"] or ui["info"] else log)

    root = Layout()
    root.split_column(
        Layout(header(store, sessions, account, now), name="head", size=3),
        Layout(name="mid", size=dh + 2),
        bottom,
        Layout(footer(store, focus, focus_sid is None, account, now, sel=bool(ui["sel"])), name="foot", size=1),
    )
    diag = Panel(diagram(store, focus, max(40, width - side_w - 4), compact, now, ui["sel"]),
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
        arrows = {"\xe0H": b"k", "\xe0P": b"j", "\xe0K": b"h", "\xe0M": b"l",
                  "\xe0I": b"\x1b[5~", "\xe0Q": b"\x1b[6~"}
        return arrows.get(keys, keys.encode("utf-8", "ignore"))
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
    ui = new_ui()
    try:
        if fd is not None:
            tty.setcbreak(fd)
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                now = time.time()
                store.poll(now)
                root, sessions, focus = render(console, store, focus_sid, account, now, ui)
                live.update(root, refresh=True)
                keys = read_keys(fd, 0.12)
                if not keys:
                    continue
                if keys in (b"q", b"\x03"):
                    break
                ids = [v["id"] for v in store.agents(focus)] if focus else []
                page = max(1, ui["rows"] - 1)
                if keys in (b"l", b"h", b"\x1b[C", b"\x1b[D"):
                    order = [None] + ids
                    i = order.index(ui["sel"]) if ui["sel"] in order else 0
                    ui["sel"] = order[(i + (1 if keys in (b"l", b"\x1b[C") else -1)) % len(order)]
                    ui["scroll"] = 0
                    if ui["sel"]:
                        focus_sid = focus.sid  # que el modo auto no cambie de sesión mientras se lee
                elif keys == b"\x1b":
                    ui["sel"] = None
                elif keys in (b"p", b"r"):
                    if ui["sel"] is None and ids:
                        ui["sel"], focus_sid = ids[0], focus.sid
                    ui["tab"] = "prompt" if keys == b"p" else "resp"
                    ui["scroll"] = 0
                elif keys == b"i":
                    ui["info"] = not ui["info"]
                elif keys in (b" ", b"\x1b[6~"):
                    ui["scroll"] += page
                elif keys in (b"b", b"\x1b[5~"):
                    ui["scroll"] = max(0, ui["scroll"] - page)
                elif keys == b"a":
                    focus_sid, ui["sel"] = None, None
                elif keys == b"c" and store.multi:
                    cycle = [None] + [a.name for a in accounts]
                    account = cycle[(cycle.index(account) + 1) % len(cycle)]
                    focus_sid, ui["sel"] = None, None
                elif keys in (b"j", b"k", b"\t", b"\x1b[A", b"\x1b[B") and sessions:
                    step = -1 if keys in (b"k", b"\x1b[A") else 1
                    i = sessions.index(focus) if focus in sessions else 0
                    focus_sid, ui["sel"] = sessions[(i + step) % len(sessions)].sid, None
                    ui["scroll"] = 0
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
    ap.add_argument("--no-update", action="store_true", help="no busca actualizaciones al arrancar")
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
    sub.add_parser("update", help="trae los cambios del repo y reinstala")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "config":
        print(config_path())
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
    if cfg["auto_update"] and not args.no_update and not args.once and sys.stdout.isatty():
        auto_update()
    run(accounts, args.once)


if __name__ == "__main__":
    main()
