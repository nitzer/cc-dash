"""Ventana de configuración: ajustes, cuentas y cómo se aplican."""

import shutil

from rich import box
from rich.panel import Panel
from rich.text import Text

from .config import LAYOUTS, VIEWERS, apply_limits, save_config
from .text import HOME, trunc
from .theme import C, THEMES, apply_theme, label


# (clave, nombre, tipo, opciones o paso, valor por defecto, ayuda); None separa secciones
SETTINGS = [
    ("theme", "Tema", "choice", list(THEMES), "clasico", "colores y marco de todo el dashboard"),
    ("layout", "Diseño de agentes", "choice", list(LAYOUTS), "lista", "cómo se dibuja el panel del lead y sus subagentes"),
    ("toasts", "Toasts", "bool", None, True, "novedades como recuadros en la esquina; el registro (n) sigue igual"),
    ("alerts", "Pitido y aviso de tmux", "bool", None, True, "suena y avisa en tmux con las novedades importantes"),
    ("effects", "Efectos visuales", "bool", None, True, "arranque animado, glitch en los avisos y lluvia en reposo"),
    ("auto_update", "Actualizarse al arrancar", "bool", None, True, "busca cambios en el repo al abrir el dashboard"),
    ("viewer", "Visor de documentos", "viewer", None, "", "programa que abre los .md (tecla D); otro: cc-dash set viewer \"ORDEN\""),
    ("viewer_popup", "Visor en popup de tmux", "bool", None, True, "no: lanza el visor tal cual, para programas con ventana propia"),
    None,
    ("stuck_errors", "Errores seguidos", "int", 1, 3, "atasco: resultados con error seguidos (0 = no mirar)"),
    ("stuck_repeat", "Llamadas repetidas", "int", 1, 4, "atasco: veces seguidas la misma llamada (0 = no mirar)"),
    ("stuck_tool", "Herramienta sin volver", "int", 30, 300, "atasco: segundos que puede tardar una herramienta (0 = no mirar)"),
    ("stuck_silent", "Sin actividad", "int", 30, 180, "atasco: segundos trabajando sin escribir nada (0 = no mirar)"),
    None,
    ("hide_after", "Ocultar subagente tras", "int", 10, 60, "segundos que un subagente terminado sigue a la vista"),
    ("toast_seconds", "Duración de un toast", "int", 1, 8, "segundos que un toast sigue en pantalla"),
    ("toast_max", "Toasts a la vez", "int", 1, 4, "cuántos toasts caben apilados"),
]


def setting_entries(store):
    """Filas de la ventana: los ajustes fijos y, debajo, un interruptor por cuenta."""
    rows = [*SETTINGS, None, "Cuentas"]
    for a in store.accounts:
        where = a.path.replace(HOME, "~")
        rows.append((f"account:{a.name}", a.name, "account", None, True,
                     f"{where} · al deshabilitarla no se lee ni se muestra"))
    return rows


def setting_value(cfg, item):
    key = item[0]
    return key.split(":", 1)[1] not in cfg["disabled"] if item[2] == "account" else cfg[key]


