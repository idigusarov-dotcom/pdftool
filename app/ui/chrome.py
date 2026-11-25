"""Элементы оформления экранов: заголовок с шагами, «Дополнительно», зона перетаскивания, уведомления."""
from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from . import style


def _repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    for ch in w.findChildren(QWidget):
        ch.style().unpolish(ch)
        ch.style().polish(ch)


class StepBar(QWidget):
    """«1. Откройте → 2. Проверьте → 3. Сохраните» с состоянием done/active/todo.
    В узком окне у неактивных шагов остаются только кружки с номерами (текст — во всплывающей подсказке),
    чтобы строка шагов не раздвигала окно вбок."""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("StepBar")
        h = QHBoxLayout(self)
        h.setSizeConstraint(QLayout.SetNoConstraint)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.items: list[QFrame] = []
        for i, text in enumerate(steps):
            if i:
                a = QLabel("→")
                a.setObjectName("StepArrow")
                h.addWidget(a)
            f = QFrame()
            f.setObjectName("Step")
            fl = QHBoxLayout(f)
            fl.setContentsMargins(4, 4, 12, 4)
            fl.setSpacing(8)
            n = QLabel(str(i + 1))
            n.setObjectName("StepNum")
            t = QLabel(text)
            t.setObjectName("StepText")
            f.setToolTip(f"Шаг {i + 1}: {text}")
            fl.addWidget(n)
            fl.addWidget(t)
            h.addWidget(f)
            self.items.append(f)
        h.addStretch()
        self._done = -1
        self.set_done(0)

    def set_done(self, n_done: int):
        """n_done — сколько первых шагов выполнено; следующий — активный."""
        if n_done == self._done:
            return
        self._done = n_done
        for i, f in enumerate(self.items):
            st = "done" if i < n_done else ("active" if i == n_done else "todo")
            f.setProperty("state", st)
            f.findChild(QLabel, "StepNum").setText("✓" if st == "done" else str(i + 1))
            _repolish(f)
        self._fit()

    def _texts(self):
        return [f.findChild(QLabel, "StepText") for f in self.items]

    def _full_width(self) -> int:
        lay = self.layout()
        w = 0
        for i in range(lay.count()):
            it = lay.itemAt(i)
            wd = it.widget()
            if wd is None:
                continue
            if wd in self.items:
                fl = wd.layout()
                m = fl.contentsMargins()
                w += m.left() + m.right() + fl.spacing() + sum(
                    wd.findChild(QLabel, n).sizeHint().width() for n in ("StepNum", "StepText"))
            else:
                w += wd.sizeHint().width()
            w += lay.spacing()
        return w

    def _fit(self):
        compact = self.width() > 0 and self._full_width() > self.width()
        for i, t in enumerate(self._texts()):
            t.setVisible(not compact or self.items[i].property("state") == "active")

    def minimumSizeHint(self):
        return QSize(min(self._full_width(), 320), super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(self._full_width(), super().sizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def state(self, i: int) -> str:
        return self.items[i].property("state")


class ScreenHeader(QWidget):
    """Заголовок экрана: название, пояснение, шаги и кнопка «?» (тур по экрану)."""
    help_clicked = Signal()

    def __init__(self, title: str, subtitle: str, steps: list[str], parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(4)
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("ScreenTitle")
        top.addWidget(self.title)
        top.addStretch()
        self.help = QToolButton()
        self.help.setObjectName("HelpButton")
        self.help.setText("Как пользоваться")
        self.help.setIcon(style.icon("circle-help", style.colors()["accent"]))
        self.help.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.help.setToolTip("Пошаговая подсказка: покажет, куда и зачем нажимать на этом экране")
        self.help.clicked.connect(self.help_clicked)
        top.addWidget(self.help)
        v.addLayout(top)
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("ScreenSub")
        self.sub.setWordWrap(True)
        v.addWidget(self.sub)
        self.steps = StepBar(steps)
        v.addWidget(self.steps)

    def retheme(self):
        self.help.setIcon(style.icon("circle-help", style.colors()["accent"]))


class Collapsible(QFrame):
    """Сворачиваемый блок «Дополнительно»."""
    toggled = Signal(bool)

    def __init__(self, title: str = "Дополнительно", parent=None, expanded: bool = False):
        super().__init__(parent)
        self.setObjectName("Advanced")
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 2, 6, 6)
        v.setSpacing(4)
        self.head = QPushButton(title)
        self.head.setObjectName("AdvancedHead")
        self.head.setCheckable(True)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setToolTip("Показать или скрыть дополнительные настройки")
        self.head.toggled.connect(self.set_expanded)
        v.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(6, 0, 6, 4)
        self.body_lay.setSpacing(8)
        v.addWidget(self.body)
        self._title = title
        self.set_expanded(expanded)

    def add(self, w):
        if isinstance(w, QWidget):
            self.body_lay.addWidget(w)
        else:
            self.body_lay.addLayout(w)

    def set_expanded(self, on: bool):
        self.head.blockSignals(True)
        self.head.setChecked(on)
        self.head.blockSignals(False)
        self.body.setVisible(on)
        self.head.setIcon(style.icon("chevron-down" if on else "chevron-right", style.colors()["muted"]))
        self.toggled.emit(on)

    def is_expanded(self) -> bool:
        return self.head.isChecked()


def pdf_paths(mime, exts=(".pdf",)) -> list[str]:
    if not mime.hasUrls():
        return []
    out = []
    for u in mime.urls():
        p = u.toLocalFile()
        if p and os.path.splitext(p)[1].lower() in exts and os.path.isfile(p):
            out.append(p)
    return out


class ElideLabel(QLabel):
    """Надпись в одну строку, которая в узком окне сокращается многоточием посередине
    (длинное имя файла не раздвигает окно). text() возвращает полный текст."""

    def minimumSizeHint(self):
        return QSize(60, super().minimumSizeHint().height())

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        r = self.contentsRect()
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        p.drawText(r, int(self.alignment()) | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, r.width()))
        p.end()


class PdfDropFrame(QFrame):
    files = Signal(list)

    def __init__(self, parent=None, exts=(".pdf",)):
        super().__init__(parent)
        self.exts = exts
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if pdf_paths(e.mimeData(), self.exts):
            e.acceptProposedAction()
            self.setProperty("hover", "true")
            _repolish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("hover", "false")
        _repolish(self)

    def dropEvent(self, e):
        self.setProperty("hover", "false")
        _repolish(self)
        ps = pdf_paths(e.mimeData(), self.exts)
        if ps:
            e.acceptProposedAction()
            self.files.emit(ps)


class FileOpenButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            event.accept()
            if not event.isAutoRepeat():
                self.click()
        else:
            super().keyPressEvent(event)


class FileStartPanel(PdfDropFrame):
    """Основное действие до открытия документа. Вся панель принимает файлы."""
    def __init__(self, title, hint, action, choose):
        super().__init__()
        self.setObjectName("FileStart")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        layout.addStretch()
        logo = QLabel()
        pm = QPixmap(style.asset("icons", "app-logo.png"))
        pm.setDevicePixelRatio(pm.width() / 80)
        logo.setPixmap(pm)
        layout.addWidget(logo, 0, Qt.AlignHCenter)
        heading = QLabel(title)
        heading.setObjectName("StartTitle")
        layout.addWidget(heading, 0, Qt.AlignHCenter)
        description = QLabel(hint)
        description.setObjectName("ScreenSub")
        description.setAlignment(Qt.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addSpacing(6)
        self.button = FileOpenButton(action)
        self.button.setProperty("kind", "primary")
        self.button.setToolTip(title)
        self.button.setMinimumWidth(152)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(choose)
        layout.addWidget(self.button, 0, Qt.AlignHCenter)
        layout.addStretch()


class DropZone(PdfDropFrame):
    """Плашка «Перетащите PDF сюда или нажмите»; после загрузки показывает имя файла."""
    clicked = Signal()

    def __init__(self, title: str, hint: str = "Перетащите PDF сюда или нажмите, чтобы выбрать",
                 exts=(".pdf",), icon_name: str = "file-plus", parent=None):
        super().__init__(parent, exts)
        self.setObjectName("DropZone")
        self.setFocusPolicy(Qt.StrongFocus)
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)
        self._title, self._hint, self._icon = title, hint, icon_name
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(12)
        self.ico = QLabel()
        h.addWidget(self.ico)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.t = ElideLabel(title)
        self.t.setObjectName("DropTitle")
        self.s = QLabel(hint)
        self.s.setObjectName("DropSub")
        self.s.setWordWrap(True)
        v.addWidget(self.t)
        v.addWidget(self.s)
        h.addLayout(v, 1)
        for label in (self.ico, self.t, self.s):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setToolTip(hint)
        self.set_loaded("")

    def retheme(self):
        loaded = self.property("loaded") == "true"
        self.ico.setPixmap(style.icon("circle-check" if loaded else self._icon,
                                      style.colors()["success" if loaded else "accent"]).pixmap(QSize(26, 26)))

    def set_loaded(self, text: str):
        self.setProperty("loaded", "true" if text else "false")
        self.t.setText(self._title + (": " + text if text else ""))
        self.t.setToolTip(self.t.text())
        self.s.setText("Чтобы заменить — перетащите другой файл или нажмите" if text else self._hint)
        self.setAccessibleName(self.t.text())
        self.setAccessibleDescription(self.s.text())
        self.retheme()
        _repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = True
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            e.accept()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit()
        else:
            super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            e.accept()
            if not e.isAutoRepeat():
                self.clicked.emit()
        else:
            super().keyPressEvent(e)

class Toast(QFrame):
    """Всплывающее уведомление внизу справа; исчезает само."""

    def __init__(self, host: QWidget, text: str, kind: str = "info", action: tuple | None = None,
                 timeout_ms: int = 5000):
        super().__init__(host)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(10)
        ic = QLabel()
        symbol, color = {"error": ("triangle-alert", "white"), "ok": ("circle-check", style.colors()["success"])}.get(
            kind, ("info", style.colors()["accent"]))
        ic.setPixmap(style.icon(symbol, color).pixmap(QSize(20, 20)))
        h.addWidget(ic)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(460)
        h.addWidget(self.label, 1)
        if action:
            b = QPushButton(action[0])
            b.clicked.connect(lambda: (action[1](), self.close_now()))
            h.addWidget(b)
        x = QPushButton("✕")
        x.setToolTip("Закрыть")
        x.setAccessibleName("Закрыть уведомление")
        x.clicked.connect(self.close_now)
        h.addWidget(x)
        _repolish(self)
        self.adjustSize()
        self.eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.eff)
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.setDuration(180)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.show()
        self.raise_()
        self.anim.start()
        self._closed = False
        self._host_window = host.window()
        if timeout_ms:
            QTimer.singleShot(timeout_ms, self, self.close_now)   # таймер умирает вместе с уведомлением

    def close_now(self):
        if self._closed:
            return
        self._closed = True
        win = self._host_window
        self.hide()
        self.deleteLater()
        if win is not None and hasattr(win, "_toasts"):
            win._toasts = [t for t in win._toasts if t is not self]
            win.place_toasts()
