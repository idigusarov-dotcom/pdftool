"""Журнал ошибок и данные программы:
Windows — %APPDATA%\\PdfTool, macOS — ~/Library/Application Support/PdfTool, Linux — ~/.local/share/PdfTool."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

log = logging.getLogger("pdftool")


def log_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "PdfTool")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:  # нет прав — рядом с программой
        import tempfile   # внутри .app на macOS писать нельзя — во временную папку
        d = os.path.join(tempfile.gettempdir(), "PdfTool_logs")
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
