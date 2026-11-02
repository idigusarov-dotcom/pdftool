"""Вкладка «Сортировка по квартирам»."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QProgressDialog, QRadioButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import numbers, sorting
from ..core.logsetup import log
from ..core.pdfutil import NRect, safe_output_path
from .infra import UndoMixin, load_rect, run_task, save_rect
from .thumbs import ThumbLoader, page_pixmap, placeholder_icon, visible_rows
from .widgets import PageView, ask_open_pdf, ask_save, big_button, error, info, zoom_bar

HELP = ("Откройте PDF. Номер квартиры впишите в таблицу (двойной щелчок) или нажмите «Распознать номера» — "
        "перед этим нарисуйте на странице справа зелёную зону, где написан номер (для второго вида бланка — зону 2). "
        "Повторы — красные, неуверенно распознанные — оранжевые (проверьте!). Ctrl+Z — отменить.")

DEFAULT_ZONE = NRect(0.345, 0.218, 0.455, 0.256)


class SortTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.doc = None
        self.path = ""
        self.loader: ThumbLoader | None = None
        self.unsure: set[int] = set()   # страницы с неуверенным распознаванием
        self._icons: dict[int, QIcon] = {}
        self.zones = {"zone": load_rect("sort/zone1", DEFAULT_ZONE), "zone2": load_rect("sort/zone2", None)}
        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть PDF…", self.open))
        self.lbl = QLabel("файл не выбран")
        top.addWidget(self.lbl)
        top.addStretch()
        top.addWidget(big_button("🔢 Распознать номера", self.recognize))
        top.addWidget(big_button("🔎 Из текста PDF", self.guess))
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
        self.table.itemChanged.connect(self.item_edited)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_page(r))
        self.table.verticalScrollBar().valueChanged.connect(self.request_thumbs)
        split.addWidget(self.table)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.view = PageView()
        self.view.active = "zone"
        self.view.rectChanged.connect(self.zone_changed)
        rv.addWidget(self.view, 1)
        zl = zoom_bar(self.view)
        zl.addWidget(QLabel("Рисовать:"))
        self.r_z1 = QRadioButton("зона номера")
        self.r_z2 = QRadioButton("зона 2 (другой бланк)")
        self.r_z1.setChecked(True)
        g = QButtonGroup(self)
        for b in (self.r_z1, self.r_z2):
            g.addButton(b)
            zl.addWidget(b)
        self.r_z1.toggled.connect(lambda on: setattr(self.view, "active", "zone" if on else "zone2"))
        zl.addWidget(big_button("✖ убрать зону 2", self.clear_zone2))
        rv.addLayout(zl)
        split.addWidget(right)
        split.setSizes([450, 550])
        lay.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.stat = QLabel("")
        bottom.addWidget(self.stat)
        bottom.addStretch()
        bottom.addWidget(big_button("📋 Сохранить список (CSV)…", self.save_csv))
        bottom.addWidget(big_button("💾 Сохранить отсортированный PDF…", self.save, primary=True))
        lay.addLayout(bottom)

    # ---------- Ctrl+Z ----------
    def get_state(self):
        if not self.doc:
            return None
        order = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]
        return dict(order=order, values=self.values_by_page(), unsure=set(self.unsure))

    def set_state(self, st):
        if not st:
            return
        self.unsure = set(st["unsure"])
        self._fill(st["order"], st["values"])

    # ---------- загрузка ----------
    def open(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if not path:
            return
        self.path, self.doc = path, pymupdf.open(path)
        self.lbl.setText(f"{os.path.basename(path)} ({len(self.doc)} стр.)")
        self.unsure = set()
        self._icons = {}
        if self.loader:
            self.loader.stop()
        self.loader = ThumbLoader(path, 30)
        self.loader.ready.connect(self.thumb_ready)
        self._fill(list(range(len(self.doc))), [None] * len(self.doc))
        found = sorting.guess_all(self.doc)
        if any(found):
            self._fill(list(range(len(self.doc))), found)
        self.table.setCurrentCell(0, 1)
        QTimer.singleShot(0, self.request_thumbs)
        self.reset_undo()

    def _fill(self, order, values):
        self.table.blockSignals(True)
        self.table.setRowCount(len(order))
        ph = placeholder_icon()
        for r, p in enumerate(order):
            it = QTableWidgetItem(self._icons.get(p, ph), f"стр. {p + 1}")
            it.setData(Qt.UserRole, p)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(values[p] or ""))
            n = QTableWidgetItem("")
            n.setFlags(n.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, n)
        self.table.blockSignals(False)
        self.refresh_marks()
        QTimer.singleShot(0, self.request_thumbs)

    def request_thumbs(self, *_):
        if self.loader:
            rows = visible_rows(self.table)
            self.loader.request([self.table.item(r, 0).data(Qt.UserRole) for r in rows if self.table.item(r, 0)])

    def thumb_ready(self, path, p, img):
        if path != self.path:
            return
        self._icons[p] = QIcon(QPixmap.fromImage(img))
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.UserRole) == p:
                it.setIcon(self._icons[p])
                break

    def values_by_page(self) -> list:
        vals = [None] * (len(self.doc) if self.doc else 0)
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).data(Qt.UserRole)
            vals[p] = (self.table.item(r, 1).text().strip() or None)
        return vals

    def set_values(self, vals):
        order = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]
        self._fill(order, vals)
        self.commit("номера")

    def item_edited(self, item):
        if item.column() == 1:
            self.unsure.discard(self.table.item(item.row(), 0).data(Qt.UserRole))
        self.refresh_marks()
        self.commit("номер квартиры")

    def guess(self, silent=False):
        if not self.doc:
            return
        found = sorting.guess_all(self.doc)
        cur = self.values_by_page()
        self.set_values([c or f for c, f in zip(cur, found)])
        n = sum(1 for f in found if f)
        if not silent:
            info(self, f"Номер найден в тексте на {n} из {len(found)} страниц."
                       + ("\nЭто сканы без текста — используйте «Распознать номера» или впишите вручную." if n == 0 else ""))

    # ---------- распознавание ----------
    def zone_changed(self, key):
        nr = self.view.get_nrect(key)
        if nr is not None and key in self.zones:
            self.zones[key] = nr
            save_rect("sort/zone1" if key == "zone" else "sort/zone2", nr)

    def clear_zone2(self):
        self.zones["zone2"] = None
        self.view.remove_box("zone2")
        from .infra import settings
        settings().remove("sort/zone2")

    def recognize(self):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        zones = [z for z in (self.zones["zone"], self.zones["zone2"]) if z is not None]
        cur = self.values_by_page()
        todo = [p for p, v in enumerate(cur) if not v]
        if not todo:
            return info(self, "Все номера уже заполнены. Очистите ячейки, которые нужно распознать заново.")
        dlg = QProgressDialog("Распознаю номера квартир…", "Отмена", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        path = self.path

        def done(res):
            dlg.close()
            vals = self.values_by_page()
            got = 0
            for p, r in zip(todo, res):
                if r.text:
                    vals[p] = r.text
                    got += 1
                    if not r.ok:
                        self.unsure.add(p)
            self.set_values(vals)
            info(self, f"Распознано: {got} из {len(todo)} пустых.\nНеуверенных (оранжевые): "
                       f"{len([p for p in todo if p in self.unsure])}.\nРукописные цифры распознаются неточно — "
                       f"проверьте все номера по картинке справа.")

        def fail(msg):
            dlg.close()
            error(self, "Не удалось распознать: " + msg)

        t = run_task(lambda prog: numbers.read_document(path, zones, "blue", progress=prog, pages=todo), done, fail,
                     lambda i, n: dlg.setValue(int(100 * i / max(n, 1))))
        dlg.canceled.connect(lambda: setattr(t, "cancelled", True))

    def refresh_marks(self, *_):
        if not self.doc:
            return
        vals = self.values_by_page()
        dup = sorting.duplicates(vals)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).data(Qt.UserRole)
            v = self.table.item(r, 1).text().strip()
            note, color = "", QColor("white")
            if not v:
                note, color = "нет номера", QColor("#fff8e1")
            elif v in dup:
                note, color = "ПОВТОР", QColor("#ffcdd2")
            elif p in self.unsure:
                note, color = "проверьте (распознано неуверенно)", QColor("#ffcc80")
            for c in (1, 2):
                self.table.item(r, c).setBackground(color)
            self.table.item(r, 2).setText(note)
        self.table.blockSignals(False)
        empty = sum(1 for v in vals if not v)
        self.stat.setText(f"Страниц: {len(vals)}   без номера: {empty}   повторы: "
                          f"{', '.join(sorted(dup, key=sorting.apt_key)) or 'нет'}")

    def sort_table(self):
        if not self.doc:
            return
        vals = self.values_by_page()
        self._fill(sorting.sorted_order(vals), vals)
        self.commit("сортировка")

    def show_page(self, r):
        if self.doc and r is not None and r >= 0 and self.table.item(r, 0):
            self.view.set_page(page_pixmap(self.doc, self.table.item(r, 0).data(Qt.UserRole), 80))
            for k, z in self.zones.items():
                if z is not None and k not in self.view.boxes:
                    self.view.set_nrect(z, key=k, emit=False)

    def save(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out = out or ask_save(self, safe_output_path(self.path, "по_квартирам"))
        if out:
            try:
                sorting.export_sorted(self.path, self.values_by_page(), out)
                self.mark_saved()
                info(self, f"Сохранено: {out}")
            except Exception as e:  # noqa: BLE001
                log.exception("Сортировка")
                error(self, str(e))

    def save_csv(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out = out or ask_save(self, safe_output_path(self.path, "список_квартир", ".csv"), "CSV (*.csv)")
        if out:
            sorting.export_csv(self.values_by_page(), out)
            info(self, f"Список сохранён: {out}\n(открывается в Excel)")
