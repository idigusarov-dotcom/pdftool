"""Общие виджеты: просмотр страницы с рамкой, миниатюры, помощники."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QMessageBox, QPushButton, QWidget)

from ..core.pdfutil import NRect

# старые эмодзи в подписях кнопок -> иконки Lucide (эмодзи на некоторых ПК видны как квадратики)
EMOJI_ICONS = {"📄": "file-plus", "🖼": "image", "💾": "save", "👁": "eye", "🔢": "scan-search", "🔎": "file-text",
               "↕": "arrow-up-down", "↺": "refresh-ccw", "▶": "play", "✂": "scissors", "✍": "pen-tool",
               "✖": "x", "➕": "plus", "➖": "x", "⟲": "rotate-ccw", "⟳": "rotate-cw", "⤶": "download",
               "📂": "folder-open", "📋": "list-ordered", "🗑": "trash-2"}

# подсказки по умолчанию для кнопок (по тексту без эмодзи)
TIPS = {
    "Открыть акты…": "Выбрать PDF-файл с подписанными актами (откуда берём подписи)",
    "Открыть расписки…": "Выбрать PDF-файл с расписками (куда ставим подписи)",
    "Открыть PDF…": "Выбрать PDF-файл с компьютера",
    "Распознать номера и сопоставить": "Прочитать номера квартир в зелёных зонах и подобрать акт к каждой расписке",
    "Распознать номера": "Прочитать рукописные номера квартир в зелёной зоне (заполняются только пустые ячейки)",
    "Из текста PDF": "Найти «кв. №» в текстовом слое PDF (работает только для не-сканов)",
    "Упорядочить таблицу": "Расставить строки по номеру квартиры",
    "Рамки по умолчанию": "Вернуть стандартные рамки подписи для этих бланков",
    "Предпросмотр": "Посмотреть все расписки с подписями, ничего не сохраняя",
    "Сохранить новый PDF…": "Создать новый PDF с результатом. Исходные файлы не меняются",
    "Сохранить отсортированный PDF…": "Создать новый PDF со страницами в порядке квартир",
    "Сохранить список (CSV)…": "Сохранить таблицу «страница — квартира» для Excel",
    "Выбрать картинку…": "Выбрать PNG или JPG (печать, подпись, логотип)",
    "Добавить пару…": "Добавить в пакет файл актов и файл расписок",
    "Убрать": "Убрать выбранную строку из списка",
    "Проверить во вкладке «Подписи»": "Открыть выбранную пару на экране «Подписи», чтобы проверить пары страниц",
    "Взять сопоставление": "Забрать исправленные на экране «Подписи» пары страниц обратно в пакет",
    "Обработать все": "Создать PDF с подписями для всех пар по очереди",
    "Открыть папку результатов": "Открыть папку, куда сохранены готовые файлы",
    "Добавить…": "Добавить PDF-файлы для объединения",
    "Объединить и сохранить…": "Склеить файлы списка в один новый PDF",
    "Повернуть вправо": "Повернуть выделенные страницы по часовой стрелке",
    "Повернуть влево": "Повернуть выделенные страницы против часовой стрелки",
    "Удалить выбранные": "Удалить выделенные страницы (в новом файле)",
    "Сохранить как новый PDF…": "Сохранить изменённые страницы в новый файл",
    "По диапазонам…": "Разрезать PDF на части по введённым диапазонам",
    "Каждая страница отдельно…": "Сохранить каждую страницу отдельным PDF",
    "убрать зону 2": "Удалить вторую зону номера",
}


def big_button(text: str, slot=None, primary: bool = False, icon_name: str | None = None,
               tip: str | None = None, ghost: bool = False) -> QPushButton:
    from . import style
    first, _, rest = text.partition(" ")
    if first in EMOJI_ICONS and rest:
        icon_name, text = icon_name or EMOJI_ICONS[first], rest
    b = QPushButton(text)
    b.setProperty("kind", "primary" if primary else ("ghost" if ghost else "secondary"))
    b.setProperty("icon_name", icon_name or "")
    if icon_name:
        b.setIcon(style.icon(icon_name, "#ffffff" if primary else None))
    b.setCursor(Qt.PointingHandCursor)
    b.setToolTip(tip or TIPS.get(text, text))
    if slot:
        b.clicked.connect(slot)
    return b


def retheme_buttons(root: QWidget):
    """После смены темы перекрасить иконки кнопок."""
    from . import style
    for b in root.findChildren(QPushButton):
        n = b.property("icon_name")
        if n:
            b.setIcon(style.icon(n, "#ffffff" if b.property("kind") == "primary" else None))


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


def with_ext(path: str, flt: str) -> str:
    """Если имя ввели без расширения — добавить его из фильтра («PDF (*.pdf)» → .pdf)."""
    import os
    import re
    m = re.search(r"\*(\.\w+)", flt or "")
    if path and m and not os.path.splitext(path)[1]:
        return path + m.group(1)
    return path


def ask_save(parent: QWidget, suggested: str, flt="PDF (*.pdf)") -> str:
    from .infra import remember_dir
    p, _ = QFileDialog.getSaveFileName(parent, "Сохранить как", suggested, flt)
    p = with_ext(p, flt)
    remember_dir(p)
    return p


def try_open_pdf(parent: QWidget, path: str):
    """Открыть PDF; если нельзя (пустой, битый, с паролем) — понятное уведомление и None."""
    from ..core.logsetup import log
    from ..core.pdfutil import PdfError, open_pdf
    try:
        return open_pdf(path)
    except PdfError as e:
        log.warning("Не открылся %s: %s", path, e)
        error(parent, str(e))
        return None


def open_folder(path: str):
    """Открыть папку файла (или саму папку) в проводнике."""
    import os
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    QDesktopServices.openUrl(QUrl.fromLocalFile(d))


def _host(parent):
    w = parent.window() if parent is not None else None
    return w if w is not None and hasattr(w, "toast") else None


def info(parent, text, action=None):
    """Сообщение без всплывающего окна: уведомление внизу справа."""
    h = _host(parent)
    if h is not None:
        h.toast(text, "ok", action=action, timeout_ms=7000)
    else:
        QMessageBox.information(parent, "Готово", text)


def error(parent, text):
    h = _host(parent)
    if h is not None:
        h.toast(text, "error", timeout_ms=9000)
    else:
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
        from . import style
        self.setBackgroundBrush(QColor("#3a3f4b" if style.is_dark() else "#e6e9f0"))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.page_item: QGraphicsPixmapItem | None = None
        self.boxes: dict[str, BoxItem] = {}
        self.colors = dict(self.COLORS, main=color)
        self.active = "main"
        self.draw_enabled = True
        self._drag_start = None
        self._auto_fit = True
        self.setMinimumSize(160, 140)

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
        if self.page_item and e.button() == Qt.LeftButton and item is self.page_item and act is not None \
                and act.pixmap is not None:
            # щелчок по пустому месту страницы — картинка переезжает туда (центром)
            p = self.mapToScene(e.position().toPoint())
            r = act.rect()
            act.setPos(p.x() - r.width() / 2, p.y() - r.height() / 2)
            self.rectChanged.emit(self.active)
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
    from . import style
    for name, tip, fn in (("zoom-out", "Уменьшить", view.zoom_out),
                          ("zoom-in", "Увеличить (или Ctrl + колесо мыши)", view.zoom_in),
                          ("maximize", "По размеру окна", view.fit)):
        b = QToolButton()
        b.setIcon(style.icon(name))
        b.setProperty("icon_name", name)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        lay.addWidget(b)
    lay.addStretch()
    return lay
