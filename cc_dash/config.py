"""Cuentas y configuración (~/.config/cc-dash/config.toml) y los umbrales vigentes."""

import json
import os
import re
import sys
import tomllib

from .text import HOME
from .theme import THEMES

DEFAULT_DIR = os.path.join(HOME, ".claude")


class Account:
    """Una cuenta = un directorio de configuración de Claude Code."""

    def __init__(self, name, path):
        self.name = name
        self.path = os.path.abspath(os.path.expanduser(path))
        self.projects = os.path.join(self.path, "projects")
        self.live_dir = os.path.join(self.path, "sessions")

    def email(self):
        candidates = [os.path.join(self.path, ".claude.json")]
        if self.path == DEFAULT_DIR:
            candidates.append(DEFAULT_DIR + ".json")
        for path in candidates:
            try:
                with open(path) as fh:
                    return (json.load(fh).get("oauthAccount") or {}).get("emailAddress") or ""
            except (OSError, ValueError, AttributeError):
                continue
        return ""


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
    return os.path.join(base, "cc-dash", "config.toml")


# umbral -> (valor por defecto, qué es)
LIMITS = {
    "stuck_errors": (3, "errores seguidos para sospechar un atasco (0 = no mirar)"),
    "stuck_repeat": (4, "veces seguidas la misma llamada (0 = no mirar)"),
    "stuck_tool": (300, "segundos que una herramienta puede tardar en volver (0 = no mirar)"),
    "stuck_silent": (180, "segundos trabajando sin actividad (0 = no mirar)"),
    "hide_after": (60, "segundos que un subagente terminado sigue a la vista"),
    "toast_seconds": (8, "segundos que dura un toast"),
    "toast_max": (4, "toasts visibles a la vez"),
}
# los umbrales vigentes; apply_limits los cambia en su sitio
limits = {key: default for key, (default, _) in LIMITS.items()}
SWITCHES = {"toasts": "mostrar toasts (tecla X)", "alerts": "pitido y aviso de tmux (tecla m)",
            "effects": "arranque, glitch y lluvia (tecla E)", "auto_update": "actualizarse al arrancar",
            "viewer_popup": "abrir los documentos en un popup de tmux (false: lanzar el visor tal cual)"}
# visores que se prueban, en orden, cuando `viewer` está vacío
VIEWERS = ("glow -p", "bat --paging=always", "batcat --paging=always", "less -R", "more")
SPEC_MATCH = "spec|plan|design|dise[ñn]o|prd|rfc|propuesta|proposal"
TEXTS = {
    "viewer": ("", "programa que abre los .md, p. ej. \"glow -p\" o \"nvim -R {file}\" (vacío = automático)"),
    "spec_match": (SPEC_MATCH, "regex: rutas de .md que se abren solas al terminar un turno"),
}


def apply_limits(cfg):
    """Pasa los umbrales de la configuración a los que usa el dashboard."""
    limits.update({key: cfg[key] for key in LIMITS})


def limits_default():
    return {key: default for key, (default, _) in LIMITS.items()}


