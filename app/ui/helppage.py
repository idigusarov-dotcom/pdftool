"""Страница «Справка»: тот же текст, что в пошаговых подсказках, одним списком."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from . import style
from .chrome import ScreenHeader


class HelpPage(QWidget):
    def __init__(self, screens: list[tuple[str, QWidget]], start_tour):
        super().__init__()
        self.screens, self.start_tour = screens, start_tour
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        self.header = ScreenHeader("Справка", "Всё, что показывают подсказки «Как пользоваться», — здесь текстом. "
                                   "Кнопка «Как пользоваться» на любом экране покажет то же самое прямо на кнопках.",
                                   [])
        self.header.help.hide()
        lay.addWidget(self.header)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        lay.addWidget(self.text, 1)
        self.refresh()

    def html(self) -> str:
        parts = []
        for name, w in self.screens:
            parts.append(f"<h2>{name}</h2><ol>")
            for st in w.tour_steps():
                parts.append(f"<li><b>{st.title}</b><br>{st.text}</li>")
            parts.append("</ol>")
        parts.append("<h2>Общее</h2><ul>"
                     "<li><b>Ctrl + Z</b> — отменить последнее действие, <b>Ctrl + Y</b> — повторить.</li>"
                     "<li><b>Ctrl + S</b> — сохранить результат текущего инструмента в новый PDF.</li>"
                     "<li>Файлы можно перетаскивать мышью прямо в окно программы.</li>"
                     "<li>Программа никогда не меняет исходные файлы — результат всегда сохраняется в новый PDF.</li>"
                     "<li>Если что-то пошло не так: меню «⋯» → «Открыть папку с логами» и отправьте файл log.txt.</li>"
                     "<li>Тёмная тема — кнопка с луной вверху справа.</li></ul>")
        return "".join(parts)

    def refresh(self):
        self.text.setHtml(style.key(self.html()))

    def tour_steps(self):
        from .tour import Step
        return [Step(self.text, "Справка", "Здесь описано, как работает каждый экран. Прокрутите вниз.")]

    def step_index(self) -> int:
        return 0
