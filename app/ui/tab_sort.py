"""Вкладка «Сортировка по квартирам»."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import sorting
from ..core.pdfutil import safe_output_path
from .thumbs import page_pixmap, thumb_icon
from .widgets import PageView, ask_open_pdf, ask_save, big_button, error, info

HELP = ("Откройте PDF. Для каждой страницы впишите номер квартиры (двойной щелчок по ячейке). "
        "Номера, которые встречаются дважды, подсвечиваются красным. Затем «Сохранить отсортированный PDF».")


class SortTab(QWidget):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.path = ""
        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть PDF…", self.open))
        self.lbl = QLabel("файл не выбран")
        top.addWidget(self.lbl)
        top.addStretch()
        top.addWidget(big_button("🔎 Найти номера в тексте", self.guess))
        top.addWidget(big_button("↕ Упорядочить таблицу", self.sort_table))
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Страница", "Квартира", "Примечание"])
        self.table.setIconSize(QSize(60, 84))
        self.table.verticalHeader().setDefaultSectionSize(90)
        self.table.setColumnWidth(0, 140)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet("QTableWidget { font-size: 16px; }")
        self.table.itemChanged.connect(self.refresh_marks)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_page(r))
        split.addWidget(self.table)
        self.view = PageView()
        self.view.draw_enabled = False
        split.addWidget(self.view)
        split.setSizes([450, 550])
        lay.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.stat = QLabel("")
        bottom.addWidget(self.stat)
        bottom.addStretch()
        bottom.addWidget(big_button("📋 Сохранить список (CSV)…", self.save_csv))
        bottom.addWidget(big_button("💾 Сохранить отсортированный PDF…", self.save, primary=True))
        lay.addLayout(bottom)

    def open(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if not path:
            return
        self.path, self.doc = path, pymupdf.open(path)
        self.lbl.setText(f"{os.path.basename(path)} ({len(self.doc)} стр.)")
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.doc))
        for i in range(len(self.doc)):
            it = QTableWidgetItem(thumb_icon(self.doc, i), f"стр. {i + 1}")
            it.setData(Qt.UserRole, i)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(""))
            n = QTableWidgetItem("")
            n.setFlags(n.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 2, n)
        self.table.blockSignals(False)
        self.guess(silent=True)

    def values_by_page(self) -> list:
        vals = [None] * self.table.rowCount()
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).data(Qt.UserRole)
            vals[p] = (self.table.item(r, 1).text().strip() or None)
        return vals

    def set_values(self, vals):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).data(Qt.UserRole)
            self.table.item(r, 1).setText(vals[p] or "")
        self.table.blockSignals(False)
        self.refresh_marks()

    def guess(self, silent=False):
        if not self.doc:
            return
        found = sorting.guess_all(self.doc)
        cur = self.values_by_page()
        merged = [c or f for c, f in zip(cur, found)]
        self.set_values(merged)
        n = sum(1 for f in found if f)
        if not silent:
            info(self, f"Номер найден в тексте на {n} из {len(found)} страниц."
                       + ("\nЭто сканы без текста — впишите номера вручную." if n == 0 else ""))

    def refresh_marks(self, *_):
        vals = self.values_by_page()
        dup = sorting.duplicates(vals)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            v = self.table.item(r, 1).text().strip()
            note, color = "", QColor("white")
            if not v:
                note, color = "нет номера", QColor("#fff8e1")
            elif v in dup:
                note, color = "ПОВТОР", QColor("#ffcdd2")
            for c in (1, 2):
                self.table.item(r, c).setBackground(color)
            self.table.item(r, 2).setText(note)
        self.table.blockSignals(False)
        empty = sum(1 for v in vals if not v)
        self.stat.setText(f"Страниц: {len(vals)}   без номера: {empty}   повторы: {', '.join(sorted(dup, key=sorting.apt_key)) or 'нет'}")

    def sort_table(self):
        if not self.doc:
            return
        vals = self.values_by_page()
        order = sorting.sorted_order(vals)
        self.table.blockSignals(True)
        for r, p in enumerate(order):
            it = QTableWidgetItem(thumb_icon(self.doc, p), f"стр. {p + 1}")
            it.setData(Qt.UserRole, p)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(vals[p] or ""))
            n = QTableWidgetItem("")
            n.setFlags(n.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, n)
        self.table.blockSignals(False)
        self.refresh_marks()

    def show_page(self, r):
        if self.doc and r is not None and r >= 0 and self.table.item(r, 0):
            self.view.set_page(page_pixmap(self.doc, self.table.item(r, 0).data(Qt.UserRole), 80))

    def save(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out = out or ask_save(self, safe_output_path(self.path, "по_квартирам"))
        if out:
            try:
                sorting.export_sorted(self.path, self.values_by_page(), out)
                info(self, f"Сохранено: {out}")
            except Exception as e:  # noqa: BLE001
                error(self, str(e))

    def save_csv(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out = out or ask_save(self, safe_output_path(self.path, "список_квартир", ".csv"), "CSV (*.csv)")
        if out:
            sorting.export_csv(self.values_by_page(), out)
            info(self, f"Список сохранён: {out}\n(открывается в Excel)")
