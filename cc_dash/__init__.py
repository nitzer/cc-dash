"""cc-dash — dashboard en vivo de las sesiones de Claude Code.

Por cada cuenta (un directorio de configuración de Claude Code) lee
sessions/*.json (sesiones abiertas) y projects/**/*.jsonl (modelo,
herramientas, subagentes, tokens). Solo lectura: no toca ni envía nada.

Dentro del dashboard, ? muestra todas las teclas.
Uso:    cc-dash [--once] [-a CUENTA]
        cc-dash accounts | add NOMBRE RUTA | remove NOMBRE | scan DIR | config
        cc-dash update
        cc-dash disable CUENTA | enable CUENTA
        cc-dash set CLAVE VALOR     (umbrales y opciones; `cc-dash set` las lista)
"""
__version__ = "0.43.0"

from .cli import main  # después de __version__, que cli importa

__all__ = ["__version__", "main"]
