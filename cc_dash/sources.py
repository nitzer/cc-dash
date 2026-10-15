"""De dónde salen los datos: las cuentas de Claude Code de esta máquina y de otras, por ssh.

Una fuente entrega lotes con lo nuevo desde la lectura anterior; Store solo los interpreta.
En otra máquina, `cc-dash agent` lee su disco con LocalSource y manda los lotes como líneas
JSON; aquí RemoteSource los recibe por ssh. Sources junta todas las fuentes en una.
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from .text import block_text, clip

try:
    import termios
except ImportError:  # Windows
    termios = None


@dataclass
class Batch:
    """Lo nuevo de una lectura."""
    records: list = field(default_factory=list)   # (cuenta, registro del transcript)
    backlog: list = field(default_factory=list)   # como records, pero historia ya pasada: sin avisos
    meta: list = field(default_factory=list)      # (agentId, contenido de agent-<id>.meta.json)
    live: dict | None = None                      # sid -> info de la sesión abierta (None: no se miró)
    forget: list = field(default_factory=list)    # máquinas cuyos datos hay que olvidar (se van a reenviar)


class LocalSource:
    """Lee los transcripts que crecen, los metadatos de subagentes y las sesiones abiertas del disco."""

    remote_accounts = frozenset()

    def __init__(self, since=None):
        self.since = since  # si se da: de los archivos sin tocar desde entonces solo se lee lo que se añada
        self.offsets = {}   # ruta -> bytes ya leídos
        self.files = []     # (ruta, nombre de cuenta)
        self.meta_seen = set()
        self.t_glob = self.t_live = self.t_tail = 0.0

    @property
    def transcripts(self):
        return len(self.files)

    def refresh(self):
        """Que la próxima lectura vuelva a buscar archivos y sesiones (p. ej. al activar una cuenta)."""
        self.t_glob = self.t_live = 0.0

    def read(self, now, accounts):
        batch = Batch()
        if now - self.t_glob > 3:
            self.files = [(path, acct.name) for acct in accounts
                          for path in glob.glob(os.path.join(acct.projects, "**", "*.jsonl"), recursive=True)]
            self.t_glob = now
            for path, _ in self.files:
                if self.since and path not in self.offsets:
                    self.skip_if_old(path)
                if os.sep + "subagents" + os.sep in path and path not in self.meta_seen:
                    self.read_meta(path, batch)
        if now - self.t_live > 1:
            batch.live = self.read_live(accounts)
            self.t_live = now
        if now - self.t_tail > 0.5:
            self.t_tail = now
            for path, account in self.files:
                batch.records.extend((account, rec) for rec in self.tail(path))
        return batch

    def skip_if_old(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return
        if st.st_mtime < self.since:
            self.offsets[path] = st.st_size

    def read_meta(self, path, batch):
        """agent-<id>.meta.json enlaza el transcript del subagente con su lanzamiento."""
        aid = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        try:
            with open(path[:-len(".jsonl")] + ".meta.json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return
        self.meta_seen.add(path)
        batch.meta.append((aid, meta))

    def tail(self, path):
        """Los registros completos que se añadieron al archivo desde la última vez."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        pos = self.offsets.get(path, 0)
        if size < pos:
            pos = 0
        if size == pos:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                data = fh.read(size - pos)
        except OSError:
            return []
        cut = data.rfind(b"\n")
        if cut < 0:
            return []
        self.offsets[path] = pos + cut + 1
        out = []
        for raw in data[:cut].split(b"\n"):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    @staticmethod
    def read_live(accounts):
        """Sesiones abiertas: sessions/<pid>.json de cada cuenta, si el proceso sigue vivo."""
        live = {}
        for acct in accounts:
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
        return live


# ── otras máquinas ───────────────────────────────────────────────────────

PROTOCOL = 1        # versión de las líneas que manda `cc-dash agent`; las dos puntas deben coincidir
HEARTBEAT = 5       # segundos máximos sin mandar nada: así el dashboard sabe que la conexión sigue viva
RETRY = 10          # segundos entre intentos de reconexión
# en la otra máquina cc-dash suele estar en ~/.local/bin, que ssh sin login no pone en el PATH
REMOTE_CMD = 'sh -c \'PATH="$HOME/.local/bin:$PATH"; exec cc-dash agent\''


