"""Панель «Шаблон бланка»: выбор, сохранить, переименовать, удалить, экспорт/импорт."""
from __future__ import annotations

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from ..core import templates as tp
from .infra import last_dir, remember_dir
from .widgets import big_button, error, info

NONE = "— без шаблона —"


class TemplateBar(QWidget):
    """capture(name) -> Template из текущего состояния вкладки; applied(Template) — выбран шаблон."""
    applied = Signal(object)
    changed = Signal()          # список шаблонов изменился (для других вкладок)

    def __init__(self, capture, parent=None, auto_text="Определять бланк каждой страницы"):
        super().__init__(parent)
        self.capture = capture
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        h = QHBoxLayout()
        outer.addLayout(h)
        h.addWidget(QLabel("Шаблон бланка:"))
        self.cb = QComboBox()
        self.cb.setMinimumWidth(200)
        self.cb.setToolTip("Выберите шаблон — рамки, зоны и чернила подставятся сами")
        self.cb.activated.connect(self._chosen)
        h.addWidget(self.cb)
        self.buttons = {}
        for key, text, ic, slot, tip in (
                ("save", "Сохранить", "save", self.save, "Запомнить текущие рамки, зоны номера и чернила как шаблон"),
                ("rename", "", "pencil", self.rename, "Переименовать выбранный шаблон"),
                ("delete", "", "trash-2", self.delete, "Удалить выбранный шаблон"),
                ("export", "Экспорт", "upload", self.export, "Сохранить шаблон в файл, чтобы перенести на другой компьютер"),
                ("import", "Импорт", "download", self.import_, "Загрузить шаблон из файла")):
            b = big_button(text, slot, icon_name=ic, tip=tip, ghost=not text)
            self.buttons[key] = b
            h.addWidget(b)
        self.chk_auto = QCheckBox(auto_text)
        self.chk_auto.setChecked(True)
        self.chk_auto.setToolTip("Если в файле бланки разного вида, для каждой страницы выбирается похожий шаблон,\n"
                                 "и зоны номера сдвигаются вслед за бланком.")
        h.addStretch()
        outer.addWidget(self.chk_auto)
        self.reload()

    # ---------- список ----------
    def reload(self, select: str | None = None):
        cur = select if select is not None else self.cb.currentText()
        self.cb.blockSignals(True)
        self.cb.clear()
        self.cb.addItem(NONE)
        for t in tp.load_all():
            self.cb.addItem(t.name)
        i = self.cb.findText(cur)
        self.cb.setCurrentIndex(max(0, i))
        self.cb.blockSignals(False)

    def current_name(self) -> str | None:
        t = self.cb.currentText()
        return None if t == NONE else t

    def templates(self) -> list:
        return tp.load_all()

    def auto_templates(self) -> list:
        """Шаблоны для автоопределения (если включено), иначе только выбранный."""
        if self.chk_auto.isChecked():
            return tp.load_all()
        t = tp.get(self.current_name()) if self.current_name() else None
        return [t] if t else []

    def _chosen(self, *_):
        name = self.current_name()
        t = tp.get(name) if name else None
        if t is not None:
            self.applied.emit(t)

    # ---------- действия ----------
    def save(self, name: str = ""):
        if not name:
            name, ok = QInputDialog.getText(self, "Сохранить шаблон", "Название шаблона (например «Акт ЦО»):",
                                            text=self.current_name() or "")
            if not ok or not name.strip():
                return
        name = name.strip()
        if tp.get(name) is not None and name != self.current_name():
            if QMessageBox.question(self, "Шаблон уже есть", f"Заменить шаблон «{name}»?") != QMessageBox.Yes:
                return
        try:
            t = self.capture(name)
            tp.save(t)
        except Exception as e:  # noqa: BLE001
            from ..core.logsetup import log
            log.exception("Сохранение шаблона")
            return error(self, f"Не удалось сохранить шаблон: {e}")
        self.reload(name)
        self.changed.emit()
        info(self, f"Шаблон «{name}» сохранён.")
        return t

    def rename(self):
        old = self.current_name()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Переименовать шаблон", "Новое название:", text=old)
        if not ok or not new.strip() or new.strip() == old:
            return
        try:
            tp.rename(old, new.strip())
        except ValueError as e:
            return error(self, str(e))
        self.reload(new.strip())
        self.changed.emit()

    def delete(self):
        name = self.current_name()
        if not name:
            return
        if QMessageBox.question(self, "Удалить шаблон", f"Удалить шаблон «{name}»?") != QMessageBox.Yes:
            return
        tp.delete(name)
        self.reload(NONE)
        self.changed.emit()

    def export(self):
        name = self.current_name()
        if not name:
            return info(self, "Сначала выберите шаблон в списке.")
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт шаблона", os.path.join(last_dir(), name + ".json"),
                                              "Шаблон PdfTool (*.json)")
        if path:
            remember_dir(path)
            try:
                tp.export_to(tp.get(name), path)
            except OSError as e:
                return error(self, f"Не удалось сохранить файл шаблона: {e.strerror or e}")
            info(self, f"Шаблон сохранён в файл: {path}")

    def import_(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Импорт шаблона", last_dir(), "Шаблон PdfTool (*.json)")
        if not path:
            return
        remember_dir(path)
        try:
            t = tp.import_from(path)
        except Exception as e:  # noqa: BLE001
            return error(self, f"Не удалось прочитать шаблон: {e}")
        self.reload(t.name)
        self.changed.emit()
        self.applied.emit(t)
