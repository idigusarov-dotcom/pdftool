"""Вкладка «Перенос подписей»."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QScrollArea)

from ..core import signatures as sig
from ..core.pdfutil import safe_output_path
from .thumbs import page_pixmap, thumb_icon
from .widgets import PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap

HELP = ("1) Откройте файл с АКТАМИ и файл с РАСПИСКАМИ.  2) В таблице для каждой расписки выберите страницу акта "
        "(или «— не ставить —»).  3) Красная рамка — откуда берём подпись в акте, синяя — куда ставим в расписке. "
        "Двигайте рамку мышью, размер — синим квадратиком в углу, новую рамку можно нарисовать мышью.  "
        "4) «Предпросмотр», затем «Сохранить».")


class SignTab(QWidget):
    def __init__(self):
        super().__init__()
        self.acts: pymupdf.Document | None = None
        self.rec: pymupdf.Document | None = None
        self.acts_path = self.rec_path = ""
        self.job: sig.TransferJob | None = None
        self._loading = False

        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        h.setStyleSheet("font-size: 13px; color: #333;")
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть акты…", self.open_acts))
        self.lbl_acts = QLabel("акты не выбраны")
        top.addWidget(self.lbl_acts)
        top.addSpacing(20)
        top.addWidget(big_button("📄 Открыть расписки…", self.open_receipts))
        self.lbl_rec = QLabel("расписки не выбраны")
        top.addWidget(self.lbl_rec)
        top.addStretch()
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Расписка", "Подпись из акта"])
        self.table.setIconSize(QSize(48, 64))
        self.table.verticalHeader().setDefaultSectionSize(70)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_row(r))
        split.addWidget(self.table)

        g1 = QGroupBox("Акт — откуда взять подпись (красная рамка)")
        v1 = QVBoxLayout(g1)
        self.view_act = PageView(color="#e53935")
        self.view_act.rectChanged.connect(self.src_changed)
        v1.addWidget(self.view_act)
        self.chk_src_own = QCheckBox("Рамка только для этой страницы акта")
        v1.addWidget(self.chk_src_own)
        split.addWidget(g1)

        g2 = QGroupBox("Расписка — куда поставить (синяя рамка)")
        v2 = QVBoxLayout(g2)
        self.view_rec = PageView(color="#1565c0")
        self.view_rec.rectChanged.connect(self.dst_changed)
        v2.addWidget(self.view_rec)
        self.chk_dst_own = QCheckBox("Рамка только для этой расписки")
        v2.addWidget(self.chk_dst_own)
        split.addWidget(g2)
        split.setSizes([300, 450, 450])
        lay.addWidget(split, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(big_button("↺ Рамки по умолчанию", self.reset_rects))
        bottom.addStretch()
        bottom.addWidget(big_button("👁 Предпросмотр", self.preview))
        bottom.addWidget(big_button("💾 Сохранить новый PDF…", self.save, primary=True))
        lay.addLayout(bottom)

    # ---------- загрузка ----------
    def open_acts(self, path: str = ""):
        path = path or ask_open_pdf(self, "Файл с актами")
        if path:
            self.acts_path, self.acts = path, pymupdf.open(path)
            self.lbl_acts.setText(f"{os.path.basename(path)} ({len(self.acts)} стр.)")
            self.rebuild()

    def open_receipts(self, path: str = ""):
        path = path or ask_open_pdf(self, "Файл с расписками")
        if path:
            self.rec_path, self.rec = path, pymupdf.open(path)
            self.lbl_rec.setText(f"{os.path.basename(path)} ({len(self.rec)} стр.)")
            self.rebuild()

    def rebuild(self):
        if not (self.acts and self.rec):
            return
        self._loading = True
        self.job = sig.TransferJob(self.acts_path, self.rec_path, sig.guess_mapping(len(self.rec), len(self.acts)))
        self.table.setRowCount(len(self.rec))
        for i in range(len(self.rec)):
            it = QTableWidgetItem(thumb_icon(self.rec, i), f"стр. {i + 1}")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, it)
            cb = QComboBox()
            cb.setIconSize(QSize(40, 56))
            cb.addItem("— не ставить (оставить пустой) —", None)
            for a in range(len(self.acts)):
                cb.addItem(thumb_icon(self.acts, a, dpi=20), f"Акт, стр. {a + 1}", a)
            m = self.job.mapping[i]
            cb.setCurrentIndex(0 if m is None else m + 1)
            cb.currentIndexChanged.connect(lambda _=0, row=i, c=cb: self.map_changed(row, c))
            self.table.setCellWidget(i, 1, cb)
        self._loading = False
        self.table.setCurrentCell(0, 1)
        self.show_row(0)

    def set_mapping(self, mapping):
        """Для тестов/скриптов: задать соответствие (0-based, None — пусто)."""
        for i, m in enumerate(mapping):
            self.table.cellWidget(i, 1).setCurrentIndex(0 if m is None else m + 1)

    def map_changed(self, row, cb):
        self.job.mapping[row] = cb.currentData()
        if row == self.table.currentRow():
            self.show_row(row)

    # ---------- рамки ----------
    def current(self):
        r = self.table.currentRow()
        if not self.job or r < 0:
            return None, None
        return r, self.job.mapping[r]

    def show_row(self, r):
        if not self.job or r is None or r < 0:
            return
        self._loading = True
        a = self.job.mapping[r]
        self.view_rec.box = None
        self.view_rec.set_page(page_pixmap(self.rec, r, 90))
        self.chk_dst_own.setChecked(r in self.job.dst_overrides)
        self.view_rec.set_nrect(self.job.dst_overrides.get(r, self.job.dst_rect))
        self.view_act.box = None
        if a is None:
            self.view_act.scene().clear()
            self.view_act.page_item = None
            self.view_act.scene().addText("Подпись не ставится")
        else:
            self.view_act.set_page(page_pixmap(self.acts, a, 90))
            self.chk_src_own.setChecked(a in self.job.src_overrides)
            self.view_act.set_nrect(self.job.src_overrides.get(a, self.job.src_rect))
        self._loading = False

    def src_changed(self):
        r, a = self.current()
        nr = self.view_act.get_nrect()
        if self._loading or a is None or nr is None:
            return
        if self.chk_src_own.isChecked():
            self.job.src_overrides[a] = nr
        else:
            self.job.src_rect = nr

    def dst_changed(self):
        r, _ = self.current()
        nr = self.view_rec.get_nrect()
        if self._loading or r is None or nr is None:
            return
        if self.chk_dst_own.isChecked():
            self.job.dst_overrides[r] = nr
        else:
            self.job.dst_rect = nr

    def reset_rects(self):
        if self.job:
            self.job.src_rect, self.job.dst_rect = sig.DEFAULT_SRC_RECT, sig.DEFAULT_DST_RECT
            self.job.src_overrides.clear()
            self.job.dst_overrides.clear()
            self.show_row(self.table.currentRow())

    # ---------- результат ----------
    def preview(self):
        if not self.job:
            return error(self, "Сначала откройте акты и расписки.")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Предпросмотр")
            dlg.resize(900, 800)
            area = QScrollArea(dlg)
            area.setWidgetResizable(True)
            inner = QWidget()
            v = QVBoxLayout(inner)
            for i in range(len(self.rec)):
                img = sig.preview_page(self.job, i, dpi=70)
                lab = QLabel()
                lab.setPixmap(np_to_pixmap(img))
                m = self.job.mapping[i]
                v.addWidget(QLabel(f"<b>Расписка стр. {i + 1}</b> ← " + ("не ставим" if m is None else f"акт стр. {m + 1}")))
                v.addWidget(lab)
            area.setWidget(inner)
            QVBoxLayout(dlg).addWidget(area)
        finally:
            QApplication.restoreOverrideCursor()
        dlg.exec()

    def save(self, out: str = ""):
        if not self.job:
            return error(self, "Сначала откройте акты и расписки.")
        out = out or ask_save(self, safe_output_path(self.rec_path, "с_подписями"))
        if not out:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            rep = sig.apply_transfer(self.job, out)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            return error(self, str(e))
        QApplication.restoreOverrideCursor()
        msg = f"Сохранено: {out}\nПодписей поставлено: {len(rep['placed'])}, пустых: {len(rep['blank'])}."
        if rep["empty_ink"]:
            msg += "\nВНИМАНИЕ: в рамке не найдено синих чернил для расписок: " + \
                   ", ".join(str(i + 1) for i in rep["empty_ink"])
        info(self, msg)
