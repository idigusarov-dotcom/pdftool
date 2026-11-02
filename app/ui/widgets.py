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
    from .infra import last_dir, remember_dir
    p, _ = QFileDialog.getOpenFileName(parent, title, last_dir(), "PDF (*.pdf)")
    remember_dir(p)
    return p


def ask_save(parent: QWidget, suggested: str, flt="PDF (*.pdf)") -> str:
    from .infra import remember_dir
    p, _ = QFileDialog.getSaveFileName(parent, "Сохранить как", suggested, flt)
    remember_dir(p)
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
    """Показ страницы с несколькими рамками (по ключу). Новая рамка рисуется мышью (ключ active).
    Масштаб: Ctrl+колесо, кнопки zoom_in/zoom_out/fit."""
    rectChanged = Signal(str)

    COLORS = {"main": "#e53935", "zone": "#2e7d32", "zone2": "#00897b"}

    def __init__(self, parent=None, color="#e53935"):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#9e9e9e"))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.page_item: QGraphicsPixmapItem | None = None
        self.boxes: dict[str, BoxItem] = {}
        self.colors = dict(self.COLORS, main=color)
        self.active = "main"
        self.draw_enabled = True
        self._drag_start = None
        self._auto_fit = True
        self.setMinimumSize(300, 300)

    # совместимость: основная рамка
    @property
    def box(self):
        return self.boxes.get("main")

    @box.setter
    def box(self, v):
        if v is None:
            self.boxes.pop("main", None)

    def clear_boxes(self):
        for b in list(self.boxes.values()):
            if b.scene():
                self.scene().removeItem(b)
        self.boxes.clear()

    def set_page(self, pix: QPixmap):
        saved = {k: (self.get_nrect(k), b.pixmap) for k, b in self.boxes.items()}
        self.scene().clear()
        self.boxes.clear()
        self.page_item = self.scene().addPixmap(pix)
        self.page_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().setSceneRect(QRectF(pix.rect()))
        for k, (nr, pm) in saved.items():
            if nr:
                self.set_nrect(nr, pm, key=k, emit=False)
        if self._auto_fit:
            self.fit()

    def show_message(self, text: str):
        self.scene().clear()
        self.boxes.clear()
        self.page_item = None
        self.scene().addText(text)

    def fit(self):
        if self.page_item:
            self._auto_fit = True
            self.resetTransform()
            self.fitInView(self.page_item, Qt.KeepAspectRatio)

    def zoom(self, factor: float):
        self._auto_fit = False
        self.scale(factor, factor)

    def zoom_in(self):
        self.zoom(1.25)

    def zoom_out(self):
        self.zoom(0.8)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
            return
        super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto_fit:
            self.fit()

    def _size(self):
        r = self.page_item.pixmap().rect()
        return r.width(), r.height()

    def set_nrect(self, nr: NRect, pixmap: QPixmap | None = None, key: str = "main", emit: bool = True):
        if not self.page_item or nr is None:
            return
        w, h = self._size()
        n = nr.normalized()
        rect = QRectF(n.x0 * w, n.y0 * h, max(4.0, (n.x1 - n.x0) * w), max(4.0, (n.y1 - n.y0) * h))
        old = self.boxes.get(key)
        if old is not None and old.scene():
            self.scene().removeItem(old)
        b = BoxItem(rect, self.colors.get(key, "#e53935"), pixmap, on_change=lambda k=key: self.rectChanged.emit(k))
        self.boxes[key] = b
        self.scene().addItem(b)
        if emit:
            self.rectChanged.emit(key)

    def remove_box(self, key: str):
        b = self.boxes.pop(key, None)
        if b is not None and b.scene():
            self.scene().removeItem(b)

    def get_nrect(self, key: str = "main") -> NRect | None:
        b = self.boxes.get(key)
        if not (b and self.page_item):
            return None
        w, h = self._size()
        r = b.scene_rect()
        return NRect(r.left() / w, r.top() / h, r.right() / w, r.bottom() / h)

    def mousePressEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        act = self.boxes.get(self.active)
        if self.draw_enabled and self.page_item and e.button() == Qt.LeftButton and item is self.page_item \
                and (act is None or act.pixmap is None):
            self._drag_start = self.mapToScene(e.position().toPoint())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            p = self.mapToScene(e.position().toPoint())
            w, h = self._size()
            a = self._drag_start
            self.set_nrect(NRect(a.x() / w, a.y() / h, p.x() / w, p.y() / h), key=self.active, emit=False)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_start is not None:
            self._drag_start = None
            self.rectChanged.emit(self.active)
            return
        super().mouseReleaseEvent(e)


def zoom_bar(view: PageView):
    """Небольшая панель масштаба под просмотром страницы."""
    from PySide6.QtWidgets import QHBoxLayout, QToolButton
    lay = QHBoxLayout()
    for text, tip, fn in (("−", "Уменьшить", view.zoom_out), ("+", "Увеличить (или Ctrl+колесо)", view.zoom_in),
                          ("⤢", "По размеру окна", view.fit)):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setStyleSheet("font-size: 16px; min-width: 30px;")
        b.clicked.connect(fn)
        lay.addWidget(b)
    lay.addStretch()
    return lay
