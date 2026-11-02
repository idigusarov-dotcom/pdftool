"""Журнал ошибок: %APPDATA%\\PdfTool\\log.txt (Windows) или ~/.local/share/PdfTool/log.txt."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

log = logging.getLogger("pdftool")


def log_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "PdfTool")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:  # нет прав — рядом с программой
        d = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "PdfTool_logs")
        os.makedirs(d, exist_ok=True)
        return d


def setup_logging() -> str:
    path = os.path.join(log_dir(), "log.txt")
    if not log.handlers:
        h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return path
