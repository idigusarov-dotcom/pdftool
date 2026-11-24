"""Окно «Доработать вручную»: ластик и кисть «Вернуть чернила» поверх автоматически вырезанного фрагмента.

Правки — мазки в долях страницы-источника (app.core.retouch), поэтому переживают смену чувствительности,
режима и сдвиг рамки. Отмена/повтор здесь свои, на всё окно; при «Готово» правки целиком становятся одним
шагом отмены на экране «Вырезать и перенести»."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QGraphicsEllipseItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout)

from ..core import fragment as fr
from ..core import retouch as rt
from . import style
from .widgets import big_button, np_to_pixmap

DLG_DPI = 200
BRUSH_MIN, BRUSH_MAX, BRUSH_DEFAULT = 2, 120, 14   # радиус кисти в пикселях фрагмента (200 dpi)


def checker(h: int, w: int, dark: bool, cell: int = 10) -> np.ndarray:
    yy, xx = np.mgrid[:h, :w]
    a, b = (214, 186) if dark else (240, 208)   # светлые клетки и в тёмной теме: чернила тёмные
    v = np.where(((yy // cell + xx // cell) % 2) == 0, a, b).astype(np.uint8)
    return np.dstack([v, v, v])


class RetouchView(QGraphicsView):
    strokeStarted = Signal(QPointF)
    strokeMoved = Signal(QPointF)
    strokeEnded = Signal()
    brushWheel = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(style.colors()["page"]))
        self.setMouseTracking(True)
        self.viewport().setCursor(Qt.BlankCursor)
        self.cursor_item = QGraphicsEllipseItem()
        self.cursor_item.setZValue(10)
        self._drawing = False
        self._pan = None
        self._auto_fit = True
        self.radius = BRUSH_DEFAULT
        self.setMinimumSize(320, 200)

    def set_cursor_color(self, color: str):
        pen = QPen(QColor(color), 1.5)
        pen.setCosmetic(True)
        self.cursor_item.setPen(pen)
        self.cursor_item.setBrush(QBrush(QColor(color).lighter(150), Qt.NoBrush))

    def place_cursor(self, p: QPointF):
        r = self.radius
        self.cursor_item.setRect(QRectF(p.x() - r, p.y() - r, 2 * r, 2 * r))
        self.cursor_item.setVisible(True)

    def fit(self):
        r = self.scene().sceneRect()
        if r.isEmpty():
            return
        self._auto_fit = True
        self.resetTransform()
        self.fitInView(r.adjusted(-8, -8, 8, 8), Qt.KeepAspectRatio)

    def zoom(self, f: float):
        self._auto_fit = False
        cur = self.transform().m11()
        f = max(0.1 / cur, min(f, 16 / cur))
        self.scale(f, f)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto_fit:
            self.fit()

    def viewportEvent(self, e):
        if e.type() == QEvent.NativeGesture and e.gestureType() == Qt.ZoomNativeGesture:
            self.zoom(1.0 + e.value())
            e.accept()
            return True
        return super().viewportEvent(e)

    def wheelEvent(self, e):
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        elif mods & Qt.AltModifier:
            self.brushWheel.emit(1 if (e.angleDelta().y() or e.angleDelta().x()) > 0 else -1)
            e.accept()
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            self._drawing = True
            self.strokeStarted.emit(self.mapToScene(e.position().toPoint()))

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        p = self.mapToScene(e.position().toPoint())
        self.place_cursor(p)
        if self._drawing:
            self.strokeMoved.emit(p)

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan = None
            self.viewport().setCursor(Qt.BlankCursor)
            return
        if e.button() == Qt.LeftButton and self._drawing:
            self._drawing = False
            self.strokeEnded.emit()

    def leaveEvent(self, e):
        self.cursor_item.setVisible(False)
        super().leaveEvent(e)


class RetouchDialog(QDialog):
    """strokes() после exec() == Accepted — новые правки (tuple[Stroke, ...])."""

    def __init__(self, parent, src_doc, page: int, nrect, mode: str, sensitivity: float, keep_colors: bool,
                 strokes=(), dpi: int = DLG_DPI):
        super().__init__(parent)
        self.setWindowTitle("Доработать вручную")
        self.setObjectName("RetouchDialog")
        self.mode, self.keep = mode, keep_colors
        self.auto, self.crop, self.box, self.page_px = fr.cut_parts(src_doc, page, nrect, mode, sensitivity,
                                                                    keep_colors, dpi)
        h, w = self.auto.shape[:2]
        self.hist: list[tuple] = [tuple(strokes)]
        self.pos = 0
        self._cur: list = []          # точки текущего мазка (в пикселях фрагмента)
        self._lab_cache = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        hint = QLabel("Ластик убирает лишнее (точки, линии бланка). «Вернуть чернила» возвращает пропавшие "
                      "штрихи — из исходного скана, другим цветом рисовать нельзя.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(hint, 1)
        self.lbl_count = QLabel()
        self.lbl_count.setObjectName("Muted")
        top.addWidget(self.lbl_count, 0, Qt.AlignTop)
        lay.addLayout(top)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.grp = QButtonGroup(self)
        self.btn_erase = self._tool_button("Ластик", "eraser", "Стереть лишнее (E)")
        self.btn_restore = self._tool_button("Вернуть чернила", "brush",
                                             "Вернуть штрихи из исходного скана (B)")
        self.btn_erase.setChecked(True)
        for b in (self.btn_erase, self.btn_restore):
            self.grp.addButton(b)
            tools.addWidget(b)
        self.grp.buttonToggled.connect(lambda *_: self._tool_changed())
        tools.addSpacing(10)
        tools.addWidget(QLabel("Размер:"))
        self.sl_size = QSlider(Qt.Horizontal)
        self.sl_size.setRange(BRUSH_MIN, BRUSH_MAX)
        self.sl_size.setValue(BRUSH_DEFAULT)
        self.sl_size.setMinimumWidth(90)
        self.sl_size.setToolTip("Размер кисти: [ — меньше, ] — больше (или Alt + колесо мыши)")
        self.sl_size.valueChanged.connect(self._size_changed)
        tools.addWidget(self.sl_size, 1)
        self.chk_orig = QCheckBox("Исходник под фрагментом")
        self.chk_orig.setToolTip("Бледно показать исходный скан под фрагментом, чтобы видеть, что можно вернуть (O)")
        self.chk_orig.toggled.connect(self._show_orig)
        tools.addWidget(self.chk_orig)
        lay.addLayout(tools)

        self.view = RetouchView()
        sc = self.view.scene()
        self.bg_item = QGraphicsPixmapItem(np_to_pixmap(checker(h, w, style.is_dark())))
        self.orig_item = QGraphicsPixmapItem(np_to_pixmap(self.crop))
        self.orig_item.setOpacity(0.28)
        self.orig_item.setVisible(False)
        self.res_item = QGraphicsPixmapItem()
        for it in (self.bg_item, self.orig_item, self.res_item):
            it.setTransformationMode(Qt.SmoothTransformation)
            sc.addItem(it)
        sc.addItem(self.view.cursor_item)
        sc.setSceneRect(QRectF(0, 0, w, h))
        self.view.strokeStarted.connect(self._start)
        self.view.strokeMoved.connect(self._move)
        self.view.strokeEnded.connect(self._end)
        self.view.brushWheel.connect(lambda d: self.sl_size.setValue(self.sl_size.value() + 2 * d))
        lay.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_undo = big_button("", self.undo, icon_name="undo-2", ghost=True, tip="Отменить мазок (Ctrl + Z)")
        self.btn_redo = big_button("", self.redo, icon_name="redo-2", ghost=True,
                                   tip="Повторить (Ctrl + Shift + Z или Ctrl + Y)")
        self.btn_reset = big_button("Сбросить к автоматическому", self.reset, icon_name="refresh-ccw", ghost=True,
                                    tip="Убрать все ручные правки — как вырезалось автоматически")
        self.btn_fit = big_button("", self.view.fit, icon_name="maximize", ghost=True,
                                  tip="По размеру окна (Ctrl + 0). Масштаб — Ctrl + колесо мыши")
        for b in (self.btn_undo, self.btn_redo, self.btn_fit, self.btn_reset):
            bottom.addWidget(b)
        bottom.addStretch(1)
        self.btn_cancel = big_button("Отмена", self.reject, tip="Закрыть без изменений (Esc)")
        self.btn_ok = big_button("Готово", self.accept, primary=True, icon_name="check",
                                 tip="Применить правки к фрагменту")
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_ok)
        lay.addLayout(bottom)

        for seq, fn in (("Ctrl+Z", self.undo), ("Ctrl+Shift+Z", self.redo), ("Ctrl+Y", self.redo),
                        ("[", self.smaller), ("]", self.bigger),
                        ("Х", self.smaller), ("Ъ", self.bigger),          # те же клавиши в русской раскладке
                        ("E", lambda: self.btn_erase.setChecked(True)), ("У", lambda: self.btn_erase.setChecked(True)),
                        ("B", lambda: self.btn_restore.setChecked(True)),
                        ("И", lambda: self.btn_restore.setChecked(True)),
                        ("O", lambda: self.chk_orig.toggle()), ("Щ", lambda: self.chk_orig.toggle()),
                        ("Ctrl+0", self.view.fit),
                        ("Ctrl+=", lambda: self.view.zoom(1.25)), ("Ctrl++", lambda: self.view.zoom(1.25)),
                        ("Ctrl+-", lambda: self.view.zoom(0.8))):
            QShortcut(QKeySequence(seq), self, activated=fn)

        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.btn_ok.setDefault(True)
        self.view.setFocus()
        self.setMinimumHeight(380)
        if parent is not None and parent.window() is not None:
            pw = parent.window().size()
            self.resize(max(560, int(pw.width() * 0.8)), max(400, int(pw.height() * 0.8)))
        else:
            self.resize(900, 600)
        self._size_changed(self.sl_size.value())
        self._tool_changed()
        self._render()

    # ---------- кнопки ----------
    def _tool_button(self, text, icon_name, tip):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setIcon(style.icon(icon_name))
        b.setProperty("kind", "secondary")
        b.setProperty("icon_name", icon_name)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def smaller(self):
        self.sl_size.setValue(self.sl_size.value() - 2)

    def bigger(self):
        self.sl_size.setValue(self.sl_size.value() + 2)

    def tool(self) -> str:
        return rt.RESTORE if self.btn_restore.isChecked() else rt.ERASE

    def _tool_changed(self):
        """Выбранный инструмент — синяя кнопка (как основная), второй — обычная."""
        for b in (self.btn_erase, self.btn_restore):
            on = b.isChecked()
            b.setProperty("kind", "primary" if on else "secondary")
            b.setIcon(style.icon(b.property("icon_name"), "#ffffff" if on else None))
            b.style().unpolish(b)
            b.style().polish(b)
        self._update_cursor_color()

    def _update_cursor_color(self):
        self.view.set_cursor_color("#e53935" if self.tool() == rt.ERASE else style.colors()["accent"])

    def _size_changed(self, v):
        self.view.radius = v
        if self.view.cursor_item.isVisible():
            c = self.view.cursor_item.rect().center()
            self.view.place_cursor(c)

    def _show_orig(self, on):
        self.orig_item.setVisible(on)

    # ---------- правки ----------
    def strokes(self) -> tuple:
        return self.hist[self.pos]

    def _push(self, strokes: tuple):
        self.hist = self.hist[:self.pos + 1] + [strokes]
        self.pos += 1
        self._lab_cache = None
        self._render()

    def undo(self):
        if self.pos > 0:
            self.pos -= 1
            self._lab_cache = None
            self._render()

    def redo(self):
        if self.pos < len(self.hist) - 1:
            self.pos += 1
            self._lab_cache = None
            self._render()

    def reset(self):
        if self.strokes():
            self._push(())

    def _stroke_from(self, pts) -> rt.Stroke:
        W = self.page_px[0]
        return rt.Stroke(self.tool(), self.view.radius / W,
                         tuple(rt.page_point(x, y, self.box, self.page_px) for x, y in pts))

    def _start(self, p: QPointF):
        self._cur = [(p.x(), p.y())]
        self._render(live=True)

    def _move(self, p: QPointF):
        if not self._cur:
            return
        lx, ly = self._cur[-1]
        if (p.x() - lx) ** 2 + (p.y() - ly) ** 2 < max(1.0, self.view.radius * 0.25) ** 2:
            return
        self._cur.append((p.x(), p.y()))
        self._render(live=True)

    def _end(self):
        if self._cur:
            s = self._stroke_from(self._cur)
            self._cur = []
            self._push(self.strokes() + (s,))

    def _labels(self):
        if self._lab_cache is None:
            self._lab_cache = rt.rasterize(self.strokes(), self.box, self.page_px)
        return self._lab_cache

    def composed(self, live: bool = False) -> np.ndarray:
        lab = self._labels()
        if live and self._cur:
            cur = rt.rasterize((self._stroke_from(self._cur),), self.box, self.page_px)
            lab = lab.copy()
            lab[cur > 0] = cur[cur > 0]
        return rt.apply(self.auto, self.crop, (), self.box, self.page_px, self.mode, self.keep, labels=lab)

    def _render(self, live: bool = False):
        self.res_item.setPixmap(np_to_pixmap(self.composed(live)))
        n = len(self.strokes())
        self.lbl_count.setText(f"Ручных правок: {n}" if n else "Без ручных правок")
        self.btn_undo.setEnabled(self.pos > 0)
        self.btn_redo.setEnabled(self.pos < len(self.hist) - 1)
        self.btn_reset.setEnabled(bool(n))

    def showEvent(self, e):
        super().showEvent(e)
        self.view.fit()
