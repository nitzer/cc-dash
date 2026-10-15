"""Cada diseño, tema y ventana se dibuja sin fallar, a varios tamaños de terminal."""
import io
import time

import pytest
from builder import SID, SideTranscript, Transcript
from rich.console import Console

from cc_dash import config, screen, theme

LAYOUTS = ("lista", "tabla", "tarjetas", "tiempo", "diagrama")
SIZES = ((80, 24), (120, 45), (160, 60))


@pytest.fixture
def scene(acct, load):
    """Una sesión trabajando, con tareas, un documento, un error y tres subagentes en distintos estados."""
    tr = (Transcript(start=time.time() - 300).prompt("migra la base de datos")
          .meta("ai-title", aiTitle="Migrar la base de datos")
          .tool("t0", "TodoWrite", todos=[{"content": "esquema", "activeForm": "migrando esquema",
                                           "status": "in_progress"}, {"content": "datos", "status": "pending"}])
          .tool("t1", "Write", file_path="docs/plan.md", content="x").result("t1")
          .tool("t2", "Bash", command="make", description="compilar").result("t2", "boom", error=True))
    for n, (kind, aid) in enumerate((("Explore", "a1"), ("Plan", "a2"), ("code-reviewer", "a3"))):
        tr.tool(f"ag{n}", "Agent", subagent_type=kind, description=f"tarea {n}", prompt="…")
        tr.result(f"ag{n}", "lanzado", agentId=aid, isAsync=True)
        side = SideTranscript(aid, start=tr.ts)
        for k in range(3 + n * 4):
            side.tool(f"{aid}s{k}", "Grep", pattern=f"p{k}").result(f"{aid}s{k}")
        acct.write_side(SID, side, kind, f"ag{n}")
    tr.meta("queue-operation", operation="enqueue",
            content="<task-notification><task-id>a3</task-id><status>completed</status></task-notification>")
    tr.text("trabajando en ello", ctx=150_000)
    acct.write(tr)
    acct.open_session(status="busy", name="demo")
    store = load()
    store.ready = True
    return store


def draw(store, width, height, ui):
    out = io.StringIO()
    console = Console(file=out, width=width, height=height, force_terminal=True, color_system="truecolor")
    root, _, _ = screen.render(console, store, None, None, time.time(), ui)
    console.print(root)
    return out.getvalue()


def plain(store, width, height, ui):
    out = io.StringIO()
    console = Console(file=out, width=width, height=height, color_system=None)
    console.print(screen.render(console, store, None, None, time.time(), ui)[0])
    return out.getvalue()


def ui_with(**changes):
    ui = screen.new_ui()
    ui["cfg"] = config.load_config()
    ui.update(changes)
    return ui


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("size", SIZES)
def test_layouts(scene, layout, size):
    text = plain(scene, *size, ui_with(layout=layout))
    assert "CLAUDE CODE" in text
    if size[0] >= 120:
        assert "demo" in text


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_themes(scene, name):
    theme.apply_theme(name)
    for layout in LAYOUTS:
        draw(scene, 120, 45, ui_with(layout=layout, theme=name))


@pytest.mark.parametrize("window", [
    {"help": {"top": 0, "rows": 1}},
    {"settings": {"at": 0}},
    {"gallery": {"face": 0, "tier": 4}},
    {"session": {"top": None, "rows": 1, "last": 0, "pane": "list"}},
    {"log": {"cur": None, "scroll": 0, "rows": 1, "pane": "list"}},
    {"tasks": True},
    {"news": True},
    {"info": True},
    {"focus": True, "sel": "ag0"},
    {"toasts": [{"born": None, "kind": k, "who": "demo", "text": "algo pasó"} for k in theme.NEWS]},
])
def test_windows(scene, window):
    for toast in window.get("toasts", ()):
        toast["born"] = time.time()
    for size in SIZES:
        draw(scene, *size, ui_with(layout="diagrama", **window))


def test_log_entry_window(scene):
    for e in scene.log:
        draw(scene, 120, 45, ui_with(entry={"e": e, "scroll": 0, "rows": 1, "pane": "out"}))


def test_gallery_hides_undiscovered_evolutions(scene):
    text = plain(scene, 120, 45, ui_with(gallery={"face": 0, "tier": 4}))
    assert "aún sin descubrir" in text
    assert "LV 10" not in text


def test_empty_dashboard(acct, load):
    text = plain(load(), 120, 45, ui_with())
    assert "CLAUDE CODE" in text


def test_side_panel_notes_hidden_sessions(scene, acct, load):
    acct.write(Transcript(sid="0" * 8 + SID[8:], start=time.time() - 30 * 3600).prompt("vieja"))
    store = load()
    text = plain(store, 140, 45, ui_with())
    assert "+1 inactiva hace +12h · t" in text
    shown = plain(store, 140, 45, ui_with(all=True))
    assert "inactiva" not in shown


def test_log_shows_only_the_chosen_session_by_default(scene, acct, load):
    acct.write(Transcript(sid="7" * 8 + SID[8:], start=time.time() - 30).prompt("mensaje de otra sesión"))
    store = load()
    assert "mensaje de otra sesión" not in plain(store, 140, 45, ui_with())
    assert "mensaje de otra sesión" in plain(store, 140, 45, ui_with(only=False))
