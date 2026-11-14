"""Точка входа: окно с вкладками, меню, журнал ошибок."""
from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

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


IS_MAC = sys.platform == "darwin"
COMPACT_BELOW = 1180          # уже этого окна боковая панель показывает только значки
THEMES = [("system", "Как в системе"), ("light", "Светлая"), ("dark", "Тёмная")]

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

        # Каркас как в приложениях macOS: слева боковая панель на всю высоту (название, разделы),
        # справа — строка инструментов и сам экран.
        root = QWidget()
        root.setObjectName("Page")
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_header())
        self.tabs = ScreenStack()   # имя «tabs» оставлено для совместимости со скриптами
        for key, *_ in SCREENS:
            w = self.helppage if key == "help" else getattr(self, key)
            w.setObjectName("Page")
            self.tabs.add_screen(w)
            w.header.help_clicked.connect(self.start_tour)
        self.tabs.currentChanged.connect(self._screen_changed)
        right.addWidget(self.tabs, 1)
        body.addLayout(right, 1)
        self.setCentralWidget(root)
        self._actions()
        if IS_MAC:
            self._build_menubar()
            self.macify()
        self._step_timer = QTimer(self, interval=400, timeout=self.refresh_steps)
        self._step_timer.start()
        self.resize(1366, 768)
        self.show_screen(self.sign)
        self._screen_changed(0)

    # ---------- каркас ----------
    def _build_header(self):
        h = QFrame()
        h.setObjectName("Toolbar")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(16, 6, 14, 6)
        lay.setSpacing(2)
        lay.addStretch()
        self.btn_undo = self._tool("undo-2", "Отменить последнее действие (Ctrl + Z)", self.undo)
        self.btn_redo = self._tool("redo-2", "Повторить (Ctrl + Y)", self.redo)
        self.btn_theme = self._tool("moon", "Тёмная / светлая тема", self.toggle_theme)
        self.btn_more = self._tool("info", "Ещё: журнал ошибок, о программе", None)
        m = QMenu(self)
        m.addAction("Открыть папку с логами", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        m.addAction("Справка", lambda: self.show_screen(self.helppage))
        m.addAction("Оформление…", self.preferences)
        m.addAction("О программе", self.about)
        self.btn_more.setMenu(m)
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        self.btn_help = self._tool("circle-help", "", None)
        self.btn_help.setToolTip("Показать, куда и зачем нажимать на этом экране")
        self.btn_help.clicked.connect(self.start_tour)
        for b in (self.btn_undo, self.btn_redo, self.btn_theme, self.btn_more, self.btn_help):
            lay.addWidget(b)
        return h

    def _tool(self, name, tip, slot):
        b = QToolButton()
        b.setIcon(style.icon(name))
        b.setProperty("icon_name", name)
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        if slot:
            b.clicked.connect(slot)
        return b

    def _build_sidebar(self):
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(208)
        self.sidebar = sb
        v = QVBoxLayout(sb)
        v.setContentsMargins(10, 14, 10, 12)
        v.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 10)
        brand.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(self._logo_pixmap())
        self._logo = logo
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        t = QLabel("PDF-помощник")
        t.setObjectName("AppTitle")
        ver = QLabel(f"АкваТехСтрой · версия {__version__}")
        ver.setObjectName("AppVersion")
        ver.setWordWrap(True)
        names.addWidget(t)
        names.addWidget(ver)
        self._brand_text = QWidget()
        self._brand_text.setLayout(names)
        brand.addWidget(self._brand_text, 1)
        v.addLayout(brand)
        self._section = QLabel("Инструменты")
        self._section.setObjectName("SideSection")
        v.addWidget(self._section)
        self.nav = {}
        self._nav_labels = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for i, (key, label, ic, tip) in enumerate(SCREENS):
            if key == "help":
                v.addStretch()
            b = QPushButton(label)
            b.setCheckable(True)
            b.setIcon(style.icon(ic, on_color="#ffffff"))
            b.setProperty("icon_name", ic)
            b.setProperty("icon_on", "#ffffff")
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            self._nav_labels[key] = label
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self.tabs.setCurrentIndex(k))
            grp.addButton(b)
            v.addWidget(b)
            self.nav[key] = b
        self._compact = None
        return sb

    def _logo_pixmap(self):
        from PySide6.QtGui import QPixmap
        pm = QPixmap(style.asset("icons", "app-logo.png"))
        if pm.isNull():
            return style.icon("file-text", style.colors()["accent"]).pixmap(QSize(30, 30))
        pm.setDevicePixelRatio(pm.width() / 30)
        return pm

    def set_compact(self, on: bool):
        """Узкое окно: боковая панель — только значки, чтобы экран помещался без прокрутки вбок."""
        if on == self._compact:
            return
        self._compact = on
        self.sidebar.setFixedWidth(60 if on else 208)
        self.sidebar.setProperty("compact", "true" if on else "false")
        self._brand_text.setVisible(not on)
        self._section.setVisible(not on)
        for key, b in self.nav.items():
            b.setText("" if on else self._nav_labels[key])
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        for b in self.nav.values():
            b.style().unpolish(b)
            b.style().polish(b)

    def _actions(self):
        if IS_MAC:          # на Mac сочетания клавиш живут в строке меню (⌘Z, ⇧⌘Z, ⌘?)
            return
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.redo), (QKeySequence.HelpContents, self.start_tour)):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(fn)
            self.addAction(a)

    def _build_menubar(self):
        """Системная строка меню macOS. «О программе», «Настройки…» (⌘,) и «Завершить» (⌘Q) Qt сам
        переносит в меню приложения."""
        mb = self.menuBar()
        app_m = mb.addMenu("Файл")
        a = app_m.addAction("О программе «PDF-помощник»", self.about)
        a.setMenuRole(QAction.AboutRole)
        a = app_m.addAction("Настройки…", self.preferences)
        a.setMenuRole(QAction.PreferencesRole)
        a.setShortcut(QKeySequence.Preferences)
        a = app_m.addAction("Завершить PDF-помощник", self.close)
        a.setMenuRole(QAction.QuitRole)
        a.setShortcut(QKeySequence.Quit)
        app_m.addAction("Закрыть окно", self.close).setShortcut(QKeySequence.Close)
        ed = mb.addMenu("Правка")
        ed.addAction("Отменить", self.undo).setShortcut(QKeySequence.Undo)
        r = ed.addAction("Повторить", self.redo)
        r.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        view = mb.addMenu("Вид")
        self._theme_group = QActionGroup(self)
        self._theme_actions = {}
        for code, title in THEMES:
            a = view.addAction(f"Тема: {title.lower()}")
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, c=code: self.set_theme_mode(c))
            self._theme_group.addAction(a)
            self._theme_actions[code] = a
        view.addSeparator()
        for i, (key, label, *_rest) in enumerate(SCREENS):
            a = view.addAction(label, lambda k=i: self.tabs.setCurrentIndex(k))
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
        win = mb.addMenu("Окно")
        win.addAction("Свернуть", self.showMinimized).setShortcut(QKeySequence("Ctrl+M"))
        win.addAction("Увеличить", lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        hm = mb.addMenu("Справка")
        hm.addAction("Как пользоваться этим экраном", self.start_tour).setShortcut(QKeySequence.HelpContents)
        hm.addAction("Справка PDF-помощника", lambda: self.show_screen(self.helppage))
        hm.addAction("Открыть папку с журналом", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        self._sync_theme_actions()

    def macify(self):
        """Подписи «Ctrl» → «⌘» во всех надписях и подсказках (на Mac «Ctrl» в программе — это ⌘)."""
        for w in self.findChildren(QWidget):
            tip = w.toolTip()
            if "Ctrl" in tip:
                w.setToolTip(style.key(tip))
            if isinstance(w, (QLabel, QAbstractButton)) and "Ctrl" in w.text():
                w.setText(style.key(w.text()))
            if isinstance(w, QGroupBox) and "Ctrl" in w.title():
                w.setTitle(style.key(w.title()))

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
        self.set_compact(self.width() < COMPACT_BELOW)
        self.place_toasts()

    # ---------- тема ----------
    def toggle_theme(self):
        """Кнопка «луна/солнце»: переключить на противоположную и запомнить выбор."""
        self.set_theme_mode("light" if style.is_dark() else "dark")

    def set_theme_mode(self, mode: str):
        """mode: system — как в системе (меняется вместе с ней), light, dark."""
        s = settings()
        s.setValue("ui/theme", mode)
        if mode != "system":
            s.setValue("ui/dark", mode == "dark")
        self.apply_theme_mode()

    def apply_theme_mode(self):
        dark = theme_is_dark()
        if dark != style.is_dark() or not QApplication.instance().styleSheet():
            style.apply_theme(QApplication.instance(), dark)
            self.retheme()
        self._sync_theme_actions()

    def _sync_theme_actions(self):
        acts = getattr(self, "_theme_actions", None)
        if acts:
            acts[theme_mode()].setChecked(True)

    def preferences(self):
        """Окно «Настройки»: оформление (как в системе / светлое / тёмное)."""
        d = QDialog(self)
        d.setWindowTitle("Настройки")
        v = QVBoxLayout(d)
        v.setContentsMargins(20, 18, 20, 14)
        v.setSpacing(10)
        t = QLabel("Оформление")
        t.setObjectName("AppTitle")
        v.addWidget(t)
        cur = theme_mode()
        radios = {}
        for code, title in THEMES:
            r = QRadioButton(title)
            r.setChecked(code == cur)
            r.toggled.connect(lambda on, c=code: on and self.set_theme_mode(c))
            radios[code] = r
            v.addWidget(r)
        hint = QLabel("«Как в системе» — тема меняется вместе с настройкой\n"
                      + ("«Внешний вид» в Системных настройках macOS." if IS_MAC else "Windows (светлый/тёмный режим)."))
        hint.setObjectName("Muted")
        v.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        bb.button(QDialogButtonBox.Close).setText("Закрыть")
        v.addWidget(bb)
        d.radios = radios
        if os.environ.get("PDFTOOL_NO_MODAL"):
            d.show()
            return d
        d.exec()
        return d

    def retheme(self):
        self.btn_theme.setProperty("icon_name", "sun" if style.is_dark() else "moon")
        for b in self.findChildren(QToolButton) + self.findChildren(QPushButton):
            n = b.property("icon_name")
            if n:
                b.setIcon(style.icon(n, "#ffffff" if b.property("kind") == "primary" else None,
                                     on_color=b.property("icon_on")))
        self._logo.setPixmap(self._logo_pixmap())
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
            v.setBackgroundBrush(QColor(style.colors()["page"]))

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

    def open_files(self, paths: list[str]):
        """Файлы, открытые через систему (перетащили на значок в Dock, «Открыть с помощью»)."""
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self.raise_()
        self.activateWindow()
        w = self.current()
        if all(not p.lower().endswith(".pdf") for p in paths):
            w = self.image            # одни картинки — на экран «Картинка»
        elif not hasattr(w, "accept_files"):
            w = self.pages            # со «Справки» — просто открыть PDF на «Страницах»
        self.show_screen(w)
        w.accept_files(paths)

    def templates_changed(self):
        for bar in (self.sign.tbar, self.sort.tbar):
            bar.reload()
        self.batch.refresh_templates()

    def about(self):
        QMessageBox.about(self, "О программе", f"{APP_NAME}\nВерсия {__version__}\n\n"
                          f"Журнал ошибок и шаблоны: {log_dir()}\n\n"
                          "Шрифт Inter (SIL OFL 1.1), иконки Lucide (ISC).")

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


def theme_mode() -> str:
    """Выбор темы. Если пользователь раньше переключал тему кнопкой (старый ключ ui/dark) — уважаем это."""
    s = settings()
    m = s.value("ui/theme", "", type=str)
    if m in ("system", "light", "dark"):
        return m
    if s.contains("ui/dark"):
        return "dark" if s.value("ui/dark", False, type=bool) else "light"
    return "system"


def theme_is_dark() -> bool:
    m = theme_mode()
    return style.system_dark() if m == "system" else m == "dark"


class PdfApp(QApplication):
    """Приложение, которое принимает файлы от системы (macOS: перетаскивание на значок в Dock,
    «Открыть с помощью»). Файлы, пришедшие до создания окна, ждут в очереди."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending: list[str] = []

    def event(self, e):
        if e.type() == QEvent.FileOpen:
            p = e.file() or e.url().toLocalFile()
            if p:
                p = os.path.normpath(p)
                if self.window is not None:
                    self.window.open_files([p])
                else:
                    self.pending.append(p)
            return True
        return super().event(e)

    def set_window(self, w):
        self.window = w
        if self.pending:
            ps, self.pending = self.pending, []
            QTimer.singleShot(0, lambda: w.open_files(ps))


def setup_app(app):
    """Шрифт и тема — до создания окна."""
    app.setFont(style.ui_font(style.load_fonts()))
    style.apply_theme(app, theme_is_dark())


def _follow_system_theme(app, w):
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: theme_mode() == "system" and w.apply_theme_mode())
    except AttributeError:   # Qt < 6.5
        pass


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":   # проверка сборки (CI, поддержка)
        from .selftest import run
        return run(sys.argv[2] if len(sys.argv) > 2 else os.getcwd())
    setup_logging()
    log.info("Запуск %s %s", APP_NAME, __version__)
    install_excepthook()
    app = PdfApp(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("PDF-помощник")
    app.setOrganizationName("AkvaTehStroy")
    setup_app(app)
    if not IS_MAC:     # на Mac значок в Dock берётся из PdfTool.app
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(style.asset("icons", "app-logo.png")))
    w = MainWindow()
    _follow_system_theme(app, w)
    w.show()
    app.set_window(w)
    args = [a for a in sys.argv[1:] if not a.startswith("-") and os.path.isfile(a)]
    if args:       # «Открыть с помощью» на Windows передаёт файлы в командной строке
        QTimer.singleShot(0, lambda: w.open_files(args))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
