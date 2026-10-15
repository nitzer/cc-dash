"""Actualización desde el clon de git con el que se instaló."""

import os
import subprocess
import sys
import time

from . import __version__
from .config import data_dir
from .repo import git


UPDATE_EVERY = 6 * 3600  # segundos entre comprobaciones automáticas


def source_dir():
    """Clon de git desde el que se instaló (lo anota install.sh)."""
    try:
        with open(os.path.join(data_dir(), "source")) as fh:
            src = fh.read().strip()
    except OSError:
        return None
    return src if os.path.isdir(os.path.join(src, ".git")) else None


def update(quiet=False):
    """Trae cambios del repo y reinstala. Devuelve True si instaló algo nuevo."""
    def say(msg, error=False):
        if error and not quiet:
            sys.exit(f"cc-dash: {msg}")
        if not error:
            print(f"cc-dash: {msg}")
        return False

    src = source_dir()
    if not src:
        return say("no hay un clon de git registrado; instala con ./install.sh desde un clon del repo", True)
    try:
        if git(src, "fetch", "--quiet").returncode:
            return say(f"no se pudo consultar el repositorio remoto de {src}", True)
        behind = git(src, "rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if behind in ("", "0"):
            return False if quiet else say(f"ya está al día ({__version__})")
        say(f"{behind} cambio(s) nuevo(s), actualizando…")
        pull = git(src, "pull", "--ff-only", "--quiet")
        if pull.returncode:
            return say(f"no se pudo actualizar {src} (¿cambios locales?): {pull.stderr.strip()}", True)
        inst = subprocess.run([os.path.join(src, "install.sh")], capture_output=True, text=True, timeout=600)
        if inst.returncode:
            return say(f"falló la reinstalación: {(inst.stderr or inst.stdout).strip()}", True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return say(f"no se pudo actualizar: {exc}", True)
    head = git(src, "log", "-1", "--format=%h %s").stdout.strip()
    say(f"actualizado a {head}")
    return True


def auto_update():
    """Comprueba como mucho cada UPDATE_EVERY; si actualiza, relanza el programa."""
    if os.environ.get("CC_DASH_NO_UPDATE") or not source_dir():
        return
    stamp = os.path.join(data_dir(), "last-check")
    try:
        if time.time() - os.path.getmtime(stamp) < UPDATE_EVERY:
            return
    except OSError:
        pass
    try:
        with open(stamp, "w"):
            pass
    except OSError:
        return
    if update(quiet=True):
        os.environ["CC_DASH_NO_UPDATE"] = "1"
        os.execv(sys.argv[0], sys.argv)
