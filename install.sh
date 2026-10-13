#!/bin/sh
# Instala cc-dash para el usuario actual. Uso: ./install.sh [--uninstall]
set -eu
cd "$(dirname "$0")"

DEST="${XDG_DATA_HOME:-$HOME/.local/share}/cc-dash"
BIN="$HOME/.local/bin"

if [ "${1:-}" = "--uninstall" ]; then
    command -v uv >/dev/null 2>&1 && uv tool uninstall cc-dash 2>/dev/null || true
    command -v pipx >/dev/null 2>&1 && pipx uninstall cc-dash 2>/dev/null || true
    rm -rf "$DEST"
    [ -L "$BIN/cc-dash" ] && rm -f "$BIN/cc-dash"
    echo "cc-dash desinstalado (la configuración en ~/.config/cc-dash se conserva)"
    exit 0
fi

# cc-dash usa esta ruta para actualizarse (cc-dash update)
mkdir -p "$DEST"
pwd > "$DEST/source"

if command -v uv >/dev/null 2>&1; then
    uv tool install --force .
elif command -v pipx >/dev/null 2>&1; then
    pipx install --force .
else
    PY="${PYTHON:-python3}"
    "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
        echo "cc-dash necesita Python 3.11 o superior" >&2
        exit 1
    }
    rm -rf "$DEST/venv"
    "$PY" -m venv "$DEST/venv" || {
        echo "no se pudo crear el entorno virtual; en Debian/Ubuntu: sudo apt install python3-venv" >&2
        exit 1
    }
    "$DEST/venv/bin/pip" install --quiet --disable-pip-version-check .
    mkdir -p "$BIN"
    ln -sf "$DEST/venv/bin/cc-dash" "$BIN/cc-dash"
    case ":$PATH:" in
        *":$BIN:"*) ;;
        *) echo "aviso: añade $BIN a tu PATH" ;;
    esac
fi

echo "listo: ejecuta 'cc-dash' (y 'cc-dash accounts' para ver las cuentas)"
