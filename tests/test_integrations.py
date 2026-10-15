"""Lo que toca el exterior: git, los hooks de Claude Code y los documentos que escribe una sesión."""
import json
import os
import subprocess

import pytest

import cc_dash as c
from builder import Transcript
from test_cli import cc


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "git", "init", "-q")
    sh(root, "git", "config", "user.email", "t@t")
    sh(root, "git", "config", "user.name", "t")
    (root / "a.py").write_text("uno\ndos\ntres\ncuatro\n")
    sh(root, "git", "add", ".")
    sh(root, "git", "commit", "-qm", "inicio")
    return root


def test_uncommitted_changes_by_line(repo):
    path = str(repo / "a.py")
    (repo / "a.py").write_text("uno\nDOS\ntres\nnueva\n")   # cambia la 2, borra cuatro y añade nueva
    against, note = c.change_source(path)
    assert against == ["HEAD"] and note == "cambios sin commitear"
    added, modified, removed = c.changed_lines(path, against)
    assert modified == {2, 4}
    assert removed == {2: ["dos"], 4: ["cuatro"]}   # el texto de antes de cada línea cambiada
    (repo / "a.py").write_text("uno\ndos\ntres\ncuatro\ncinco\n")
    added, modified, removed = c.changed_lines(path, c.change_source(path)[0])
    assert added == {5} and not modified
    (repo / "a.py").write_text("uno\ntres\ncuatro\n")
    added, modified, removed = c.changed_lines(path, c.change_source(path)[0])
    assert removed == {2: ["dos"]}


def test_change_source_cases(repo, tmp_path):
    assert c.change_source(str(repo / "a.py"))[1].startswith("creado en el primer commit")
    (repo / "b.py").write_text("x\n")
    assert c.change_source(str(repo / "b.py")) == ([], "archivo nuevo, sin seguimiento")
    (repo / "a.py").write_text("otro\n")
    sh(repo, "git", "commit", "-qam", "cambio")
    against, note = c.change_source(str(repo / "a.py"))
    assert len(against) == 2 and note.startswith("sin cambios pendientes")
    outside = tmp_path / "suelto.py"
    outside.write_text("x")
    assert c.change_source(str(outside)) == (None, "no está en un repositorio git")


def test_git_info(repo, acct, load):
    acct.write(Transcript(cwd=str(repo)).prompt("hola"))
    (repo / "a.py").write_text("cambio\n")
    (repo / "nuevo.txt").write_text("x")
    store = load()
    s = next(iter(store.sessions.values()))
    info = store.git_info(s, 1e12, refresh=True)
    assert info["changed"] == 1 and info["untracked"] == 1
    assert info["branch"]


def test_turn_docs_since_last_prompt(tmp_path, acct):
    cwd = tmp_path / "proyecto"
    (cwd / "docs").mkdir(parents=True)
    for name in ("viejo.md", "spec.md", "notas.md"):
        (cwd / "docs" / name).write_text("x")
    tr = (Transcript(cwd=str(cwd)).prompt("primero").tool("t1", "Write", file_path="docs/viejo.md")
          .prompt("segundo").tool("t2", "Write", file_path="docs/spec.md")
          .tool("t3", "Edit", file_path="docs/notas.md").tool("t4", "Write", file_path="src/x.py"))
    path = acct.write(tr)
    assert c.turn_docs(path, str(cwd)) == [str(cwd / "docs" / "spec.md"), str(cwd / "docs" / "notas.md")]


def test_hook_install_status_remove(tmp_path, acct):
    settings = os.path.join(acct.root, "settings.json")
    with open(settings, "w") as fh:
        json.dump({"theme": "dark", "hooks": {"PreToolUse": [{"matcher": "Grep", "hooks": [
            {"type": "command", "command": "otro"}]}]}}, fh)
    res = cc(tmp_path, acct, "hook", "install")
    assert res.returncode == 0, res.stderr
    with open(settings) as fh:
        data = json.load(fh)
    assert data["theme"] == "dark"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "otro"   # lo ajeno no se toca
    ours = [g for groups in data["hooks"].values() for g in groups if c.is_ours(g)]
    assert ours
    assert cc(tmp_path, acct, "hook", "status").returncode == 0
    assert cc(tmp_path, acct, "hook", "remove").returncode == 0
    with open(settings) as fh:
        data = json.load(fh)
    assert not [g for groups in data["hooks"].values() for g in groups if c.is_ours(g)]
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "otro"


def test_hook_reports_new_spec(tmp_path, acct):
    cwd = tmp_path / "proyecto"
    (cwd / "docs").mkdir(parents=True)
    (cwd / "docs" / "login-spec.md").write_text("# spec")
    path = acct.write(Transcript(cwd=str(cwd)).prompt("haz el spec")
                      .tool("t1", "Write", file_path="docs/login-spec.md"))
    event = json.dumps({"hook_event_name": "Stop", "transcript_path": path, "cwd": str(cwd)})
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"),
               CLAUDE_CONFIG_DIR=acct.root, CC_DASH_NO_UPDATE="1")
    env.pop("TMUX", None)
    code = "import sys, cc_dash; sys.argv = ['cc-dash', 'hook']; cc_dash.main()"
    first = subprocess.run([c.sys.executable, "-c", code], input=event, env=env, capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=30)
    assert first.returncode == 0, first.stderr
    assert "login-spec.md" in first.stdout
    again = subprocess.run([c.sys.executable, "-c", code], input=event, env=env, capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=30)
    assert again.stdout == ""   # la misma versión no se vuelve a mostrar