def remote_account(account, host):
    """Nombre con el que se ve aquí una cuenta de otra máquina."""
    return f"{account}@{host}"


def host_of(account):
    return account.rpartition("@")[2]


AGENT_HISTORY = 2 * 86400   # el agente manda la historia de los transcripts tocados en estos segundos
CHUNK = 1_000_000           # bytes aproximados por línea: la carga inicial va en trozos


def run_agent(accounts, out=None, interval=0.5):
    """`cc-dash agent`: lee el disco de esta máquina y escribe lo nuevo como líneas JSON.

    Primero la historia reciente (en trozos; el último lleva "ready"); después, solo lo nuevo, las
    sesiones abiertas cuando cambian y un latido cada HEARTBEAT segundos. Los registros van recortados
    a lo que usa el dashboard. Termina cuando nadie lee la salida."""
    out = out or sys.stdout
    source = LocalSource(since=time.time() - AGENT_HISTORY)
    sent_live, last, first = None, 0.0, True
    try:
        while True:
            now = time.time()
            batch = source.read(now, accounts)
            live = batch.live if batch.live is not None and batch.live != sent_live else None
            sent_live = live if live is not None else sent_live
            msgs = agent_messages(batch, live, first)
            if msgs or now - last >= HEARTBEAT:
                for msg in msgs or [{"v": PROTOCOL}]:
                    out.write(json.dumps(msg, separators=(",", ":")) + "\n")
                out.flush()
                last, first = now, False
            time.sleep(interval)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


def agent_messages(batch, live, first):
    """Los mensajes de un lote: los registros en trozos de unos CHUNK bytes; el último lleva lo demás."""
    msgs, chunk, size = [], [], 0
    for account, rec in batch.records:
        rec = slim(rec)
        chunk.append((account, rec))
        size += len(json.dumps(rec, separators=(",", ":")))
        if size >= CHUNK:
            msgs.append({"v": PROTOCOL, "records": chunk})
            chunk, size = [], 0
    last = {"v": PROTOCOL}
    if chunk:
        last["records"] = chunk
    if batch.meta:
        last["meta"] = batch.meta
    if live is not None:
        last["live"] = live
    if first:
        last["ready"] = True
    if len(last) > 1:
        msgs.append(last)
    return msgs


# campos de un registro que usa el dashboard; el resto (contenidos de archivos, imágenes,
# razonamiento) no se manda por la red
KEEP = {"sessionId", "session_id", "type", "timestamp", "cwd", "gitBranch", "isSidechain", "agentId",
        "requestId", "aiTitle", "permissionMode", "totalCostUSD", "totalLinesAdded", "totalLinesRemoved",
        "operation", "content", "subtype", "durationMs", "advisorModel", "effort", "attributionSkill", "isMeta"}
RESULT_KEEP = ("agentId", "resolvedModel", "isAsync", "status", "backgroundTaskId")


def slim(rec):
    """El registro con solo lo que interpreta Store, y los textos largos recortados como él los recorta."""
    out = {k: v for k, v in rec.items() if k in KEEP}
    if isinstance(out.get("content"), str):
        out["content"] = out["content"][:4000]
    if isinstance(rec.get("toolUseResult"), dict):
        out["toolUseResult"] = {k: rec["toolUseResult"][k] for k in RESULT_KEEP if k in rec["toolUseResult"]}
    att = rec.get("attachment")
    if isinstance(att, dict):
        out["attachment"] = {k: att[k] for k in ("type", "hookEvent", "identity") if k in att}
    msg = rec.get("message")
    if isinstance(msg, dict):
        out["message"] = {k: msg[k] for k in ("model", "usage") if k in msg}
        content = msg.get("content")
        out["message"]["content"] = content if isinstance(content, str) else [
            slim_block(b) for b in content or [] if isinstance(b, dict) and b.get("type") != "thinking"]
    return out


def slim_block(b):
    if b.get("type") == "tool_use":
        inp = b.get("input")
        if isinstance(inp, dict):
            inp = {k: clip(v, 3000) if isinstance(v, str) else v for k, v in inp.items()}
        return {**b, "input": inp}
    if b.get("type") == "tool_result":
        return {**b, "content": clip(block_text(b.get("content")), 6000)}
    return b