def load_config():
    cfg = {"account": [], "scan": [], "remotes": [], "include_default": True, "auto_update": True, "layout": "lista",
           "theme": "clasico", "alerts": True, "effects": True, "toasts": True, "disabled": [],
           "viewer_popup": True, **{key: default for key, (default, _) in TEXTS.items()},
           **limits_default()}
    try:
        with open(config_path(), "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"cc-dash: no se pudo leer {config_path()}: {exc}")
    cfg["account"] = [a for a in raw.get("account", []) if a.get("name") and a.get("path")]
    cfg["scan"] = [d for d in raw.get("scan", []) if isinstance(d, str)]
    cfg["remotes"] = [h for h in raw.get("remotes", []) if isinstance(h, str) and h.strip()]
    cfg["disabled"] = [n for n in raw.get("disabled", []) if isinstance(n, str)]
    cfg["include_default"] = bool(raw.get("include_default", True))
    cfg["auto_update"] = bool(raw.get("auto_update", True))
    cfg["layout"] = raw.get("layout") if raw.get("layout") in LAYOUTS else "lista"
    cfg["theme"] = raw.get("theme") if raw.get("theme") in THEMES else "clasico"
    cfg["alerts"] = bool(raw.get("alerts", True))
    cfg["effects"] = bool(raw.get("effects", True))
    cfg["toasts"] = bool(raw.get("toasts", True))
    cfg["viewer_popup"] = bool(raw.get("viewer_popup", True))
    for key in TEXTS:
        if isinstance(raw.get(key), str):
            cfg[key] = raw[key].strip()
    try:
        re.compile(cfg["spec_match"] or SPEC_MATCH)
    except re.error:
        cfg["spec_match"] = SPEC_MATCH
    for key in LIMITS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            cfg[key] = int(value)
    cfg["toast_max"] = max(1, cfg["toast_max"])
    return cfg


def save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = [
        "# cc-dash — cuentas de Claude Code a mostrar.",
        "# include_default: añade ~/.claude (o $CLAUDE_CONFIG_DIR) si no está listada.",
        "# scan: carpetas cuyas subcarpetas son cada una una cuenta.",
        "# auto_update: al abrir el dashboard, busca cambios en el repo y se reinstala.",
        f"include_default = {'true' if cfg['include_default'] else 'false'}",
        f"auto_update = {'true' if cfg['auto_update'] else 'false'}",
        "# layout: diseño del panel de agentes (lista, tabla, tarjetas, tiempo, diagrama); tecla d.",
        f"layout = {json.dumps(cfg['layout'])}",
        "# theme: clasico, neon, matrix, ambar, catppuccin-mocha/-macchiato/-frappe/-latte o terminal"
        " (los colores de tu shell); tecla T. alerts: pitido y aviso de tmux (tecla m).",
        f"theme = {json.dumps(cfg['theme'])}",
        f"alerts = {'true' if cfg['alerts'] else 'false'}",
        "# effects: arranque animado, glitch en los avisos y lluvia en reposo (tecla E).",
        f"effects = {'true' if cfg['effects'] else 'false'}",
        "# toasts: novedades como recuadros en la esquina (tecla X); el registro (n) sigue igual.",
        f"toasts = {'true' if cfg['toasts'] else 'false'}",
        "# viewer: programa que abre los .md de una sesión (tecla D y `cc-dash hook`); {file} es la ruta",
        "# y, si no aparece, se añade al final. Vacío: el primero instalado de glow, bat, less.",
        f"viewer = {json.dumps(cfg['viewer'], ensure_ascii=False)}",
        "# viewer_popup: lo abre en un popup de tmux; false lanza la orden tal cual (visores con ventana propia).",
        f"viewer_popup = {'true' if cfg['viewer_popup'] else 'false'}",
        "# spec_match: regex sobre la ruta; los .md que la cumplen se abren solos cuando el turno termina.",
        f"spec_match = {json.dumps(cfg['spec_match'], ensure_ascii=False)}",
        "# Umbrales. Se pueden cambiar aquí o con `cc-dash set CLAVE VALOR`; el dashboard los relee solo.",
        *[f"{key} = {cfg[key]}".ljust(22) + f"# {what}" for key, (_, what) in LIMITS.items()],
        "scan = [" + ", ".join(json.dumps(d) for d in cfg["scan"]) + "]",
        "# remotes: otras máquinas (nombres de ssh) cuyas sesiones se ven aquí; cada una necesita cc-dash",
        "# instalado y acceso por ssh sin contraseña. `cc-dash remote add HOST` las añade.",
        "remotes = [" + ", ".join(json.dumps(h) for h in cfg["remotes"]) + "]",
        "# disabled: cuentas que no se muestran ni se leen (ventana de configuración, o enable/disable).",
        "disabled = [" + ", ".join(json.dumps(n) for n in cfg["disabled"]) + "]",
    ]
    for a in cfg["account"]:
        out += ["", "[[account]]", f"name = {json.dumps(a['name'])}", f"path = {json.dumps(a['path'])}"]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def resolve_accounts(cfg):
    found = [Account(a["name"], a["path"]) for a in cfg["account"]]
    for root in cfg["scan"]:
        root = os.path.expanduser(root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            sub = os.path.join(root, name)
            if os.path.isdir(os.path.join(sub, "projects")) or os.path.isfile(os.path.join(sub, ".claude.json")):
                found.append(Account(name, sub))
    if cfg["include_default"]:
        default = os.environ.get("CLAUDE_CONFIG_DIR") or DEFAULT_DIR
        if os.path.isdir(default):
            found.append(Account("default", default))
    accounts, paths, names = [], set(), set()
    for a in found:
        real = os.path.realpath(a.path)
        if real in paths:
            continue
        paths.add(real)
        base, n = a.name, 2
        while a.name in names:
            a.name = f"{base}-{n}"
            n += 1
        names.add(a.name)
        accounts.append(a)
    return accounts


def data_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), "cc-dash")


LAYOUTS = ("lista", "tabla", "tarjetas", "tiempo", "diagrama")
