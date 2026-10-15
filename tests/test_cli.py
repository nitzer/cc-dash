"""La línea de órdenes, de punta a punta: un proceso aparte con su propia configuración."""
import os
import subprocess
import sys

from builder import Transcript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cc(tmp_path, acct, *args):
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"),
               CLAUDE_CONFIG_DIR=acct.root, CC_DASH_NO_UPDATE="1", COLUMNS="120", LINES="45")
    code = f"import sys, cc_dash; sys.argv = ['cc-dash', *{list(args)!r}]; cc_dash.main()"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=60)


def test_once_prints_a_snapshot(tmp_path, acct):
    acct.write(Transcript().prompt("hola").tool("t1", "Bash", command="ls"))
    acct.open_session(status="busy", name="instantánea")
    res = cc(tmp_path, acct, "--once")
    assert res.returncode == 0, res.stderr
    assert "CLAUDE CODE" in res.stdout
    assert "instantánea" in res.stdout


def test_once_with_each_layout(tmp_path, acct):
    acct.write(Transcript().prompt("hola"))
    for layout in ("lista", "tabla", "tarjetas", "tiempo", "diagrama"):
        res = cc(tmp_path, acct, "--once", "--layout", layout, "--theme", "terminal")
        assert res.returncode == 0, res.stderr


def test_accounts_add_disable(tmp_path, acct):
    other = tmp_path / "otra"
    (other / "projects").mkdir(parents=True)
    assert cc(tmp_path, acct, "add", "trabajo", str(other)).returncode == 0
    out = cc(tmp_path, acct, "accounts").stdout
    assert "trabajo" in out and "default" in out
    assert cc(tmp_path, acct, "disable", "trabajo").returncode == 0
    with open(tmp_path / "config" / "cc-dash" / "config.toml") as fh:
        assert 'disabled = ["trabajo"]' in fh.read()


def test_set_limit(tmp_path, acct):
    res = cc(tmp_path, acct, "set", "stuck_errors", "7")
    assert res.returncode == 0, res.stderr
    with open(tmp_path / "config" / "cc-dash" / "config.toml") as fh:
        assert "stuck_errors = 7" in fh.read()


def test_unknown_account_fails(tmp_path, acct):
    res = cc(tmp_path, acct, "--once", "-a", "nadie")
    assert res.returncode != 0
    assert "cuenta desconocida" in res.stderr


def test_scan_remove_config_version(tmp_path, acct):
    (tmp_path / "varias" / "uno" / "projects").mkdir(parents=True)
    assert cc(tmp_path, acct, "scan", str(tmp_path / "varias")).returncode == 0
    assert "uno" in cc(tmp_path, acct, "accounts").stdout
    assert cc(tmp_path, acct, "add", "extra", str(tmp_path / "varias" / "uno")).returncode == 0
    assert cc(tmp_path, acct, "remove", "extra").returncode == 0
    assert "config.toml" in cc(tmp_path, acct, "config").stdout
    assert cc(tmp_path, acct, "--version").stdout.startswith("cc-dash ")


def test_set_lists_and_rejects(tmp_path, acct):
    listing = cc(tmp_path, acct, "set")
    assert listing.returncode == 0 and "stuck_errors" in listing.stdout
    assert cc(tmp_path, acct, "set", "nada", "1").returncode != 0
    assert cc(tmp_path, acct, "set", "toasts", "false").returncode == 0


def test_changes_without_terminal(tmp_path, acct):
    f = tmp_path / "x.py"
    f.write_text("print(1)\n")
    res = cc(tmp_path, acct, "changes", str(f))
    assert res.returncode == 0, res.stderr
    assert "print" in res.stdout
