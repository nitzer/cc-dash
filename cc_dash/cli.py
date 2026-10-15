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
from .config import (
    LAYOUTS,
    LIMITS,
    SPEC_MATCH,
    SWITCHES,
    TEXTS,
    config_path,
    load_config,
    resolve_accounts,
    save_config,
)
from .external import open_doc, show_changes, viewer_command
from .hooks import manage_hooks, run_hook
from .sources import LocalSource, RemoteSource, Sources, run_agent
from .text import HOME
from .theme import THEMES, C
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


def parser():
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
    p = sub.add_parser("remote", help="otras máquinas cuyas sesiones se ven aquí, por ssh (add, remove, list)")
    p.add_argument("action", nargs="?", choices=("add", "remove", "list"), default="list")
    p.add_argument("host", nargs="?", help="nombre de ssh de la máquina (como en ~/.ssh/config)")
    sub.add_parser("agent", help="manda las sesiones de esta máquina por la salida estándar (lo usa `remote`)")
    p = sub.add_parser("set", help="cambia un umbral u opción; sin argumentos los lista")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    return ap


def main():
    args = parser().parse_args()
    cfg = load_config()
    COMMANDS.get(args.cmd, cmd_dashboard)(args, cfg)


def directory(path):
    """Ruta absoluta de un directorio que tiene que existir."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        sys.exit(f"cc-dash: {path} no es un directorio")
    return path


def known_accounts(cfg, names):
    """Las cuentas detectadas; sale con error si `names` nombra alguna que no existe."""
    accounts = resolve_accounts(cfg)
    known = {a.name for a in accounts}
    missing = [n for n in names if n not in known]
    if missing:
        sys.exit(f"cc-dash: cuenta desconocida: {', '.join(missing)} (hay: {', '.join(sorted(known)) or 'ninguna'})")
    return accounts


def cmd_dashboard(args, cfg):
    accounts = known_accounts(cfg, args.account or ())
    if args.account:
        accounts = [a for a in accounts if a.name in args.account]
    if not accounts:
        sys.exit("cc-dash: no hay cuentas; registra una con `cc-dash add NOMBRE RUTA`")
    if cfg["auto_update"] and not args.no_update and not args.once and sys.stdout.isatty():
        auto_update()
    if args.layout:
        cfg["layout"] = args.layout
    if args.theme:
        cfg["theme"] = args.theme
    source = None
    if cfg["remotes"]:
        source = Sources([LocalSource(), *(RemoteSource(h) for h in cfg["remotes"])])
    try:
        run(accounts, args.once, cfg, source)
    finally:
        if source is not None:
            source.close()


def cmd_agent(args, cfg):
    run_agent([a for a in resolve_accounts(cfg) if a.name not in cfg["disabled"]])


def cmd_remote(args, cfg):
    if args.action == "list":
        for host in cfg["remotes"]:
            print(host)
        if not cfg["remotes"]:
            print("ninguna; añade una con `cc-dash remote add HOST`")
        return
    if not args.host:
        sys.exit(f"cc-dash: falta la máquina; uso: cc-dash remote {args.action} HOST")
    if args.action == "add":
        if args.host not in cfg["remotes"]:
            cfg["remotes"].append(args.host)
        save_config(cfg)
        print(f"máquina '{args.host}' añadida; necesita cc-dash instalado y ssh sin contraseña")
        return
    if args.host not in cfg["remotes"]:
        sys.exit(f"cc-dash: '{args.host}' no está en {config_path()}")
    cfg["remotes"].remove(args.host)
    save_config(cfg)
    print(f"máquina '{args.host}' quitada")


def cmd_config(args, cfg):
    print(config_path())


def cmd_set(args, cfg):
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
    cfg[args.key] = setting_value(args.key, args.value)
    save_config(cfg)
    print(f"{args.key} = {str(cfg[args.key]).lower() if isinstance(cfg[args.key], bool) else cfg[args.key]}")


def setting_value(key, value):
    """El valor de `cc-dash set CLAVE VALOR`, validado según el tipo de la clave."""
    if key in TEXTS:
        value = value.strip()
        if key == "spec_match":
            try:
                re.compile(value)
            except re.error as exc:
                sys.exit(f"cc-dash: spec_match no es una expresión regular válida: {exc}")
            value = value or SPEC_MATCH
        return value
    if key in LIMITS:
        if not value.isdigit():
            sys.exit(f"cc-dash: {key} necesita un número entero mayor o igual que 0")
        return max(1, int(value)) if key == "toast_max" else int(value)
    if key in SWITCHES:
        if value.lower() not in ("true", "false", "on", "off", "si", "sí", "no"):
            sys.exit(f"cc-dash: {key} necesita true o false")
        return value.lower() in ("true", "on", "si", "sí")
    sys.exit(f"cc-dash: clave desconocida '{key}'; `cc-dash set` lista las disponibles")


def cmd_hook(args, cfg):
    if args.action is None:
        run_hook(cfg)
        return
    accounts = resolve_accounts(cfg)
    manage_hooks([a for a in accounts if not args.account or a.name in args.account], args.action)


def cmd_changes(args, cfg):
    show_changes(os.path.abspath(os.path.expanduser(args.file)))


def cmd_view(args, cfg):
    path = os.path.abspath(os.path.expanduser(args.file))
    if not os.path.isfile(path):
        sys.exit(f"cc-dash: {path} no es un archivo")
    why = open_doc(cfg, path)
    if why == "tty":
        sys.exit(subprocess.run(viewer_command(cfg, path), shell=True).returncode)
    if why:
        sys.exit(f"cc-dash: {why}")


def cmd_update(args, cfg):
    update()


def cmd_add(args, cfg):
    path = directory(args.path)
    cfg["account"] = [a for a in cfg["account"] if a["name"] != args.name]
    cfg["account"].append({"name": args.name, "path": path})
    save_config(cfg)
    print(f"cuenta '{args.name}' → {path}")


def cmd_remove(args, cfg):
    kept = [a for a in cfg["account"] if a["name"] != args.name]
    if len(kept) == len(cfg["account"]):
        sys.exit(f"cc-dash: '{args.name}' no está en {config_path()}")
    cfg["account"] = kept
    save_config(cfg)
    print(f"cuenta '{args.name}' quitada")


def cmd_scan(args, cfg):
    path = directory(args.dir)
    if path not in cfg["scan"]:
        cfg["scan"].append(path)
        save_config(cfg)
    print(f"escaneando cuentas en {path}")


def cmd_list_accounts(args, cfg):
    cmd_accounts(resolve_accounts(cfg), cfg["disabled"])


def cmd_toggle_account(args, cfg):
    known_accounts(cfg, [args.name])
    cfg["disabled"] = [n for n in cfg["disabled"] if n != args.name]
    if args.cmd == "disable":
        cfg["disabled"].append(args.name)
    save_config(cfg)
    print(f"cuenta '{args.name}' {'deshabilitada' if args.cmd == 'disable' else 'activa'}")


# subcomando -> qué hace (sin subcomando: el dashboard)
COMMANDS = {
    "config": cmd_config, "set": cmd_set, "hook": cmd_hook, "changes": cmd_changes, "view": cmd_view,
    "update": cmd_update, "add": cmd_add, "remove": cmd_remove, "scan": cmd_scan,
    "accounts": cmd_list_accounts, "disable": cmd_toggle_account, "enable": cmd_toggle_account,
    "agent": cmd_agent, "remote": cmd_remote,
}
