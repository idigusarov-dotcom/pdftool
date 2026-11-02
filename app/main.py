"""Точка входа: окно с вкладками, меню, журнал ошибок."""
from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from . import APP_NAME, __version__
from .core.logsetup import log, log_dir, setup_logging
from .ui.tab_image import ImageTab
from .ui.tab_pages import PagesTab
from .ui.tab_sign import SignTab
from .ui.tab_sort import SortTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — версия {__version__}")
        self.resize(1400, 900)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab { font-size: 16px; padding: 10px 22px; }")
        self.sign = SignTab()
        self.sort = SortTab()
        self.image = ImageTab()
        self.pages = PagesTab()
        self.tabs.addTab(self.sign, "✍ Перенос подписей")
        self.tabs.addTab(self.sort, "🔢 Сортировка по квартирам")
        self.tabs.addTab(self.image, "🖼 Вставка картинки")
        self.tabs.addTab(self.pages, "📑 Страницы")
        self.setCentralWidget(self.tabs)
        self._menus()

    def all_tabs(self):
        return [self.sign, self.sort, self.image, self.pages]

    def _menus(self):
        m = self.menuBar().addMenu("Правка")
        a = QAction("Отменить", self)
        a.setShortcut(QKeySequence.Undo)
        a.triggered.connect(lambda: self.tabs.currentWidget().undo_stack.undo())
        m.addAction(a)
        r = QAction("Повторить", self)
        r.setShortcut(QKeySequence.Redo)
        r.triggered.connect(lambda: self.tabs.currentWidget().undo_stack.redo())
        m.addAction(r)
        h = self.menuBar().addMenu("Справка")
        lg = QAction("Открыть папку с логами", self)
        lg.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        h.addAction(lg)
        ab = QAction("О программе", self)
        ab.triggered.connect(lambda: QMessageBox.about(
            self, "О программе", f"{APP_NAME}\nВерсия {__version__}\n\nЖурнал ошибок: {log_dir()}"))
        h.addAction(ab)

    def closeEvent(self, e):
        dirty = [self.tabs.tabText(self.tabs.indexOf(t)) for t in self.all_tabs() if t.is_dirty()]
        if dirty:
            ans = QMessageBox.question(
                self, "Есть несохранённые изменения",
                "Изменения не сохранены во вкладках:\n• " + "\n• ".join(dirty) + "\n\nВсё равно закрыть программу?",
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


def main() -> int:
    setup_logging()
    log.info("Запуск %s %s", APP_NAME, __version__)
    install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AkvaTehStroy")
    f = QFont()
    f.setPointSize(11)
    app.setFont(f)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