class RemoteSource:
    """Las cuentas de otra máquina, a través de `ssh HOST cc-dash agent`. Reconecta sola."""

    transcripts = 0

    def __init__(self, host, command=None):
        self.host = host
        self.command = command or ["ssh", "-C", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                   "-o", "ServerAliveInterval=15", host, REMOTE_CMD]
        self.lock = threading.Lock()
        self.inbox = []          # mensajes recibidos y aún sin entregar
        self.proc = None
        self.started = -RETRY
        self.state, self.error = "conectando", ""
        self.ready = False       # ya llegó la primera carga completa
        self.fresh = False       # conexión nueva: lo que había de esta máquina hay que olvidarlo
        self.live = {}
        self.accounts = set()    # nombres de las cuentas de esta máquina vistos hasta ahora

    @property
    def remote_accounts(self):
        return frozenset(self.accounts)

    def refresh(self):
        pass

    def connect(self, now):
        self.started = now
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
        except OSError as exc:
            self.proc, self.state, self.error = None, "sin conexión", str(exc)
            return
        with self.lock:
            self.fresh = True
        threading.Thread(target=self.listen, args=(self.proc,), daemon=True).start()

    def listen(self, proc):
        """Hilo: recoge las líneas del agente hasta que la conexión se corta."""
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                with self.lock:
                    self.inbox.append(msg)
        err = proc.stderr.read().decode(errors="replace").strip().splitlines()
        with self.lock:
            self.error = err[-1] if err else "la conexión se cerró"

    def read(self, now, accounts):
        batch = Batch()
        alive = self.proc is not None and self.proc.poll() is None
        if not alive and self.proc is not None:   # se cortó: sus sesiones dejan de verse abiertas
            self.proc, self.state, self.ready = None, "sin conexión", False
            self.live, batch.live = {}, {}
        if self.proc is None and now - self.started >= RETRY:
            self.connect(now)
        with self.lock:
            inbox, self.inbox = self.inbox, []
            if self.fresh:
                batch.forget.append(self.host)
                self.fresh = False
        for msg in inbox:
            self.take(msg, batch)
        return batch

    def take(self, msg, batch):
        if msg.get("v") != PROTOCOL:
            self.state, self.error = "sin conexión", f"versión distinta de cc-dash en {self.host}: actualízalo"
            return
        self.state = "conectado"
        into = batch.backlog if msg.get("ready") or not self.ready else batch.records
        for account, rec in msg.get("records", ()):
            name = remote_account(account, self.host)
            self.accounts.add(name)
            into.append((name, rec))
        batch.meta.extend(msg.get("meta", ()))
        if "live" in msg:
            self.live = {sid: {**info, "_account": remote_account(info.get("_account", "?"), self.host)}
                         for sid, info in msg["live"].items()}
            self.accounts.update(info["_account"] for info in self.live.values())
            batch.live = self.live
        if msg.get("ready"):
            self.ready = True

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()


class Sources:
    """Varias fuentes vistas como una: la de esta máquina y las remotas."""

    def __init__(self, parts):
        self.parts = parts
        self.lives = [{} for _ in parts]   # últimas sesiones abiertas de cada fuente

    @property
    def transcripts(self):
        return sum(p.transcripts for p in self.parts)

    @property
    def remote_accounts(self):
        return frozenset().union(*(p.remote_accounts for p in self.parts))

    @property
    def remotes(self):
        return [p for p in self.parts if isinstance(p, RemoteSource)]

    def refresh(self):
        for p in self.parts:
            p.refresh()

    def read(self, now, accounts):
        batch, changed = Batch(), False
        for i, part in enumerate(self.parts):
            got = part.read(now, accounts)
            batch.records.extend(got.records)
            batch.backlog.extend(got.backlog)
            batch.meta.extend(got.meta)
            batch.forget.extend(got.forget)
            if got.live is not None:
                self.lives[i], changed = got.live, True
        if changed:
            batch.live = {sid: info for live in self.lives for sid, info in live.items()}
        return batch

    def settled(self):
        """Cada máquina remota ya mandó su primera carga o no se pudo conectar."""
        return all(r.ready or r.state == "sin conexión" for r in self.remotes)

    def close(self):
        for r in self.remotes:
            r.close()
