#!/bin/sh
# Chequeo de salud del código. Uso: ./check.sh [--update]
#   --update  guarda las métricas actuales como nueva línea base (cuando algo mejoró)
# La primera vez crea .venv con las herramientas de requirements-dev.txt.
set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/ruff ]; then
    python3 -m venv .venv
    .venv/bin/pip install --quiet --disable-pip-version-check -r requirements-dev.txt
fi
V=.venv/bin
fail=0

echo "── sintaxis (Python 3.11+)"
$V/python -m py_compile cc_dash.py && echo "ok"

echo "── ruff"
$V/ruff check . || fail=1

echo "── vulture (código muerto)"
$V/vulture || fail=1

echo "── métricas"
$V/python tools/quality.py "$@" || fail=1

[ "$fail" = 0 ] && echo "── todo bien" || echo "── hay problemas"
exit "$fail"