def settings_panel(cfg, store, view, width, max_rows):
    """Ventana flotante de configuración: cada cambio se aplica y se guarda al momento."""
    entries = setting_entries(store)
    items = [e for e in entries if isinstance(e, tuple)]
    view["at"] %= len(items)
    chosen = items[view["at"]]
    # si no caben todas las filas, la ventana se desplaza para no perder la elegida
    room = max(5, max_rows - 6)
    at = entries.index(chosen)
    first = max(0, min(at - room // 2, len(entries) - room)) if len(entries) > room else 0
    body = Text(no_wrap=True, overflow="ellipsis")
    name_w = 26
    for item in entries[first: first + room]:
        if item is None:
            body.append("\n")
            continue
        if isinstance(item, str):
            body.append(f" {item.upper()}\n", style=f"bold {C.SWARM}")
            continue
        key, name, kind, _, default, _ = item
        picked = item is chosen
        value = setting_value(cfg, item)
        if kind == "account":
            shown = "activa" if value else "deshabilitada"
        elif kind == "bool":
            shown = "sí" if value else "no"
        elif kind == "viewer":
            shown = value or f"automático ({viewer_choices(cfg)[1] if len(viewer_choices(cfg)) > 1 else 'ninguno instalado'})"
        elif kind == "int" and value == 0 and key.startswith("stuck"):
            shown = "apagado"
        elif kind == "int" and key not in ("stuck_errors", "stuck_repeat", "toast_max"):
            shown = f"{value} s"
        else:
            shown = str(value)
        line = Text(no_wrap=True)
        line.append(" ▸ " if picked else "   ", style=f"bold {C.LEAD}")
        line.append(f"{trunc(name, name_w - 1):<{name_w}}", style="bold" if picked else C.FG)
        line.append("◂ " if picked else "  ", style=C.LEAD)
        color = (C.TOOL if value else C.ERR) if kind in ("bool", "account") else C.SWARM
        line.append(shown, style=f"bold {color}")
        line.append(" ▸" if picked else "", style=C.LEAD)
        if value != default:
            line.append("  ·  modificado", style=C.DIM)
        if picked:
            line.pad_right(max(0, width - 4 - len(line.plain)))
            line.stylize(f"on {C.LINE}")
        body.append_text(line)
        body.append("\n")
    body.append("\n " + trunc(chosen[5], width - 6) + "\n", style=C.DIM)
    body.append(" " + trunc("j/k mover · h/l cambiar · Enter alterna · r por defecto · Esc cerrar", width - 6), style=C.DIM)
    more = len(entries) > room
    return Panel(body, title=label("configuración", "CFG"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {C.LEAD}", style=f"on {C.CARD_BG}", width=width,
                 subtitle=f"fila {view['at'] + 1} de {len(items)} · se guarda al cambiar" if more
                 else "se guarda al cambiar", subtitle_align="right")


def viewer_choices(cfg):
    """Automático, los visores instalados y, si es otro, el que haya en la configuración."""
    found = [v for v in (*VIEWERS, "nvim -R", "vim -R") if shutil.which(v.split()[0])]
    return ["", *found, *([cfg["viewer"]] if cfg["viewer"] and cfg["viewer"] not in found else [])]


def change_setting(cfg, item, step):
    """Mueve un ajuste un paso (step=±1), o lo devuelve a su valor por defecto (step=0)."""
    key, _, kind, extra, default, _ = item
    if kind == "account":
        name = key.split(":", 1)[1]
        if name in cfg["disabled"]:
            cfg["disabled"].remove(name)
        elif step != 0:   # "por defecto" es activa
            cfg["disabled"].append(name)
    elif step == 0:
        cfg[key] = default
    elif kind == "bool":
        cfg[key] = not cfg[key]
    elif kind == "viewer":
        options = viewer_choices(cfg)
        cfg[key] = options[(options.index(cfg[key]) + step) % len(options)]
    elif kind == "choice":
        cfg[key] = extra[(extra.index(cfg[key]) + step) % len(extra)]
    else:
        cfg[key] = max(1 if key == "toast_max" else 0, cfg[key] + step * extra)


def apply_config(cfg, ui, store=None):
    """Lleva la configuración al dashboard en marcha y la guarda."""
    if store is not None:
        store.off = set(cfg["disabled"])
        store.t_glob = store.t_live = 0.0   # que la próxima pasada relea archivos y sesiones
    ui["theme"], ui["layout"] = cfg["theme"], cfg["layout"]
    ui["toasts_on"], ui["alerts"], ui["effects"] = cfg["toasts"], cfg["alerts"], cfg["effects"]
    if not ui["toasts_on"]:
        ui["toasts"].clear()
    apply_theme(ui["theme"])
    apply_limits(cfg)
    try:
        save_config(cfg)
    except OSError:
        pass
