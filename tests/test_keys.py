"""El dashboard interactivo en una pseudo-terminal: recorre todas las teclas y sale con q sin fallar."""
import fcntl
import os
import pty
import struct
import subprocess
import sys
import termios
import threading
import time

from builder import SID, SideTranscript, Transcript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESC, TAB, BTAB, ENTER = "\x1b", "\t", "\x1b[Z", "\r"
PGDN, PGUP, LEFT, RIGHT = "\x1b[6~", "\x1b[5~", "\x1b[D", "\x1b[C"
# fuera quedan las teclas que abren programas externos (D, e) o saltan de panel de tmux (Enter en sesiones)
KEYS = [
    "?", "j", "k", ESC,
    ",", "j", "j", "l", "h", ESC,
    "L", "l", "k", "j", "h", ESC,
    "T", "T", "T", "d", "d", "d", "d", "d", "E", "E", "X", "X", "m", "m", "x",
    "j", "k", "a",
    TAB, "j", "k", "g", "g", "G", "l", "h", RIGHT, "r", "p", "F", "F", ESC, "t", "t",
    TAB, "j", "k", "G", ENTER, "j", ESC,
    TAB, "j", "k", "g", "g", "G", PGDN, PGUP, " ", "b",
    BTAB, BTAB, BTAB,
    "s", "j", "k", ENTER, ESC, ESC, "f", "f",
    "/", "a", "b", ENTER, "/", ESC,
    "o", "j", "o", "n", "n", "i", "i",
    "v", "j", "k", TAB, ESC, "c",
]


def scene(acct):
    tr = (Transcript(start=time.time() - 120).prompt("migra la base")
          .tool("t0", "TodoWrite", todos=[{"content": "esquema", "status": "in_progress"}])
          .tool("t1", "Bash", command="make", description="compilar").result("t1", "boom", error=True))
    for n, aid in enumerate(("a1", "a2")):
        tr.tool(f"ag{n}", "Agent", subagent_type="Explore", description=f"tarea {n}", prompt="…")
        tr.result(f"ag{n}", "lanzado", agentId=aid, isAsync=True)
        side = SideTranscript(aid, start=tr.ts).tool(f"{aid}s", "Grep", pattern="x").result(f"{aid}s")
        acct.write_side(SID, side, "Explore", f"ag{n}")
    acct.write(tr.text("sigo"))
    acct.open_session(status="busy", name="teclas")


def test_every_key(tmp_path, acct):
    scene(acct)
    cfg = tmp_path / "config" / "cc-dash"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('viewer = "true"\nviewer_popup = false\neffects = false\nalerts = false\n')
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"),
               CLAUDE_CONFIG_DIR=acct.root, CC_DASH_NO_UPDATE="1", TERM="xterm-256color")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
    # con CC_COV (lo pone check.sh) el proceso también suma a la cobertura
    cov = ["-m", "coverage", "run", "-p", "--source=cc_dash"] if os.environ.get("CC_COV") else []
    proc = subprocess.Popen([sys.executable, *cov, "-m", "cc_dash"], cwd=ROOT, env=env,
                            stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    out = bytearray()

    def drain():
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:
                return
            if not data:
                return
            out.extend(data)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        time.sleep(1.5)
        for key in KEYS:
            assert proc.poll() is None, f"se cerró antes de tiempo:\n{out.decode(errors='replace')[-3000:]}"
            os.write(master, key.encode())
            time.sleep(0.12)
        os.write(master, b"q")
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        reader.join(timeout=2)
        os.close(master)
    text = out.decode(errors="replace")
    assert "Traceback" not in text, text[-3000:]
    assert proc.returncode == 0
