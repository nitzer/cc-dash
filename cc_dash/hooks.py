"""Hook de Claude Code que abre el documento a revisar, y su instalación."""

import json
import os
import re
import shlex
import shutil
import sys

from .config import SPEC_MATCH, data_dir
from .external import open_doc
from .text import is_doc


def turn_docs(transcript, cwd):
    """Los .md que la sesión escribió desde el último mensaje del usuario, el más reciente al final."""
    docs = []
    try:
        with open(transcript, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - 8_000_000))
            if fh.tell():
                fh.readline()   # línea a medias
            for raw in fh:
                if b'"tool_use"' not in raw and b'"user"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if entry.get("isSidechain"):
                    continue
                content = (entry.get("message") or {}).get("content")
                if entry.get("type") == "user":
                    typed = isinstance(content, str) or any(
                        isinstance(b, dict) and b.get("type") == "text" for b in content or [])
                    if typed and not entry.get("isMeta"):
                        docs = []
                    continue
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Write", "Edit", "MultiEdit"):
                        path = (b.get("input") or {}).get("file_path")
                        if is_doc(path):
                            path = os.path.join(cwd or "", path)
                            docs = [p for p in docs if p != path] + [path]
    except OSError:
        pass
    return [p for p in docs if os.path.isfile(p)]


def run_hook(cfg):
    """`cc-dash hook`: Claude Code lo llama al preguntar o al terminar un turno; abre el documento a revisar."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    docs = turn_docs(event.get("transcript_path") or "", event.get("cwd") or "")
    wanted = re.compile(cfg["spec_match"] or SPEC_MATCH, re.I)
    specs = [p for p in docs if wanted.search(p)]
    # al terminar el turno solo se abren las rutas que parecen un spec; ante una pregunta, cualquier .md
    doc = (specs or (docs if event.get("hook_event_name") != "Stop" else []))[-1:]
    if not doc:
        return
    doc = doc[0]
    seen_path = os.path.join(data_dir(), "shown.json")
    try:
        with open(seen_path) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    stamp = os.path.getmtime(doc)
    if seen.get(doc) == stamp:   # esa versión ya se mostró
        return
    why = open_doc(cfg, doc, only_visible=True)
    if why:
        note = f"cc-dash: documento a revisar: {doc}" + ("" if why in ("tty", "hidden") else f" ({why})")
        print(json.dumps({"systemMessage": note}, ensure_ascii=False))
    seen.pop(doc, None)
    seen[doc] = stamp
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(seen_path, "w") as fh:
            json.dump(dict(list(seen.items())[-100:]), fh)
    except OSError:
        pass


def hook_command():
    exe = os.path.abspath(sys.argv[0])
    if os.path.basename(exe) != "cc-dash":
        exe = shutil.which("cc-dash") or f"{shlex.quote(sys.executable)} -m cc_dash"
    return f"{exe} hook"


def is_ours(group):
    return any(re.search(r"cc[-_]dash(\.py)?['\"]? hook$", h.get("command", "")) for h in group.get("hooks", []))


def manage_hooks(accounts, action):
    """Instala, quita o muestra el hook de cc-dash en el settings.json de cada cuenta."""
    ours = {"type": "command", "command": hook_command(), "timeout": 10}
    wanted = {"PreToolUse": {"matcher": "AskUserQuestion", "hooks": [ours]}, "Stop": {"hooks": [ours]}}
    for a in accounts:
        path = os.path.realpath(os.path.join(a.path, "settings.json"))
        try:
            with open(path) as fh:
                settings = json.load(fh)
        except FileNotFoundError:
            settings = {}
        except (OSError, ValueError) as exc:
            print(f"{a.name}: no se pudo leer {path}: {exc}")
            continue
        hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
        has = all(any(is_ours(g) for g in hooks.get(event, [])) for event in wanted)
        if action == "status":
            print(f"{a.name}: {'instalado' if has else 'sin instalar'} · {path}")
            continue
        for event, group in wanted.items():
            kept = [g for g in hooks.get(event, []) if not is_ours(g)]
            if action == "install":
                kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if hooks:
            settings["hooks"] = hooks
        else:
            settings.pop("hooks", None)
        try:
            with open(path + ".cc-dash.tmp", "w") as fh:
                json.dump(settings, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(path + ".cc-dash.tmp", path)
        except OSError as exc:
            print(f"{a.name}: no se pudo escribir {path}: {exc}")
            continue
        print(f"{a.name}: hook {'instalado' if action == 'install' else 'quitado'} · {path}")
