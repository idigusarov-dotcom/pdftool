"""Постраничный просмотр результата с масштабом и переходом по номеру."""
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from ..core.logsetup import log
from .widgets import PageView, zoom_bar


def open_preview(parent: QWidget, title: str, object_name: str, page_label: str, count: int,
                 render_page: Callable[[int], tuple[QPixmap, str]], start: int = 0):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setObjectName(object_name)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    available = parent.window().screen().availableGeometry().size()
    dlg.resize(QSize(900, 800).boundedTo(parent.window().size()).boundedTo(available - QSize(40, 60)))
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(16, 14, 16, 14)
    nav = QHBoxLayout()
    previous = QPushButton("Назад")
    following = QPushButton("Далее")
    previous.setAutoDefault(False)
    following.setAutoDefault(False)
    page = QSpinBox()
    page.setAccessibleName(page_label)
    page.setRange(1, count)
    page.setSuffix(f" из {count}")
    page.setKeyboardTracking(False)
    page.setValue(start + 1)
    for widget in (QLabel(page_label), page, previous, following):
        nav.addWidget(widget)
    nav.addStretch()
    layout.addLayout(nav)
    source = QLabel()
    source.setTextFormat(Qt.PlainText)
    source.setWordWrap(True)
    layout.addWidget(source)
    view = PageView()
    view.draw_enabled = False
    view.setDragMode(PageView.ScrollHandDrag)
    layout.addWidget(view, 1)
    footer = zoom_bar(view)
    close = QDialogButtonBox(QDialogButtonBox.Close)
    close.button(QDialogButtonBox.Close).setText("Закрыть")
    close.button(QDialogButtonBox.Close).setAutoDefault(False)
    close.rejected.connect(dlg.reject)
    footer.addWidget(close)
    layout.addLayout(footer)
    previous.clicked.connect(page.stepDown)
    following.clicked.connect(page.stepUp)
    previous.setToolTip("Предыдущая страница (Page Up)")
    following.setToolTip("Следующая страница (Page Down)")
    QShortcut(QKeySequence(Qt.Key_PageUp), dlg, activated=page.stepDown)
    QShortcut(QKeySequence(Qt.Key_PageDown), dlg, activated=page.stepUp)

    def render(number):
        previous.setEnabled(number > 1)
        following.setEnabled(number < count)
        try:
            pixmap, caption = render_page(number - 1)
        except Exception as exc:
            log.exception("Предпросмотр")
            view.hide()
            source.setText(f"Не удалось показать страницу {number}: {exc}")
            return
        source.setText(caption)
        view.show()
        view.set_page(pixmap)

    page.valueChanged.connect(render)
    dlg.open()
    page.setFocus()
    QTimer.singleShot(0, dlg, lambda: render(page.value()))
