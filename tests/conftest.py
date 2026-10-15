import time

import pytest

import cc_dash as c
from builder import Account


@pytest.fixture(autouse=True)
def defaults(tmp_path, monkeypatch):
    """Cada test arranca con el tema y los umbrales por defecto y sin tocar la configuración real."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    c.apply_theme("clasico")
    c.apply_limits(c.load_config())
    yield
    c.apply_theme("clasico")
    c.apply_limits(c.load_config())


@pytest.fixture
def acct(tmp_path):
    return Account(tmp_path / "cuenta")


@pytest.fixture
def load(acct):
    """Lee la cuenta como lo hace el dashboard; llamarla otra vez lee lo nuevo."""
    store = c.Store([c.Account("prueba", acct.root)])
    clock = [time.time()]

    def poll():
        clock[0] += 5   # pasado el intervalo de cada lectura
        store.poll(clock[0])
        return store

    return poll
