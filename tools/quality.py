"""Métricas de salud del código, comparadas con una línea base para que no empeoren.

Uso: python tools/quality.py [--update] [ARCHIVOS…]   (por defecto, todos los .py del proyecto)

Mide la cobertura de los tests, la complejidad ciclomática (radon), el índice de mantenibilidad, el tamaño y las
variables que tapan un nombre de fuera (pylint W0621: ya causó un crash). Si alguna
métrica empeora respecto a quality-baseline.json, sale con código 1. Con --update, la
línea base pasa a ser el estado actual (solo hay que hacerlo cuando algo mejora).
"""
import glob
import json
import os
import subprocess
import sys

from radon.complexity import cc_rank, cc_visit
from radon.metrics import mi_visit
from radon.raw import analyze

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "quality-baseline.json")
# métrica -> (descripción, True si más alto es mejor)
METRICS = {
    "lines": ("líneas de código (sin comentarios ni blancos)", None),
    "biggest_file": ("líneas del archivo más grande", False),
    "mi_min": ("índice de mantenibilidad del peor archivo (0-100)", True),
    "cc_avg": ("complejidad media por función", False),
    "cc_max": ("complejidad de la peor función", False),
    "rank_d": ("funciones con complejidad D (21-30)", False),
    "rank_ef": ("funciones con complejidad E o F (31+)", False),
    "long_funcs": ("funciones de más de 80 líneas", False),
    "shadowing": ("nombres locales que tapan uno de fuera (W0621)", False),
    "coverage": ("% de líneas cubiertas por los tests", True),
}


def project_files():
    skip = (os.sep + ".venv" + os.sep, os.sep + "build" + os.sep)
    return sorted(p for p in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)
                  if not any(s in p for s in skip) and os.sep + "tools" + os.sep not in p
                  and os.sep + "tests" + os.sep not in p)


def blocks_of(code):
    """Funciones y métodos (cc_visit ya incluye los métodos sueltos, además de sus clases)."""
    return [b for b in cc_visit(code) if b.letter in "FM"]


def measure(files):
    m = dict.fromkeys(METRICS, 0)
    worst, total_cc, funcs, mis = [], 0, 0, []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        sloc = analyze(code).sloc
        m["lines"] += sloc
        m["biggest_file"] = max(m["biggest_file"], len(code.splitlines()))
        mis.append(mi_visit(code, multi=True))
        for b in blocks_of(code):
            funcs += 1
            total_cc += b.complexity
            rank = cc_rank(b.complexity)
            m["rank_d"] += rank == "D"
            m["rank_ef"] += rank in "EF"
            m["long_funcs"] += b.endline - b.lineno + 1 > 80
            m["cc_max"] = max(m["cc_max"], b.complexity)
            worst.append((b.complexity, f"{os.path.relpath(path, ROOT)}:{b.lineno} {b.fullname}"))
    m["cc_avg"] = round(total_cc / funcs, 2) if funcs else 0
    m["mi_min"] = round(min(mis), 2) if mis else 100
    py = os.path.join(os.path.dirname(sys.executable), "pylint")
    res = subprocess.run([py, "--disable=all", "--enable=redefined-outer-name", "--score=n", *files],
                         capture_output=True, text=True, check=False)
    m["shadowing"] = sum("W0621" in line for line in res.stdout.splitlines())
    m["coverage"] = coverage()
    return m, sorted(worst, reverse=True)[:8]


def coverage():
    """Total del último `pytest --cov` (lo deja check.sh en .coverage); 0 si no hay datos."""
    if not os.path.exists(os.path.join(ROOT, ".coverage")):
        return 0
    res = subprocess.run([sys.executable, "-m", "coverage", "report", "--format=total"], cwd=ROOT,
                         capture_output=True, text=True, check=False)
    return int(res.stdout.strip() or 0) if res.returncode == 0 else 0


def main():
    update = "--update" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")] or project_files()
    now, worst = measure(files)
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
    except (OSError, ValueError):
        base = {}
    worse = []
    print(f"{'métrica':<52} {'base':>8} {'ahora':>8}")
    for key, (desc, higher_better) in METRICS.items():
        b, n = base.get(key), now[key]
        mark = ""
        if b is not None and higher_better is not None and n != b:
            better = n > b if higher_better else n < b
            mark = "  ✓ mejor" if better else "  ✗ peor"
            if not better:
                worse.append(desc)
        print(f"{desc:<52} {'—' if b is None else b:>8} {n:>8}{mark}")
    print("\nfunciones más complejas:")
    for cc, where in worst:
        print(f"  {cc:>4}  {where}")
    if update or not base:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=2)
            fh.write("\n")
        print("\nlínea base actualizada")
        return 0
    if worse:
        print("\nempeoró: " + "; ".join(worse))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
