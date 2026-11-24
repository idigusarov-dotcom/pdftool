"""Миниатюры страниц: ленивая отрисовка в фоновом потоке."""
from __future__ import annotations

import threading

import numpy as np
import pymupdf
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap, QTransform

from ..core.logsetup import log
from ..core.pdfutil import render_page
from .widgets import np_to_pixmap


def page_pixmap(doc: pymupdf.Document, i: int, dpi: int = 100) -> QPixmap:
    return np_to_pixmap(render_page(doc, i, dpi))


def thumb_icon(doc: pymupdf.Document, i: int, rotation: int = 0, dpi: int = 30) -> QIcon:
    pm = page_pixmap(doc, i, dpi)
    if rotation:
        pm = pm.transformed(QTransform().rotate(rotation))
    return QIcon(pm)


def placeholder_icon(w: int = 60, h: int = 84) -> QIcon:
    pm = QPixmap(w, h)
    pm.fill(Qt.lightGray)
    return QIcon(pm)


class ThumbLoader(QObject):
    """Рисует миниатюры по запросу в отдельном потоке (свой экземпляр документа).
    Последние запрошенные (видимые) страницы рисуются первыми."""
    ready = Signal(str, int, QImage)

    def __init__(self, path: str, dpi: int = 30, parent=None):
        super().__init__(parent)
        self.path, self.dpi = path, dpi
        self._stack: list[int] = []
        self._seen: set[int] = set()
        self._cv = threading.Condition()
        self._stop = False
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def request(self, indices):
        with self._cv:
            for i in indices:
                if i not in self._seen:
                    self._seen.add(i)
                    self._stack.append(i)
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify()
        self._th.join()

    def _run(self):
        try:
            doc = pymupdf.open(self.path)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось открыть для миниатюр: %s", self.path)
            return
        while True:
            with self._cv:
                while not self._stack and not self._stop:
                    self._cv.wait()
                if self._stop:
                    break
                i = self._stack.pop()
            try:
                arr = np.ascontiguousarray(render_page(doc, i, self.dpi))
                h, w = arr.shape[:2]
                img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            except Exception:  # noqa: BLE001
                log.exception("Миниатюра стр. %s", i)
                continue
            try:
                self.ready.emit(self.path, i, img)
            except RuntimeError:   # окно уже закрыто (объект-источник сигнала удалён) — просто выходим
                break
        doc.close()


def visible_rows(view) -> range:
    """Видимые строки таблицы/списка (с запасом)."""
    vp = view.viewport()
    if hasattr(view, "rowAt"):
        top = view.rowAt(0)
        bot = view.rowAt(vp.height() - 1)
        n = view.rowCount()
    else:
        # В сетке между миниатюрами есть свободное место. Углы viewport часто попадают
        # в него даже в середине длинного документа, поэтому indexAt там возвращает -1.
        n = view.count()
        cell = view.gridSize()
        if not n or not vp.isVisible() or cell.isEmpty():
            return range(0)
        columns = max(1, vp.width() // cell.width())
        offset = -view.visualItemRect(view.item(0)).top()
        top = max(0, offset // cell.height()) * columns
        last_line = max(0, (offset + vp.height() - 1) // cell.height())
        bot = (last_line + 1) * columns - 1
    top = 0 if top < 0 else top
    bot = n - 1 if bot < 0 else bot
    return range(max(0, top - 5), min(n, bot + 10))
