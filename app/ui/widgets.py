"""Общие виджеты: просмотр страницы с рамкой, миниатюры, помощники."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QMessageBox, QPushButton, QWidget)

from ..core.pdfutil import NRect

BIG_BTN = "QPushButton { font-size: 15px; padding: 8px 16px; min-height: 30px; }"


def big_button(text: str, slot=None, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    style = BIG_BTN
    if primary:
        style += "QPushButton { background: #2e7d32; color: white; font-weight: bold; }"
    b.setStyleSheet(style)
    if slot:
        b.clicked.connect(slot)
    return b


def np_to_pixmap(arr: np.ndarray) -> QPixmap:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.shape[2] == 4:
        img = QImage(arr.data, w, h, 4 * w, QImage.Format_RGBA8888)
    else:
        img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


def ask_open_pdf(parent: QWidget, title="Выберите PDF") -> str:
    p, _ = QFileDialog.getOpenFileName(parent, title, "", "PDF (*.pdf)")
    return p


def ask_save(parent: QWidget, suggested: str, flt="PDF (*.pdf)") -> str:
    p, _ = QFileDialog.getSaveFileName(parent, "Сохранить как", suggested, flt)
    return p


def info(parent, text):
    QMessageBox.information(parent, "Готово", text)


def error(parent, text):
    QMessageBox.warning(parent, "Ошибка", text)


class BoxItem(QGraphicsRectItem):
    """Перемещаемая рамка с угловой ручкой для изменения размера; может показывать картинку."""
    HANDLE = 14

    def __init__(self, rect: QRectF, color: str = "#e53935", pixmap: QPixmap | None = None, on_change=None):
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.topLeft())
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setPen(QPen(QColor(color), 2, Qt.DashLine))
        self.setBrush(QBrush(QColor(255, 235, 59, 40)) if pixmap is None else Qt.NoBrush)
        self.pixmap = pixmap
        self.keep_aspect = pixmap is not None
        self._resizing = False
        self.on_change = on_change
        self.setCursor(Qt.SizeAllCursor)
        self.setZValue(10)

    def scene_rect(self) -> QRectF:
        return QRectF(self.pos(), self.rect().size())

    def paint(self, painter: QPainter, option, widget=None):
        if self.pixmap is not None:
            painter.drawPixmap(self.rect(), self.pixmap, QRectF(self.pixmap.rect()))
        super().paint(painter, option, widget)
        r = self.rect()
        painter.fillRect(QRectF(r.right() - self.HANDLE, r.bottom() - self.HANDLE, self.HANDLE, self.HANDLE),
                         QColor("#1565c0"))

    def _in_handle(self, p: QPointF) -> bool:
        r = self.rect()
        return p.x() >= r.right() - self.HANDLE * 1.5 and p.y() >= r.bottom() - self.HANDLE * 1.5

    def mousePressEvent(self, e):
        if self._in_handle(e.pos()):
            self._resizing = True
            self._ratio = self.rect().width() / max(self.rect().height(), 1)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            w = max(10.0, e.pos().x())
            h = max(10.0, e.pos().y())
            if self.keep_aspect:
                h = w / self._ratio
            self.prepareGeometryChange()
            self.setRect(QRectF(0, 0, w, h))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = False
        super().mouseReleaseEvent(e)
        if self.on_change:
            self.on_change()


class PageView(QGraphicsView):
    """Показ страницы. В режиме рисования — новая рамка мышью (левая кнопка на пустом месте)."""
    rectChanged = Signal()

    def __init__(self, parent=None, color="#e53935"):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#9e9e9e"))
        self.page_item: QGraphicsPixmapItem | None = None
        self.box: BoxItem | None = None
        self.color = color
        self.draw_enabled = True
        self._drag_start = None
        self.setMinimumSize(300, 300)

    def set_page(self, pix: QPixmap):
        nr = self.get_nrect()
        overlay = self.box.pixmap if self.box else None
        self.scene().clear()
        self.box = None
        self.page_item = self.scene().addPixmap(pix)
        self.scene().setSceneRect(QRectF(pix.rect()))
        if nr:
            self.set_nrect(nr, overlay)
        self.fit()

    def fit(self):
        if self.page_item:
            self.fitInView(self.page_item, Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def _size(self):
        r = self.page_item.pixmap().rect()
        return r.width(), r.height()

    def set_nrect(self, nr: NRect, pixmap: QPixmap | None = None):
        if not self.page_item:
            return
        w, h = self._size()
        n = nr.normalized()
        rect = QRectF(n.x0 * w, n.y0 * h, (n.x1 - n.x0) * w, (n.y1 - n.y0) * h)
        if self.box:
            self.scene().removeItem(self.box)
        self.box = BoxItem(rect, self.color, pixmap, on_change=self.rectChanged.emit)
        self.scene().addItem(self.box)
        self.rectChanged.emit()

    def get_nrect(self) -> NRect | None:
        if not (self.box and self.page_item):
            return None
        w, h = self._size()
        r = self.box.scene_rect()
        return NRect(r.left() / w, r.top() / h, r.right() / w, r.bottom() / h)

    def mousePressEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        if self.draw_enabled and self.page_item and e.button() == Qt.LeftButton and item is self.page_item \
                and (self.box is None or self.box.pixmap is None):
            self._drag_start = self.mapToScene(e.position().toPoint())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            p = self.mapToScene(e.position().toPoint())
            w, h = self._size()
            a = self._drag_start
            self.set_nrect(NRect(a.x() / w, a.y() / h, p.x() / w, p.y() / h))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)
