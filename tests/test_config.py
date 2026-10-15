"""Configuración: valores por defecto, ida y vuelta a disco y valores inválidos."""
import cc_dash as c


def test_defaults_without_file():
    cfg = c.load_config()
    assert cfg["layout"] == "lista" and cfg["theme"] == "clasico"
    assert cfg["stuck_errors"] == 3 and cfg["toast_max"] == 4


def test_roundtrip():
    cfg = c.load_config()
    cfg.update(layout="diagrama", theme="catppuccin-mocha", alerts=False, stuck_tool=99, viewer="glow -p",
               account=[{"name": "trabajo", "path": "/srv/trabajo"}], disabled=["trabajo"])
    c.save_config(cfg)
    back = c.load_config()
    for key in ("layout", "theme", "alerts", "stuck_tool", "viewer", "account", "disabled"):
        assert back[key] == cfg[key], key


def test_invalid_values_fall_back():
    path = c.config_path()
    c.save_config(c.load_config())
    with open(path) as fh:
        text = fh.read()
    text = (text.replace('theme = "clasico"', 'theme = "nope"').replace('layout = "lista"', 'layout = 3')
            .replace("stuck_errors = 3", "stuck_errors = -1")
            .replace('spec_match = "', 'spec_match = "(['))
    with open(path, "w") as fh:
        fh.write(text)
    cfg = c.load_config()
    assert cfg["theme"] == "clasico" and cfg["layout"] == "lista"
    assert cfg["stuck_errors"] == 3
    assert cfg["spec_match"] == c.SPEC_MATCH


def test_accounts_resolution(tmp_path, monkeypatch):
    (tmp_path / "scan" / "a" / "projects").mkdir(parents=True)
    (tmp_path / "scan" / "b").mkdir(parents=True)   # sin projects ni .claude.json: no es cuenta
    (tmp_path / "default" / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "default"))
    cfg = {**c.load_config(), "scan": [str(tmp_path / "scan")],
           "account": [{"name": "a", "path": str(tmp_path / "default")}]}
    names = [a.name for a in c.resolve_accounts(cfg)]
    assert names == ["a", "a-2"]   # la misma carpeta no se repite; el nombre repetido se numera
