"""Genera cuentas de Claude Code de prueba: transcripts, subagentes y sesiones abiertas."""
import json
import os
import time
from datetime import UTC, datetime

SID = "11111111-2222-3333-4444-555555555555"


def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


class Transcript:
    """Registros de una sesión, con marcas de tiempo que avanzan solas."""

    def __init__(self, sid=SID, cwd="/tmp/proyecto", start=None):
        self.sid, self.cwd = sid, cwd
        self.ts = start if start is not None else time.time() - 600
        self.records = []
        self.n = 0

    def add(self, rec, step=1.0):
        self.ts += step
        self.records.append({"sessionId": self.sid, "timestamp": iso(self.ts), **rec})
        return self

    def prompt(self, text):
        return self.add({"type": "user", "cwd": self.cwd, "message": {"role": "user", "content": text}})

    def assistant(self, *blocks, out=10, ctx=1000, model="claude-opus-5-5", rid=None, **extra):
        self.n += 1
        usage = {"output_tokens": out, "input_tokens": ctx}
        return self.add({"type": "assistant", "requestId": rid or f"r{self.n}", **extra,
                         "message": {"role": "assistant", "model": model, "usage": usage, "content": list(blocks)}})

    def tool(self, tid, name, out=10, ctx=1000, **inp):
        return self.assistant({"type": "tool_use", "id": tid, "name": name, "input": inp}, out=out, ctx=ctx)

    def result(self, tid, text="ok", error=False, **tool_result):
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}
        if tool_result:
            rec["toolUseResult"] = tool_result
        return self.add(rec)

    def text(self, text, **kw):
        return self.assistant({"type": "text", "text": text}, **kw)

    def turn_end(self, ms=5000):
        return self.add({"type": "system", "subtype": "turn_duration", "durationMs": ms})

    def meta(self, kind, **fields):
        return self.add({"type": kind, **fields})


class SideTranscript(Transcript):
    """Transcript propio de un subagente (sessions/<sid>/subagents/agent-<aid>.jsonl)."""

    def __init__(self, aid, **kw):
        super().__init__(**kw)
        self.aid = aid

    def add(self, rec, step=1.0):
        return super().add({"isSidechain": True, "agentId": self.aid, **rec}, step)


class Account:
    """Un directorio de configuración de Claude Code en disco."""

    def __init__(self, root):
        self.root = str(root)
        self.project = os.path.join(self.root, "projects", "-tmp-proyecto")
        os.makedirs(self.project, exist_ok=True)
        os.makedirs(os.path.join(self.root, "sessions"), exist_ok=True)

    def write(self, tr, append=False):
        path = os.path.join(self.project, f"{tr.sid}.jsonl")
        self._dump(path, tr.records, append)
        return path

    def write_side(self, sid, side, agent_type, tool_use_id):
        sub = os.path.join(self.project, sid, "subagents")
        os.makedirs(sub, exist_ok=True)
        self._dump(os.path.join(sub, f"agent-{side.aid}.jsonl"), side.records)
        with open(os.path.join(sub, f"agent-{side.aid}.meta.json"), "w") as fh:
            json.dump({"agentType": agent_type, "toolUseId": tool_use_id}, fh)

    def open_session(self, sid=SID, status="idle", pid=None, **info):
        """Marca la sesión como abierta; por defecto con el pid de los tests, que está vivo."""
        pid = pid or os.getpid()
        with open(os.path.join(self.root, "sessions", f"{pid}-{sid[:8]}.json"), "w") as fh:
            json.dump({"pid": pid, "sessionId": sid, "status": status, "startedAt": time.time() * 1000, **info}, fh)

    @staticmethod
    def _dump(path, records, append=False):
        with open(path, "a" if append else "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
