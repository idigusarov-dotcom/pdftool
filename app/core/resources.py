"""Поиск файлов программы (шрифты, иконки, модель цифр) — при запуске из исходников, из PdfTool.exe
(PyInstaller, sys._MEIPASS) и из PdfTool.app на macOS (Contents/Frameworks или Contents/Resources)."""
from __future__ import annotations

import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # папка app/


def resource(*parts: str) -> str:
    """resource("ui", "assets", "fonts", "Inter-Regular.ttf") → полный путь к существующему файлу
    (или путь в исходниках, если файла нигде нет)."""
    rel = os.path.join("app", *parts)
    roots = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        roots.append(base)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots += [os.path.join(exe_dir, "..", "Resources"), os.path.join(exe_dir, "..", "Frameworks"), exe_dir]
    for r in roots:
        p = os.path.normpath(os.path.join(r, rel))
        if os.path.exists(p):
            return p
    return os.path.join(_PKG_ROOT, *parts)
