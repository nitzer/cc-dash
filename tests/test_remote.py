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
