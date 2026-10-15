"""Consultas a git: estado de la carpeta de una sesión y líneas cambiadas de un archivo."""

import os
import re
import subprocess


def git(src, *args, timeout=30):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=8")
    return subprocess.run(["git", "-C", src, *args], capture_output=True, text=True, timeout=timeout, env=env)


def change_source(path):
    """Contra qué comparar `path` en git. Devuelve (argumentos de git diff o None, nota).

    Cambios sin commitear; si es nuevo y git no lo sigue, todo es añadido ([]); si no hay
    cambios pendientes, el último commit que lo tocó. (None, motivo) si no se puede."""
    where = os.path.dirname(path) or "."

    def run(*args):
        try:
            return git(where, *args, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None

    top = run("rev-parse", "--show-toplevel")
    if not os.path.isdir(where) or top is None or top.returncode != 0:
        return None, "no está en un repositorio git"
    known = run("ls-files", "--error-unmatch", "--", path)
    if known is None or known.returncode != 0:
        if not os.path.isfile(path):
            return None, "ya no existe y git no lo conoce"
        return [], "archivo nuevo, sin seguimiento"
    pending = run("diff", "--quiet", "HEAD", "--", path)
    if pending is not None and pending.returncode == 1:
        return ["HEAD"], "cambios sin commitear"
    last = run("log", "-1", "--format=%H", "--", path)
    sha = last.stdout.strip() if last is not None else ""
    if not sha:
        return [], "sin historial"
    parent = run("rev-parse", "--verify", "--quiet", f"{sha}^")
    if parent is None or parent.returncode != 0:
        return [], "creado en el primer commit"
    return [f"{sha}^", sha], f"sin cambios pendientes: último commit {sha[:7]}"


def changed_lines(path, against):
    """Líneas añadidas y cambiadas (número en el archivo actual) y las borradas, por posición."""
    added, modified, removed = set(), set(), {}   # removed: línea antes de la que iban -> [texto]
    if not against:
        return added, modified, removed
    where = os.path.dirname(path) or "."
    try:
        out = git(where, "diff", "-U0", "--no-color", *against, "--", path, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return added, modified, removed
    gone, at = [], 0
    for line in out.splitlines():
        hunk = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk:
            if gone:
                removed.setdefault(at, []).extend(gone)
            old_n = int(hunk.group(1) if hunk.group(1) is not None else 1)
            start, new_n = int(hunk.group(2)), int(hunk.group(3) if hunk.group(3) is not None else 1)
            gone, at = [], start if new_n else start + 1
            for n in range(start, start + new_n):
                (modified if old_n else added).add(n)
        elif line.startswith("-") and not line.startswith("---"):
            gone.append(line[1:])
    if gone:
        removed.setdefault(at, []).extend(gone)
    return added, modified, removed


def repo_status(cwd, since):
    """Rama, archivos cambiados y sin seguimiento, y commits desde `since` (epoch) en `cwd`. None si no es git."""
    try:
        status = git(cwd, "--no-optional-locks", "status", "--porcelain=v1", "-b", timeout=3)
        if status.returncode != 0:
            return None
        lines = status.stdout.splitlines()
        branch = lines[0][3:].split("...")[0] if lines and lines[0].startswith("## ") else ""
        files = [ln for ln in lines if not ln.startswith("## ")]
        commits = git(cwd, "rev-list", "--count", f"--since={int(since)}", "HEAD", timeout=3) if since else None
        return {"branch": branch,
                "untracked": sum(1 for ln in files if ln.startswith("??")),
                "changed": sum(1 for ln in files if not ln.startswith("??")),
                "commits": int(commits.stdout.strip() or 0) if commits and commits.returncode == 0 else 0}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
