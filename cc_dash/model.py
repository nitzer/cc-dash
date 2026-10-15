"""Lo que el dashboard entiende de los transcripts: sesiones, subagentes, estado y atascos."""

import glob
import json
import os
import re
import subprocess
import time
from collections import Counter
from collections import deque
from itertools import pairwise

try:
    import termios
except ImportError:  # Windows
    termios = None

from .config import limits
from .repo import git
from .text import age, block_text, clip, clip_input, clock, is_doc, parse_ts, short_model, short_tool, tool_label
from .theme import C


AGENT_TOOLS = ("Agent", "Task")
ASK_TOOLS = {"AskUserQuestion": "responder una pregunta", "ExitPlanMode": "aprobar el plan"}


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
        growth = [b - a for a, b in pairwise(marks) if b > a]   # una compactación no cuenta
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

    def event(self, ts, sid, role, tag, text, agent=None, ref=None):
        """`role`: papel de color ("TOOL", "ERR"…), no el color, para que el log siga al tema.
        `agent`: id del lanzamiento o agentId del subagente al que pertenece el evento.
        `ref`: de dónde sacar el detalle (un item de la conversación o {"k": "agent", "id": …})."""
        self.log.append((ts, sid, role, tag, text, agent, ref))

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
        if path and name in ("Edit", "Write", "NotebookEdit") and is_doc(path):
            path = os.path.join(s.cwd or "", path)
            s.docs = [*(p for p in s.docs if p != path), path]

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
                        self.event(ts, sid, "SWARM", "Agent", f"▶ {label}", b.get("id"),
                                   {"k": "agent", "id": b.get("id")})
                    else:
                        s.pending[b.get("id")] = (short_tool(name), label, ts)
                        self.event(ts, sid, "DIM" if side else "TOOL",
                                   ("↳ " if side else "") + short_tool(name), label, ref=item)
                elif "advisor" in name.lower() or "advisor" in str(b.get("type")):
                    if b.get("type", "").endswith("tool_use"):
                        s.advisor_calls += 1
                        self.event(ts, sid, "ADVISOR", "advisor", "consulta al advisor")
        elif kind == "user":
            if isinstance(content, str):
                if not side and s.turn_start is None:
                    s.turn_start = ts
                if not side and not o.get("isMeta") and not content.lstrip().startswith("<"):
                    s.pending.clear()
                    s.err_streak = s.rep_n = 0   # un prompt nuevo empieza de cero
                    s.rep_key = None
                    item = s.add_item({"k": "prompt", "ts": ts, "text": content.strip()})
                    self.event(ts, sid, "LEAD", "prompt", "› " + content, ref=item)
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
                    self.event(ts, sid, "ERR", done[0], "✗ " + (done[1] or "error"), ref=item)
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
                self.event(ts, s.sid, "DIM", "↳ " + (self.agent_type.get(aid) or "agente"),
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
        self.event(ts, s.sid, "SWARM" if ok else "ERR", "Agent",
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
                "color": C.AGENTS[n % len(C.AGENTS)],   # por orden de creación: no cambia
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
                if show_all or v["running"] or v["id"] == sel or (v["end"] and now - v["end"] < limits["hide_after"])]

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
        if limits["stuck_errors"] and s.err_streak >= limits["stuck_errors"]:
            why.append(f"{s.err_streak} errores seguidos" + (f" en {s.err_last}" if s.err_last else ""))
        if limits["stuck_repeat"] and s.rep_n >= limits["stuck_repeat"] and s.rep_key:
            why.append(f"repite {short_tool(s.rep_key[0])} {s.rep_n} veces igual")
        if s.pending:
            name, _, since = min(s.pending.values(), key=lambda p: p[2])
            if limits["stuck_tool"] and since and now - since > limits["stuck_tool"]:
                why.append(f"{name} lleva {age(now - since)} sin volver")
        elif limits["stuck_silent"] and s.last and now - s.last > limits["stuck_silent"]:
            why.append(f"sin actividad hace {age(now - s.last)}")
        return why

    def auto_focus(self, sessions):
        if not sessions:
            return None
        rank = {"waiting": 3, "busy": 2, "idle": 1, "ended": 0}
        now = time.time()
        return max(sessions, key=lambda s: (rank[self.state(s)], bool(self.stuck(s, now)), s.last))
