"""Журнал ошибок и данные программы в папке пользователя."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler

log = logging.getLogger("pdftool")


def data_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "PdfTool")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.join(tempfile.gettempdir(), "PdfTool_logs")
        os.makedirs(d, exist_ok=True)
    return d


def log_file() -> str | None:
    return next((h.baseFilename for h in log.handlers if isinstance(h, RotatingFileHandler)), None)


def log_dir() -> str:
    path = log_file()
    return os.path.dirname(path) if path else data_dir()


def setup_logging() -> str | None:
    if log.handlers:
        return log_file()
    try:
        handler = RotatingFileHandler(os.path.join(data_dir(), "log.txt"), maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8")
    except OSError:
        # Папка может существовать, но сам log.txt может быть заблокирован или доступен лишь для чтения.
        try:
            folder = tempfile.mkdtemp(prefix="PdfTool-logs-")
            handler = RotatingFileHandler(os.path.join(folder, "log.txt"), maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8")
        except OSError:
            log.addHandler(logging.NullHandler())
            return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler.baseFilename
