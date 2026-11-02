"""Точка входа: окно с вкладками."""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from .ui.tab_image import ImageTab
from .ui.tab_pages import PagesTab
from .ui.tab_sign import SignTab
from .ui.tab_sort import SortTab

APP_NAME = "PDF-помощник АкваТехСтрой"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    f = QFont()
    f.setPointSize(11)
    app.setFont(f)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
