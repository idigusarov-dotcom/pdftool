"""Пошаговая подсказка: затемнение, «прожектор» на нужном элементе и пояснение с кнопками."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import style


@dataclass
class Step:
    target: Optional[QWidget]          # None — пояснение по центру без прожектора
    title: str
    text: str
    prepare: Optional[Callable[[], None]] = None   # например, раскрыть «Дополнительно»


def _parse_rgba(s: str) -> QColor:
    if s.startswith("rgba"):
        r, g, b, a = [int(x) for x in s[5:-1].split(",")]
        return QColor(r, g, b, a)
    return QColor(s)


class TourOverlay(QWidget):
    finished = Signal()

    def __init__(self, host: QWidget, steps: list[Step], name: str = ""):
        super().__init__(host)
        self.host, self.steps, self.name = host, steps, name
        self.i = 0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.bubble = QFrame(self)
        self.bubble.setObjectName("TourBubble")
        v = QVBoxLayout(self.bubble)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        self.count = QLabel()
        self.count.setObjectName("TourCount")
        self.title = QLabel()
        self.title.setObjectName("TourTitle")
        self.title.setWordWrap(True)
        self.text = QLabel()
        self.text.setObjectName("TourText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        v.addWidget(self.count)
        v.addWidget(self.title)
        v.addWidget(self.text)
        h = QHBoxLayout()
        self.b_close = QPushButton("Закрыть")
        self.b_close.setProperty("kind", "ghost")
        self.b_close.setToolTip("Закончить подсказку (Esc)")
        self.b_back = QPushButton("Назад")
        self.b_back.setToolTip("Предыдущий шаг (стрелка влево)")
        self.b_next = QPushButton("Далее")
        self.b_next.setProperty("kind", "primary")
        self.b_next.setToolTip("Следующий шаг (стрелка вправо или Enter)")
        self.b_close.clicked.connect(self.close_tour)
        self.b_back.clicked.connect(self.back)
        self.b_next.clicked.connect(self.next)
        h.addWidget(self.b_close)
        h.addStretch()
        h.addWidget(self.b_back)
        h.addWidget(self.b_next)
        v.addLayout(h)
        self.bubble.setFixedWidth(380)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.go(0)

    # ---------- навигация ----------
    def go(self, i: int):
        self.i = max(0, min(i, len(self.steps) - 1))
        st = self.steps[self.i]
        if st.prepare:
            st.prepare()
        self.count.setText(f"Шаг {self.i + 1} из {len(self.steps)}")
        self.title.setText(st.title)
        self.text.setText(st.text)
        self.b_back.setEnabled(self.i > 0)
        self.b_next.setText("Готово" if self.i == len(self.steps) - 1 else "Далее")
        QTimer.singleShot(0, self._reveal)    # дать виджету раскрыться
        self._reveal()

    def _reveal(self):
        """Прокрутить экран так, чтобы подсвеченный элемент был виден (маленькое окно/крупный шрифт)."""
        st = self.steps[self.i] if self.steps else None
        t = st.target if st else None
        if t is not None and t.isVisible():
            from PySide6.QtWidgets import QScrollArea
            p = t.parentWidget()
            while p is not None and p is not self.host:
                if isinstance(p, QScrollArea) and p.widget() is not None and p.widget().isAncestorOf(t):
                    p.ensureWidgetVisible(t, 20, 20)
                p = p.parentWidget()
        self.relayout()

    def next(self):
        if self.i >= len(self.steps) - 1:
            return self.close_tour()
        self.go(self.i + 1)

    def back(self):
        self.go(self.i - 1)

    def close_tour(self):
        self.host.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        self.finished.emit()

    # ---------- геометрия ----------
    def target_rect(self) -> QRect:
        t = self.steps[self.i].target
        if t is None or not t.isVisible():
            return QRect()
        tl = t.mapTo(self.host, QPoint(0, 0))
        r = QRect(tl, t.size()).adjusted(-6, -6, 6, 6)
        return r.intersected(self.rect().adjusted(4, 4, -4, -4))

    def relayout(self):
        if not self.isVisible():
            return
        self.setGeometry(self.host.rect())
        self.bubble.adjustSize()
        bw, bh = self.bubble.width(), self.bubble.sizeHint().height()
        W, H = self.width(), self.height()
        r = self.target_rect()
        m = 14
        if r.isNull():
            x, y = (W - bw) // 2, (H - bh) // 2
        else:
            cands = [(r.center().x() - bw // 2, r.bottom() + m),        # снизу
                     (r.center().x() - bw // 2, r.top() - m - bh),      # сверху
                     (r.right() + m, r.center().y() - bh // 2),         # справа
                     (r.left() - m - bw, r.center().y() - bh // 2)]     # слева
            x, y = cands[0]
            for cx, cy in cands:
                fits = cx >= 8 and cy >= 8 and cx + bw <= W - 8 and cy + bh <= H - 8
                if fits:
                    x, y = cx, cy
                    break
            else:   # не помещается ни с одной стороны — в угол, подальше от цели
                x = 16 if r.center().x() > W // 2 else W - bw - 16
                y = H - bh - 16
        x = max(8, min(x, W - bw - 8))
        y = max(8, min(y, H - bh - 8))
        self.bubble.setGeometry(x, y, bw, bh)
        self.bubble.raise_()
        self.update()

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() in (QEvent.Resize, QEvent.Move):
            QTimer.singleShot(0, self.relayout)
        return False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        r = self.target_rect()
        if not r.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(r), 10, 10)
            path = path.subtracted(hole)
        p.fillPath(path, _parse_rgba(style.colors()["dim"]))
        if not r.isNull():
            p.setPen(QPen(QColor(style.colors()["accent"]), 3))
            p.drawRoundedRect(QRectF(r), 10, 10)

    def mousePressEvent(self, e):
        e.accept()   # клики мимо подсказки не проходят в программу

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.close_tour()
        elif k in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.next()
        elif k == Qt.Key_Left:
            self.back()
