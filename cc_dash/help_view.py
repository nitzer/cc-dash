"""La ventana de ayuda (?): todas las teclas, agrupadas."""

from rich import box
from rich.panel import Panel
from rich.text import Text

from .text import trunc
from .theme import C, label

# La ayuda (tecla ?): grupos de (teclas, qué hacen)
KEYS = [
    ("Sesiones", [
        ("↑ ↓  ·  j k", "cambia de sesión (con el foco en sesiones)"),
        ("a", "vuelve al modo automático"),
        ("Enter", "salta al panel de tmux de la sesión"),
        ("c", "filtra por cuenta, si hay más de una"),
        ("v", "conversación de la sesión"),
        ("D", "abre el último .md que escribió (spec, plan…)"),
    ]),
    ("Subagentes", [
        ("← →  ·  h l", "selecciona un subagente"),
        ("r  ·  p", "su última respuesta  ·  su prompt"),
        ("Esc", "suelta la selección"),
        ("t", "muestra u oculta los terminados y las sesiones inactivas"),
        ("F", "foco: log, actividad y gráfico solo de ese agente"),
    ]),
    ("Session log", [
        ("s", "cursor en el log (Enter abre el detalle)"),
        ("f", "log de todas las sesiones o solo de la elegida (por defecto)"),
        ("/", "busca texto (Enter aplica, Esc borra)"),
        ("y", "copia lo que tienes delante"),
    ]),
    ("Panel derecho", [
        ("o", "tareas: segundo plano y pendientes"),
        ("n", "registro de novedades"),
        ("i", "actividad, en terminales estrechas"),
        ("AvPág RePág", "desplaza el panel  ·  también espacio y b"),
    ]),
    ("Avisos", [
        ("x", "cierra los toasts en pantalla"),
        ("X", "apaga o enciende los toasts"),
        ("m", "silencia el pitido y el aviso de tmux"),
    ]),
    ("Apariencia y ajustes", [
        ("d", "diseño del panel de agentes"),
        ("L", "evoluciones de cada rostro (h/l personaje, j/k nivel)"),
        ("T", "tema de colores"),
        ("E", "efectos visuales"),
        (",", "configuración: ajustes y cuentas"),
    ]),
    ("Paneles", [
        ("Tab  ·  Shift+Tab", "pasa el foco al panel siguiente o anterior"),
        ("j k", "se mueve dentro del panel con el foco"),
        ("gg  ·  G", "primero  ·  último"),
        ("Enter", "agentes: foco · log: detalle · actividad: despliega la lista"),
        ("e", "actividad: el archivo con sus cambios resaltados"),
    ]),
    ("General", [
        ("?", "esta ayuda"),
        ("q", "salir"),
    ]),
]


def help_panel(view, width, max_rows):
    """Ventana flotante con todas las teclas, en dos columnas si hay ancho."""
    key_w = 13

    def block(groups, col_w):
        out = []
        for title, rows in groups:
            if out:
                out.append(Text(""))
            out.append(Text(title.upper(), style=f"bold {C.SWARM}"))
            for keys, what in rows:
                line = Text(no_wrap=True, overflow="ellipsis")
                line.append(f"{keys:<{key_w}}", style=f"bold {C.LEAD}")
                line.append(trunc(what, col_w - key_w), style=C.FG)
                out.append(line)
        return out

    inner = width - 4
    if inner >= 96:   # dos columnas, repartiendo los grupos por número de filas
        half, left, count = sum(len(rows) + 2 for _, rows in KEYS) / 2, [], 0
        for group in KEYS:
            size = len(group[1]) + 2
            if left and count + size / 2 > half:
                break
            left.append(group)
            count += size
        col_w = (inner - 3) // 2
        a, b = block(left, col_w), block(KEYS[len(left):], col_w)
        lines = []
        for n in range(max(len(a), len(b))):
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append_text(a[n] if n < len(a) else Text(""))
            line.pad_right(max(0, col_w + 3 - len(line.plain)))
            line.append_text(b[n] if n < len(b) else Text(""))
            lines.append(line)
    else:
        lines = block(KEYS, inner)
    room = max(3, max_rows - 4)
    view["rows"] = room
    top = max(0, min(view["top"], len(lines) - room))
    view["top"] = top
    body = Text(no_wrap=True, overflow="ellipsis")
    chunk = lines[top: top + room]
    for line in chunk:
        body.append_text(line)
        body.append("\n")
    more = len(lines) > room
    body.append("\n" + ("j/k desplaza · " if more else "") + "cualquier otra tecla cierra", style=C.DIM)
    return Panel(body, title=label("teclas", "HELP"), title_align="left", box=box.HEAVY,
                 border_style=f"bold {C.SWARM}", style=f"on {C.CARD_BG}", width=width,
                 subtitle=f"{top + 1}–{top + len(chunk)} de {len(lines)}" if more else None, subtitle_align="right")
