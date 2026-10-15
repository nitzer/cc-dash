"""Lectura del teclado sin bloquear, en Unix y en Windows."""

import os
import re
import time

try:
    import select
    import termios
except ImportError:  # Windows
    termios = None


def read_keys(fd, timeout):
    if termios is None:
        import msvcrt
        time.sleep(timeout)
        keys = ""
        while msvcrt.kbhit():
            keys += msvcrt.getwch()
        arrows = {"\xe0H": b"k", "\xe0P": b"j", "\xe0K": b"h", "\xe0M": b"l",
                  "\xe0I": b"\x1b[5~", "\xe0Q": b"\x1b[6~"}
        return arrows.get(keys, keys.encode("utf-8", "ignore"))
    if fd is None:
        time.sleep(timeout)
        return b""
    ready, _, _ = select.select([fd], [], [], timeout)
    return os.read(fd, 64) if ready else b""


def split_keys(raw):
    """Separa lo leído en teclas sueltas: al mantener una tecla llegan varias juntas."""
    return re.findall(rb"\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Za-z]|[\x00-\xff]", raw, re.S)
