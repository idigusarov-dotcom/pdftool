"""Панель «Шаблон бланка»: выбор, сохранить, переименовать, удалить, экспорт/импорт."""
from __future__ import annotations

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QWidget)

from ..core import templates as tp
from .infra import last_dir, remember_dir

NONE = "— без шаблона —"


class TemplateBar(QWidget):
    """capture(name) -> Template из текущего состояния вкладки; applied(Template) — выбран шаблон."""
    applied = Signal(object)
    changed = Signal()          # список шаблонов изменился (для других вкладок)

    def __init__(self, capture, parent=None, auto_text="Определять бланк каждой страницы"):
        super().__init__(parent)
        self.capture = capture
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Шаблон бланка:"))
        self.cb = QComboBox()
        self.cb.setMinimumWidth(200)
        self.cb.activated.connect(self._chosen)
        h.addWidget(self.cb)
        for text, slot, tip in (("💾 Сохранить", self.save, "Сохранить текущие рамки, зоны и чернила как шаблон"),
                                ("✎ Переименовать", self.rename, ""), ("🗑 Удалить", self.delete, ""),
                                ("⇪ Экспорт…", self.export, "Сохранить шаблон в файл (для другого компьютера)"),
                                ("⇩ Импорт…", self.import_, "Загрузить шаблон из файла")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            h.addWidget(b)
        self.chk_auto = QCheckBox(auto_text)
        self.chk_auto.setChecked(True)
        self.chk_auto.setToolTip("Если в файле бланки разного вида, для каждой страницы выбирается похожий шаблон,\n"
                                 "и зоны номера сдвигаются вслед за бланком.")
        h.addWidget(self.chk_auto)
        h.addStretch()
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
        t = self.capture(name)
        tp.save(t)
        self.reload(name)
        self.changed.emit()
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
            return QMessageBox.warning(self, "Шаблон", str(e))
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
            return QMessageBox.information(self, "Экспорт", "Сначала выберите шаблон.")
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт шаблона", os.path.join(last_dir(), name + ".json"),
                                              "Шаблон PdfTool (*.json)")
        if path:
            remember_dir(path)
            tp.export_to(tp.get(name), path)

    def import_(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Импорт шаблона", last_dir(), "Шаблон PdfTool (*.json)")
        if not path:
            return
        remember_dir(path)
        try:
            t = tp.import_from(path)
        except Exception as e:  # noqa: BLE001
            return QMessageBox.warning(self, "Импорт", f"Не удалось прочитать шаблон: {e}")
        self.reload(t.name)
        self.changed.emit()
        self.applied.emit(t)
