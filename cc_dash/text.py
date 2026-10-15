"""Utilidades de texto puras: fechas, recortes, etiquetas de herramientas."""

import json
import os
from datetime import datetime


HOME = os.path.expanduser("~")


SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def parse_ts(s):
    if not isinstance(s, str):
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def trunc(s, n):
    s = " ".join(str(s).split())
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def age(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def clock(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def ktok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def short_model(m):
    return (m or "?").replace("claude-", "")


def short_tool(name):
    """mcp__servidor__herramienta -> herramienta"""
    return name.split("__")[-1] if name.startswith("mcp__") else name


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "\n… (recortado)"


def clip_input(inp):
    """Parámetros de una herramienta como texto, acotados para no guardar archivos enteros."""
    if not isinstance(inp, dict):
        return {}
    return {k: clip(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False), 3000) for k, v in inp.items()}


def block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def tool_label(name, inp):
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return inp.get("description") or inp.get("command") or ""
    if name == "TaskCreate":
        return inp.get("subject") or ""
    if name == "TaskUpdate":
        return f"#{inp.get('taskId', '?')}" + (f" → {inp['status']}" if inp.get("status") else "")
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return os.path.basename(inp.get("file_path") or inp.get("notebook_path") or "")
    for key in ("description", "pattern", "query", "url", "skill", "prompt"):
        if inp.get(key):
            return inp[key]
    return ""


def short_path(path, cwd, n):
    """Ruta relativa a la carpeta de la sesión; si no cabe, se recorta por la izquierda."""
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        path = path[len(cwd.rstrip("/")) + 1:]
    path = path.replace(HOME, "~")
    return path if len(path) <= n else "…" + path[-(n - 1):]


def is_doc(path):
    return isinstance(path, str) and path.lower().endswith((".md", ".markdown", ".mdx"))
