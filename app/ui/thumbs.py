"""Миниатюры страниц."""
from __future__ import annotations

import pymupdf
from PySide6.QtGui import QIcon, QPixmap, QTransform

from ..core.pdfutil import render_page
from .widgets import np_to_pixmap


def page_pixmap(doc: pymupdf.Document, i: int, dpi: int = 100) -> QPixmap:
    return np_to_pixmap(render_page(doc, i, dpi))


def thumb_icon(doc: pymupdf.Document, i: int, rotation: int = 0, dpi: int = 30) -> QIcon:
    pm = page_pixmap(doc, i, dpi)
    if rotation:
        pm = pm.transformed(QTransform().rotate(rotation))
    return QIcon(pm)
