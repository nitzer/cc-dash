"""Línea de órdenes: subcomandos y arranque."""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

from rich import box
from rich.console import Console
from rich.table import Table

from . import __version__
from .app import run
from .config import LAYOUTS, LIMITS, SPEC_MATCH, SWITCHES, TEXTS, config_path, load_config, resolve_accounts, save_config
from .external import open_doc, show_changes, viewer_command
from .hooks import manage_hooks, run_hook
from .text import HOME
from .theme import C, THEMES
from .update import auto_update, update


def cmd_accounts(accounts, disabled=()):
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {C.LEAD}")
    for col in ("cuenta", "estado", "ruta", "email", "abiertas", "transcripts"):
        table.add_column(col)
    for a in accounts:
        ok = os.path.isdir(a.path)
        open_ = len(glob.glob(os.path.join(a.live_dir, "*.json")))
        total = len(glob.glob(os.path.join(a.projects, "*", "*.jsonl")))
        table.add_row(a.name, "deshabilitada" if a.name in disabled else "activa",
                      a.path.replace(HOME, "~") + ("" if ok else "  (no existe)"),
                      a.email() or "—", str(open_), str(total))
    console = Console()
    console.print(table)
    console.print(f"config: {config_path()}", style=C.DIM)


def main():
    ap = argparse.ArgumentParser(prog="cc-dash", description="Dashboard en vivo de sesiones de Claude Code.")
    ap.add_argument("--version", action="version", version=f"cc-dash {__version__}")
    ap.add_argument("--once", action="store_true", help="imprime un solo fotograma y sale")
    ap.add_argument("--no-update", action="store_true", help="no busca actualizaciones al arrancar")
    ap.add_argument("--layout", choices=LAYOUTS, help="diseño del panel de agentes para esta ejecución")
    ap.add_argument("--theme", choices=list(THEMES), help="tema de colores para esta ejecución")
    ap.add_argument("-a", "--account", action="append", metavar="CUENTA",
                    help="muestra solo esta cuenta (repetible)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("accounts", help="lista las cuentas configuradas y detectadas")
    for verb, text in (("disable", "deja de leer y mostrar una cuenta"), ("enable", "vuelve a mostrar una cuenta")):
        p = sub.add_parser(verb, help=text)
        p.add_argument("name")
    p = sub.add_parser("add", help="registra una cuenta")
    p.add_argument("name")
    p.add_argument("path", help="directorio de configuración de Claude Code (su CLAUDE_CONFIG_DIR)")
    p = sub.add_parser("remove", help="quita una cuenta registrada")
    p.add_argument("name")
    p = sub.add_parser("scan", help="registra una carpeta cuyas subcarpetas son cuentas")
    p.add_argument("dir")
    sub.add_parser("config", help="muestra la ruta del archivo de configuración")
    sub.add_parser("update", help="trae los cambios del repo y reinstala")
    p = sub.add_parser("view", help="abre un archivo con el visor configurado")
    p.add_argument("file")
    p = sub.add_parser("changes", help="muestra un archivo con colores y lo que cambió según git resaltado")
    p.add_argument("file")
    p = sub.add_parser("hook", help="hook de Claude Code que abre el documento a revisar (install, remove, status)")
    p.add_argument("action", nargs="?", choices=("install", "remove", "status"))
    p = sub.add_parser("set", help="cambia un umbral u opción; sin argumentos los lista")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "config":
        print(config_path())
        return
    if args.cmd == "set":
        if args.key is None:
            for key, (default, what) in LIMITS.items():
                print(f"{key:<14} {cfg[key]:<6} {what} (por defecto {default})")
            for key, what in SWITCHES.items():
                print(f"{key:<14} {'true' if cfg[key] else 'false':<6} {what}")
            for key, (_, what) in TEXTS.items():
                print(f"{key:<14} {json.dumps(cfg[key], ensure_ascii=False)}  {what}")
            return
        if args.value is None:
            sys.exit("cc-dash: falta el valor; uso: cc-dash set CLAVE VALOR")
        if args.key in TEXTS:
            cfg[args.key] = args.value.strip()
            if args.key == "spec_match":
                try:
                    re.compile(cfg[args.key])
                except re.error as exc:
                    sys.exit(f"cc-dash: spec_match no es una expresión regular válida: {exc}")
                cfg[args.key] = cfg[args.key] or SPEC_MATCH
        elif args.key in LIMITS:
            if not args.value.isdigit():
                sys.exit(f"cc-dash: {args.key} necesita un número entero mayor o igual que 0")
            cfg[args.key] = max(1, int(args.value)) if args.key == "toast_max" else int(args.value)
        elif args.key in SWITCHES:
            if args.value.lower() not in ("true", "false", "on", "off", "si", "sí", "no"):
                sys.exit(f"cc-dash: {args.key} necesita true o false")
            cfg[args.key] = args.value.lower() in ("true", "on", "si", "sí")
        else:
            sys.exit(f"cc-dash: clave desconocida '{args.key}'; `cc-dash set` lista las disponibles")
        save_config(cfg)
        print(f"{args.key} = {str(cfg[args.key]).lower() if isinstance(cfg[args.key], bool) else cfg[args.key]}")
        return
    if args.cmd == "hook" and args.action is None:
        run_hook(cfg)
        return
    if args.cmd == "changes":
        show_changes(os.path.abspath(os.path.expanduser(args.file)))
        return
    if args.cmd == "view":
        path = os.path.abspath(os.path.expanduser(args.file))
        if not os.path.isfile(path):
            sys.exit(f"cc-dash: {path} no es un archivo")
        why = open_doc(cfg, path)
        if why == "tty":
            sys.exit(subprocess.run(viewer_command(cfg, path), shell=True).returncode)
        if why:
            sys.exit(f"cc-dash: {why}")
        return
    if args.cmd == "update":
        update()
        return
    if args.cmd == "add":
        path = os.path.abspath(os.path.expanduser(args.path))
        if not os.path.isdir(path):
            sys.exit(f"cc-dash: {path} no es un directorio")
        cfg["account"] = [a for a in cfg["account"] if a["name"] != args.name]
        cfg["account"].append({"name": args.name, "path": path})
        save_config(cfg)
        print(f"cuenta '{args.name}' → {path}")
        return
    if args.cmd == "remove":
        kept = [a for a in cfg["account"] if a["name"] != args.name]
        if len(kept) == len(cfg["account"]):
            sys.exit(f"cc-dash: '{args.name}' no está en {config_path()}")
        cfg["account"] = kept
        save_config(cfg)
        print(f"cuenta '{args.name}' quitada")
        return
    if args.cmd == "scan":
        path = os.path.abspath(os.path.expanduser(args.dir))
        if not os.path.isdir(path):
            sys.exit(f"cc-dash: {path} no es un directorio")
        if path not in cfg["scan"]:
            cfg["scan"].append(path)
            save_config(cfg)
        print(f"escaneando cuentas en {path}")
        return

    accounts = resolve_accounts(cfg)
    if args.cmd == "accounts":
        cmd_accounts(accounts, cfg["disabled"])
        return
    if args.cmd == "hook":
        chosen = [a for a in accounts if not args.account or a.name in args.account]
        manage_hooks(chosen, args.action)
        return
    if args.cmd in ("disable", "enable"):
        if args.name not in {a.name for a in accounts}:
            sys.exit(f"cc-dash: cuenta desconocida: {args.name} (hay: {', '.join(a.name for a in accounts) or 'ninguna'})")
        cfg["disabled"] = [n for n in cfg["disabled"] if n != args.name]
        if args.cmd == "disable":
            cfg["disabled"].append(args.name)
        save_config(cfg)
        print(f"cuenta '{args.name}' {'deshabilitada' if args.cmd == 'disable' else 'activa'}")
        return
    if args.account:
        known = {a.name for a in accounts}
        missing = [n for n in args.account if n not in known]
        if missing:
            sys.exit(f"cc-dash: cuenta desconocida: {', '.join(missing)} (hay: {', '.join(sorted(known)) or 'ninguna'})")
        accounts = [a for a in accounts if a.name in args.account]
    if not accounts:
        sys.exit("cc-dash: no hay cuentas; registra una con `cc-dash add NOMBRE RUTA`")
    if cfg["auto_update"] and not args.no_update and not args.once and sys.stdout.isatty():
        auto_update()
    if args.layout:
        cfg["layout"] = args.layout
    if args.theme:
        cfg["theme"] = args.theme
    run(accounts, args.once, cfg)
