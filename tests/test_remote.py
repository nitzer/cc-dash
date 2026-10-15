"""Otras máquinas: `cc-dash agent` y RemoteSource, con un proceso local en lugar de ssh."""
import json
import os
import subprocess
import sys
import time

import pytest
from builder import SID, Transcript
from test_cli import cc

from cc_dash import model, sources

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = [sys.executable, "-c", f"import sys; sys.path.insert(0, {ROOT!r}); sys.argv = ['cc-dash', 'agent'];"
         " from cc_dash.cli import main; main()"]


@pytest.fixture
def remote_env(tmp_path, acct, monkeypatch):
    """El "otro" equipo: la cuenta de prueba vista por un agente con su propia configuración."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", acct.root)
    monkeypatch.setenv("CC_DASH_NO_UPDATE", "1")
    return acct


def settle(store, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        store.poll(time.time())
        if store.source.settled() and store.sessions:
            return store
        time.sleep(0.1)
    raise AssertionError("la máquina remota no respondió")


def scene(acct):
    tr = (Transcript().prompt("compila").tool("t1", "Bash", command="make")
          .tool("ag1", "Agent", subagent_type="Explore", description="buscar", prompt="…")
          .result("ag1", "hecho", agentId="a1"))   # un subagente que ya terminó: es historia
    acct.write(tr)
    acct.open_session(status="busy", name="remota")
    return tr


def test_agent_sends_everything_first(remote_env):
    scene(remote_env)
    proc = subprocess.Popen(AGENT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first = json.loads(proc.stdout.readline())
    finally:
        proc.kill()
    assert first["v"] == sources.PROTOCOL and first["ready"]
    assert {acct for acct, _ in first["records"]} == {"default"}
    assert SID in first["live"]


def test_remote_sessions_show_up(remote_env):
    scene(remote_env)
    store = model.Store([], sources.Sources([sources.RemoteSource("lab", command=AGENT)]))
    store.ready = True   # el dashboard ya arrancó cuando llega la historia remota
    try:
        settle(store)
        s = store.sessions[SID]
        assert s.account == "default@lab"
        assert store.is_remote(s)
        assert store.state(s) == "busy"
        assert s.tools == 2
        assert store.git_info(s, 1e12, refresh=True) is None   # su carpeta no está en esta máquina
        assert store.alerts == []   # la historia que llega de golpe no avisa
        assert "default@lab" in store.names() and not store.multi
    finally:
        store.source.close()


def test_reconnects_without_duplicates(remote_env, monkeypatch):
    scene(remote_env)
    remote = sources.RemoteSource("lab", command=AGENT)
    store = model.Store([], sources.Sources([remote]))
    try:
        settle(store)
        events = len(store.log)
        remote.proc.kill()
        remote.proc.wait()
        store.poll(time.time())
        assert remote.state == "sin conexión"
        assert store.state(store.sessions[SID]) == "ended"   # sin conexión no se sabe si sigue abierta
        monkeypatch.setattr(sources, "RETRY", 0)
        remote.ready = False
        settle(store)
        assert remote.state == "conectado"
        assert store.sessions[SID].tools == 2   # lo reenviado no se suma dos veces
        assert len(store.log) == events
    finally:
        store.source.close()


def test_other_protocol_version(tmp_path):
    fake = [sys.executable, "-c", "import time; print('{\"v\": 99}', flush=True); time.sleep(5)"]
    remote = sources.RemoteSource("viejo", command=fake)
    store = model.Store([], sources.Sources([remote]))
    try:
        end = time.time() + 10
        while remote.state != "sin conexión" and time.time() < end:
            store.poll(time.time())
            time.sleep(0.1)
        assert "versión distinta" in remote.error
    finally:
        remote.close()


def test_unreachable_host():
    remote = sources.RemoteSource("nadie", command=[sys.executable, "-c", "import sys; sys.exit('sin ruta')"])
    store = model.Store([], sources.Sources([remote]))
    end = time.time() + 10
    while remote.state != "sin conexión" and time.time() < end:
        store.poll(time.time())
        time.sleep(0.1)
    assert remote.state == "sin conexión"
    assert store.source.settled()


def test_remote_command(tmp_path, acct):
    assert "ninguna" in cc(tmp_path, acct, "remote").stdout
    assert cc(tmp_path, acct, "remote", "add", "laptop").returncode == 0
    assert cc(tmp_path, acct, "remote", "list").stdout.split() == ["laptop"]
    with open(tmp_path / "config" / "cc-dash" / "config.toml") as fh:
        assert 'remotes = ["laptop"]' in fh.read()
    assert cc(tmp_path, acct, "remote", "remove", "laptop").returncode == 0
    assert cc(tmp_path, acct, "remote", "remove", "laptop").returncode != 0


def big_scene():
    """Una sesión con lo que más pesa: archivos leídos enteros, imágenes, razonamiento y tareas."""
    tr = (Transcript().prompt("revisa todo")
          .assistant({"type": "thinking", "thinking": "x" * 50_000},
                     {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/p/a.py"}})
          .result("t1", "línea\n" * 20_000)
          .tool("t2", "Write", file_path="docs/plan.md", content="y" * 100_000).result("t2")
          .tool("t3", "TodoWrite", todos=[{"content": "a", "activeForm": "haciendo a", "status": "in_progress"}])
          .tool("t4", "TaskCreate", subject="migrar").result("t4", "Task #3 created")
          .tool("ag", "Agent", subagent_type="Plan", description="plan", prompt="p" * 9000)
          .result("ag", "el plan", agentId="z1", resolvedModel="claude-sonnet-5-5", extra="z" * 50_000)
          .meta("attachment", attachment={"type": "file", "content": "w" * 80_000})
          .text("listo")
          .turn_end())
    tr.records.append({**tr.records[2], "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "image", "source": {"data": "i" * 200_000}}, {"type": "text", "text": "una imagen"}]}]}})
    return tr


def state(store):
    s = store.sessions[SID]
    return (s.tools, dict(s.tool_counts), s.out_tokens, s.ctx, s.turns, s.todos, s.docs,
            [(i["k"], i.get("label"), i.get("res"), i.get("err")) for i in s.items],
            [(a["type"], a["status"], a["result"], a["model"]) for a in s.agents], [e[2:5] for e in store.log])


def test_slim_keeps_what_the_dashboard_uses():
    tr = big_scene()
    raw, thin = model.Store([]), model.Store([])
    for rec in tr.records:
        raw.ingest(rec, "a")
        thin.ingest(sources.slim(rec), "a")
    assert state(thin) == state(raw)
    before = sum(len(json.dumps(r)) for r in tr.records)
    after = sum(len(json.dumps(sources.slim(r))) for r in tr.records)
    assert after < before / 10


def test_initial_load_goes_in_chunks(monkeypatch):
    monkeypatch.setattr(sources, "CHUNK", 2000)
    batch = sources.Batch(records=[("a", r) for r in big_scene().records], live={})
    msgs = sources.agent_messages(batch, {}, first=True)
    assert len(msgs) > 2
    assert [m.get("ready") for m in msgs] == [None] * (len(msgs) - 1) + [True]
    assert sum(len(m.get("records", ())) for m in msgs) == len(batch.records)


def test_agent_skips_old_transcripts(acct):
    old = acct.write(Transcript(sid="viejo-0000").prompt("antiguo"))
    acct.write(Transcript().prompt("reciente"))
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    src = sources.LocalSource(since=time.time() - sources.AGENT_HISTORY)
    accounts = [type("A", (), {"name": "a", "projects": os.path.join(acct.root, "projects"),
                               "live_dir": os.path.join(acct.root, "sessions")})()]
    sids = {r["sessionId"] for _, r in src.read(time.time(), accounts).records}
    assert sids == {SID}
    with open(old, "a") as fh:   # si la sesión vieja se retoma, lo nuevo sí llega
        fh.write(json.dumps({"type": "user", "sessionId": "viejo-0000", "message": {"content": "sigo"}}) + "\n")
    src.t_tail = 0
    assert {r["sessionId"] for _, r in src.read(time.time(), accounts).records} == {"viejo-0000"}
