"""Temas de colores: la paleta activa, los marcos y los papeles de color."""

import math
from types import SimpleNamespace

from rich import box

# Cada tema da un color a cada papel; dentro de un tema no se repiten.
THEMES = {
    "clasico": dict(LEAD="#e8915a", SWARM="#6fb1ff", TOOL="#6fcf97", ADVISOR="#b48efc", ERR="#ef6b73",
                    WAIT="#f2c94c", DIM="#5c6680", LINE="#3b4560", FG="#c9d1e4", CARD_BG="#1c2740"),
    "neon": dict(LEAD="#ff2bd6", SWARM="#00e5ff", TOOL="#b6ff00", ADVISOR="#9d4dff", ERR="#ff3355",
                 WAIT="#fff200", DIM="#7a6fa8", LINE="#3d2a66", FG="#e8e6ff", CARD_BG="#1b0f33"),
    "matrix": dict(LEAD="#39ff14", SWARM="#00ff9c", TOOL="#b6ff00", ADVISOR="#7dffb0", ERR="#ff4d4d",
                   WAIT="#eaff00", DIM="#2f8f3a", LINE="#17581f", FG="#b8ffc0", CARD_BG="#06220b"),
    "ambar": dict(LEAD="#ffb000", SWARM="#ffd166", TOOL="#ffe9a8", ADVISOR="#ff8c42", ERR="#ff4d2e",
                  WAIT="#ffffff", DIM="#9a7526", LINE="#5c4412", FG="#ffdca6", CARD_BG="#261c06"),
    # Catppuccin (catppuccin.com): sus cuatro sabores, de más oscuro a claro (latte es para fondo claro)
    "catppuccin-mocha": dict(LEAD="#fab387", SWARM="#89b4fa", TOOL="#a6e3a1", ADVISOR="#cba6f7", ERR="#f38ba8",
                             WAIT="#f9e2af", DIM="#6c7086", LINE="#45475a", FG="#cdd6f4", CARD_BG="#313244"),
    "catppuccin-macchiato": dict(LEAD="#f5a97f", SWARM="#8aadf4", TOOL="#a6da95", ADVISOR="#c6a0f6",
                                 ERR="#ed8796", WAIT="#eed49f", DIM="#6e738d", LINE="#494d64", FG="#cad3f5",
                                 CARD_BG="#363a4f"),
    "catppuccin-frappe": dict(LEAD="#ef9f76", SWARM="#8caaee", TOOL="#a6d189", ADVISOR="#ca9ee6", ERR="#e78284",
                              WAIT="#e5c890", DIM="#737994", LINE="#51576d", FG="#c6d0f5", CARD_BG="#414559"),
    "catppuccin-latte": dict(LEAD="#fe640b", SWARM="#1e66f5", TOOL="#40a02b", ADVISOR="#8839ef", ERR="#d20f39",
                             WAIT="#df8e1d", DIM="#8c8fa1", LINE="#acb0be", FG="#4c4f69", CARD_BG="#e6e9ef"),
    # los 16 colores de tu terminal: sigue la paleta que tengas configurada en el shell.
    # LINE es color(8), el mismo gris que bright_black, escrito distinto para que no se repita.
    "terminal": dict(LEAD="magenta", SWARM="blue", TOOL="green", ADVISOR="cyan", ERR="red", WAIT="yellow",
                     DIM="bright_black", LINE="color(8)", FG="default", CARD_BG="default",
                     HIGHLIGHT="bright_white", BLOOD="bright_red"),
}
# colores de los subagentes por tema; los que no están usan los de neón
THEME_AGENTS = {
    "catppuccin-mocha": ("#eba0ac", "#89dceb", "#94e2d5", "#f9e2af", "#cba6f7", "#fab387", "#f5c2e7", "#b4befe"),
    "catppuccin-macchiato": ("#ee99a0", "#91d7e3", "#8bd5ca", "#eed49f", "#c6a0f6", "#f5a97f", "#f5bde6",
                             "#b7bdf8"),
    "catppuccin-frappe": ("#ea999c", "#99d1db", "#81c8be", "#e5c890", "#ca9ee6", "#ef9f76", "#f4b8e4", "#babbf1"),
    "catppuccin-latte": ("#e64553", "#04a5e5", "#179299", "#df8e1d", "#8839ef", "#fe640b", "#ea76cb", "#7287fd"),
    "terminal": ("bright_red", "bright_cyan", "bright_green", "bright_yellow", "bright_magenta", "bright_blue",
                 "cyan", "magenta"),
}
HUD_THEMES = {"neon", "matrix", "ambar"}
# Marco tipo HUD para los temas de HUD_THEMES: solo esquinas, sin laterales.
HUD_BOX = box.Box("⌜─ ⌝\n    \n    \n    \n    \n    \n    \n⌞─ ⌟\n")
HUD_HEAVY = box.Box("▛▀▀▜\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▌  ▐\n▙▄▄▟\n")


