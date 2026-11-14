"""Точка входа: окно с вкладками, меню, журнал ошибок."""
from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import APP_NAME, __version__
from .core.logsetup import log, log_dir, setup_logging
from .ui import style
from .ui.chrome import Collapsible, DropZone, Toast, pdf_paths
from .ui.helppage import HelpPage
from .ui.infra import settings
from .ui.tab_batch import BatchTab
from .ui.tab_fragment import FragmentTab
from .ui.tour import TourOverlay
from .ui.tab_image import ImageTab
from .ui.tab_pages import PagesTab
from .ui.tab_sign import SignTab
from .ui.tab_sort import SortTab


SCREENS = [("sign", "Подписи", "pen-tool", "Перенос подписей из актов в расписки"),
           ("batch", "Пакет", "layers", "Много пар файлов за один раз"),
           ("fragment", "Фрагменты", "scissors", "Вырезать подпись/печать из одного PDF и поставить в другой"),
           ("sort", "Сортировка", "list-ordered", "Разложить страницы по номерам квартир"),
           ("image", "Картинка", "image", "Поставить печать или подпись-картинку"),
           ("pages", "Страницы", "files", "Объединить, повернуть, удалить, разделить"),
           ("help", "Справка", "book-open-check", "Как пользоваться программой")]


