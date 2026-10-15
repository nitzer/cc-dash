"""De dónde salen los datos: las cuentas de Claude Code en el disco de esta máquina.

Una fuente entrega lotes con lo nuevo desde la lectura anterior; Store solo los interpreta.
"""
import glob
import json
import os
from dataclasses import dataclass, field

try:
    import termios
except ImportError:  # Windows
    termios = None


@dataclass
class Batch:
    """Lo nuevo de una lectura."""
    records: list = field(default_factory=list)   # (cuenta, registro del transcript)
    meta: list = field(default_factory=list)      # (agentId, contenido de agent-<id>.meta.json)
    live: dict | None = None                      # sid -> info de la sesión abierta (None: no se miró)


class LocalSource:
    """Lee los transcripts que crecen, los metadatos de subagentes y las sesiones abiertas del disco."""

    def __init__(self):
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