# un color por subagente, el mismo en su tarjeta, su fila y el gráfico de actividad
NEON_AGENTS = ("#ff2a6d", "#05d9e8", "#39ff14", "#ffd319", "#b967ff", "#01ffc3", "#ff71ce", "#7aa2ff")
# La paleta activa. Los módulos leen C.LEAD, C.DIM…; apply_theme la cambia en su sitio, así
# todos ven el tema nuevo sin volver a importar nada.
C = SimpleNamespace()


def apply_theme(name):
    """Cambia los colores y el marco de todo el dashboard."""
    hud = name in HUD_THEMES
    C.__dict__.update(HIGHLIGHT="#d1f7ff", BLOOD="#ff2a3c")   # los rostros; el tema puede cambiarlos
    C.__dict__.update(THEMES.get(name, THEMES["clasico"]))
    C.AGENTS = THEME_AGENTS.get(name, NEON_AGENTS)
    C.HUD = hud
    C.PANEL_BOX, C.CARD_BOX = (HUD_BOX, HUD_HEAVY) if hud else (box.SQUARE, box.HEAVY)


def role_color(role):
    """El color de un papel ("TOOL", "ERR"…) en el tema activo; un color suelto se devuelve tal cual."""
    return getattr(C, role) if role.isupper() and hasattr(C, role) else role


def label(text, tag="SYS"):
    """Título de panel: en los temas HUD, en mayúsculas y con prefijo de sistema."""
    return f"{tag}//{text.upper()}" if C.HUD else text


# estado -> (icono, texto, papel de color)
STATES = {"waiting": ("◆", "espera tu respuesta", "WAIT"), "busy": ("●", "trabajando", "LEAD"),
          "idle": ("○", "esperando prompt", "TOOL"), "ended": ("·", "cerrada", "DIM")}


def state_color(state):
    return role_color(STATES[state][2])


FRESH = 4            # segundos que dura el efecto de una tarea recién terminada

# tipo de novedad -> (icono, título, papel de color, ruidosa: pitido + mensaje de tmux + log)
NEWS = {"wait": ("◆", "te espera", "WAIT", True), "stuck": ("⚠", "posible atasco", "ERR", True),
        "fail": ("✗", "falló una tarea", "ERR", True), "job": ("✓", "comando terminado", "SWARM", False), "done": ("■", "turno terminado", "TOOL", True),
        "cheat": ("★", "truco activado", "WAIT", False),
        "task": ("✓", "tarea terminada", "TOOL", False), "agent": ("✓", "subagente terminado", "SWARM", False),
        "evo": ("★", "evolucionó", "WAIT", False)}


def blend(a, b, k):
    """Color intermedio entre dos #rrggbb; k=0 da a, k=1 da b.

    Con colores con nombre (tema terminal) no se puede mezclar: da el más cercano."""
    if not (a.startswith("#") and b.startswith("#")):
        return a if k < 0.5 else b
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ca, cb))


def breath(color, now, offset=0.0):
    """Color que "respira" entre el tono de las líneas y `color`."""
    k = (1 - math.cos(2 * math.pi * (now + offset) / 2.4)) / 2
    return blend(C.LINE, color, 0.15 + 0.85 * k)


apply_theme("clasico")