class ScreenStack(QStackedWidget):
    """Экраны в прокручиваемых областях: на маленьком экране окно не растёт, а появляется прокрутка."""

    def __init__(self):
        super().__init__()
        self._screens: list[QWidget] = []

    def add_screen(self, w: QWidget):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        self._screens.append(w)
        return self.addWidget(sa)

    def currentWidget(self):
        i = self.currentIndex()
        return self._screens[i] if 0 <= i < len(self._screens) else None

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self._screens.index(w))

    def indexOf(self, w):
        return self._screens.index(w) if w in self._screens else super().indexOf(w)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — версия {__version__}")
        self.setAcceptDrops(True)
        self._toasts: list = []
        self.tour = None
        self.sign = SignTab()
        self.sort = SortTab()
        self.image = ImageTab()
        self.pages = PagesTab()
        self.fragment = FragmentTab()
        self.batch = BatchTab(self.sign, lambda: self.show_screen(self.sign))
        for bar in (self.sign.tbar, self.sort.tbar):
            bar.changed.connect(self.templates_changed)
        self.helppage = HelpPage([("Подписи", self.sign), ("Пакет", self.batch),
                                  ("Фрагменты (вырезать и перенести)", self.fragment), ("Сортировка", self.sort),
                                  ("Картинка", self.image), ("Страницы", self.pages)], self.start_tour)

        root = QWidget()
        root.setObjectName("Page")
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self.tabs = ScreenStack()   # имя «tabs» оставлено для совместимости со скриптами
        for key, *_ in SCREENS:
            w = self.helppage if key == "help" else getattr(self, key)
            w.setObjectName("Page")
            self.tabs.add_screen(w)
            w.header.help_clicked.connect(self.start_tour)
        self.tabs.currentChanged.connect(self._screen_changed)
        body.addWidget(self.tabs, 1)
        rv.addLayout(body, 1)
        self.setCentralWidget(root)
        self._actions()
        self._step_timer = QTimer(self, interval=400, timeout=self.refresh_steps)
        self._step_timer.start()
        self.resize(1366, 768)
        self.show_screen(self.sign)
        self._screen_changed(0)

    # ---------- каркас ----------
    def _build_header(self):
        h = QFrame()
        h.setObjectName("Header")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(18, 10, 14, 10)
        logo = QLabel()
        logo.setPixmap(style.icon("file-text", style.colors()["accent"]).pixmap(QSize(26, 26)))
        self._logo = logo
        lay.addWidget(logo)
        t = QLabel("PDF-помощник")
        t.setObjectName("AppTitle")
        lay.addWidget(t)
        v = QLabel(f"АкваТехСтрой · версия {__version__}")
        v.setObjectName("AppVersion")
        lay.addWidget(v)
        lay.addStretch()
        self.btn_undo = self._tool("undo-2", "Отменить последнее действие (Ctrl + Z)", self.undo)
        self.btn_redo = self._tool("redo-2", "Повторить (Ctrl + Y)", self.redo)
        self.btn_theme = self._tool("moon", "Тёмная / светлая тема", self.toggle_theme)
        self.btn_more = self._tool("info", "Ещё: журнал ошибок, о программе", None)
        m = QMenu(self)
        m.addAction("Открыть папку с логами", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        m.addAction("Справка", lambda: self.show_screen(self.helppage))
        m.addAction("О программе", self.about)
        self.btn_more.setMenu(m)
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        self.btn_help = QToolButton()
        self.btn_help.setObjectName("HelpButton")
        self.btn_help.setText("?")
        self.btn_help.setToolTip("Показать, куда и зачем нажимать на этом экране")
        self.btn_help.clicked.connect(self.start_tour)
        for b in (self.btn_undo, self.btn_redo, self.btn_theme, self.btn_more, self.btn_help):
            lay.addWidget(b)
        return h

    def _tool(self, name, tip, slot):
        b = QToolButton()
        b.setIcon(style.icon(name))
        b.setProperty("icon_name", name)
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        if slot:
            b.clicked.connect(slot)
        return b

    def _build_sidebar(self):
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(196)
        v = QVBoxLayout(sb)
        v.setContentsMargins(10, 14, 10, 14)
        v.setSpacing(4)
        self.nav = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for i, (key, label, ic, tip) in enumerate(SCREENS):
            if key == "help":
                v.addStretch()
            b = QPushButton(label)
            b.setCheckable(True)
            b.setIcon(style.icon(ic))
            b.setProperty("icon_name", ic)
            b.setIconSize(QSize(20, 20))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self.tabs.setCurrentIndex(k))
            grp.addButton(b)
            v.addWidget(b)
            self.nav[key] = b
        return sb

    def _actions(self):
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.redo), (QKeySequence.HelpContents, self.start_tour)):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(fn)
            self.addAction(a)

    def all_tabs(self):
        return [self.sign, self.batch, self.fragment, self.sort, self.image, self.pages]

    def show_screen(self, w):
        self.tabs.setCurrentWidget(w)

    def current(self):
        return self.tabs.currentWidget()

    def undo(self):
        w = self.current()
        if hasattr(w, "undo_stack"):
            w.undo_stack.undo()

    def redo(self):
        w = self.current()
        if hasattr(w, "undo_stack"):
            w.undo_stack.redo()

    def _screen_changed(self, i):
        key = SCREENS[i][0]
        self.nav[key].setChecked(True)
        self.refresh_steps()
        if key == "help":
            self.helppage.refresh()
        QTimer.singleShot(300, lambda: self.offer_tour(key))

    def refresh_steps(self):
        w = self.current()
        if hasattr(w, "step_index") and w.header.steps.items:
            w.header.steps.set_done(w.step_index())

    # ---------- подсказки ----------
    def offer_tour(self, key: str):
        """При первом открытии экрана предлагаем пошаговую подсказку (один раз)."""
        if key == "help" or self.tour is not None or not self.isVisible():
            return
        s = settings()
        if s.value(f"tour/{key}", False, type=bool):
            return
        s.setValue(f"tour/{key}", True)
        if SCREENS[self.tabs.currentIndex()][0] != key:
            return
        self.toast(f"Впервые на экране «{SCREENS[self.tabs.currentIndex()][1]}»? Покажу, куда и зачем нажимать.",
                   "info", action=("Показать", self.start_tour), timeout_ms=12000)

    def start_tour(self):
        if self.tour is not None:
            self.tour.close_tour()
        w = self.current()
        opened = {c: c.is_expanded() for c in w.findChildren(Collapsible)}
        steps = [st for st in w.tour_steps() if st.target is None or st.target.window() is self]
        self.tour = TourOverlay(self.centralWidget(), steps, SCREENS[self.tabs.currentIndex()][0])
        def done():
            self.tour = None
            for c, was in opened.items():   # вернуть «Дополнительно» как было
                c.set_expanded(was)
        self.tour.finished.connect(done)
        return self.tour

    # ---------- уведомления ----------
    def toast(self, text: str, kind: str = "info", action=None, timeout_ms: int = 6000):
        t = Toast(self.centralWidget(), text, kind, action, timeout_ms)
        self._toasts.append(t)
        self.place_toasts()
        log.info("Уведомление: %s", text.replace("\n", " "))
        return t

    def place_toasts(self):
        host = self.centralWidget()
        y = host.height() - 18
        for t in reversed(self._toasts):
            t.adjustSize()
            y -= t.height()
            t.move(host.width() - t.width() - 22, y)
            t.raise_()
            y -= 10

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_toasts()

    # ---------- тема ----------
    def toggle_theme(self):
        dark = not style.is_dark()
        settings().setValue("ui/dark", dark)
        style.apply_theme(QApplication.instance(), dark)
        self.retheme()

    def retheme(self):
        self.btn_theme.setProperty("icon_name", "sun" if style.is_dark() else "moon")
        for b in self.findChildren(QToolButton) + self.findChildren(QPushButton):
            n = b.property("icon_name")
            if n:
                b.setIcon(style.icon(n, "#ffffff" if b.property("kind") == "primary" else None))
        self._logo.setPixmap(style.icon("file-text", style.colors()["accent"]).pixmap(QSize(26, 26)))
        for w in self.all_tabs() + [self.helppage]:
            w.header.retheme()
            if hasattr(w, "refresh_marks"):
                w.refresh_marks()
        self.batch.fill()
        for dz in self.findChildren(DropZone):
            dz.retheme()
        for c in self.findChildren(Collapsible):
            c.set_expanded(c.is_expanded())
        from PySide6.QtGui import QColor
        from .ui.widgets import PageView
        for v in self.findChildren(PageView):
            v.setBackgroundBrush(QColor("#3a3f4b" if style.is_dark() else "#e6e9f0"))

    # ---------- перетаскивание файлов на окно ----------
    def dragEnterEvent(self, e):
        if pdf_paths(e.mimeData(), (".pdf", ".png", ".jpg", ".jpeg")):
            e.acceptProposedAction()

    def dropEvent(self, e):
        ps = pdf_paths(e.mimeData(), (".pdf", ".png", ".jpg", ".jpeg"))
        w = self.current()
        if ps and hasattr(w, "accept_files"):
            e.acceptProposedAction()
            w.accept_files(ps)

    def templates_changed(self):
        for bar in (self.sign.tbar, self.sort.tbar):
            bar.reload()
        self.batch.refresh_templates()

    def about(self):
        QMessageBox.about(self, "О программе", f"{APP_NAME}\nВерсия {__version__}\n\n"
                          f"Журнал ошибок: {log_dir()}\n\nШрифт Inter (SIL OFL 1.1), иконки Lucide (ISC).")

    def closeEvent(self, e):
        names = {self.sign: "Подписи", self.batch: "Пакет", self.sort: "Сортировка", self.image: "Картинка",
                 self.pages: "Страницы", self.fragment: "Фрагменты"}
        dirty = [names[t] for t in self.all_tabs() if t.is_dirty()]
        if dirty:
            ans = QMessageBox.question(
                self, "Есть несохранённые изменения",
                "Изменения не сохранены на экранах:\n• " + "\n• ".join(dirty) + "\n\nВсё равно закрыть программу?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                e.ignore()
                return
        e.accept()


def install_excepthook():
    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        log.error("Необработанная ошибка:\n%s", text)
        try:
            QMessageBox.critical(None, "Ошибка",
                                 "Что-то пошло не так, но ваши файлы не изменены.\n\n"
                                 f"Причина: {value}\n\nПодробности записаны в журнал:\n"
                                 f"{os.path.join(log_dir(), 'log.txt')}\n(меню «Справка» → «Открыть папку с логами»)")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = hook


def setup_app(app):
    """Шрифт и тема — до создания окна."""
    fam = style.load_fonts()
    f = QFont(fam)
    f.setPointSize(10)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    style.apply_theme(app, settings().value("ui/dark", False, type=bool))


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":   # проверка сборки (CI, поддержка)
        from .selftest import run
        return run(sys.argv[2] if len(sys.argv) > 2 else os.getcwd())
    setup_logging()
    log.info("Запуск %s %s", APP_NAME, __version__)
    install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AkvaTehStroy")
    setup_app(app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
