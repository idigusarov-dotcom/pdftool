"""Вкладка «Пакет»: несколько пар «акты + расписки» обрабатываются за один раз."""
from __future__ import annotations

import copy
import os
from dataclasses import asdict

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core import batch
from ..core import templates as tp
from ..core.pdfutil import safe_output_path
from .infra import UndoMixin, remember_dir, run_task
from .templbar import NONE
from .widgets import ask_open_pdf, big_button, error, info

HELP = ("Пакетный режим: добавьте несколько пар (файл АКТОВ + файл РАСПИСОК), выберите для каждой шаблон бланка "
        "и имя результата. Сопоставление по умолчанию — по порядку страниц; чтобы проверить и исправить его, "
        "нажмите «Проверить во вкладке «Подписи»», поправьте таблицу и вернитесь — «Взять сопоставление». "
        "Затем «Обработать все». Исходные файлы никогда не перезаписываются.")
C_N, C_ACTS, C_REC, C_TPL, C_OUT, C_MAP, C_STAT = range(7)


class BatchTab(QWidget, UndoMixin):
    def __init__(self, sign_tab=None, show_sign=None):
        super().__init__()
        self.init_undo()
        self.sign = sign_tab
        self.show_sign = show_sign or (lambda: None)
        self.items: list[batch.BatchItem] = []
        self.status: list[str] = []
        self.task = None
        self.last_results: list[dict] = []
        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("➕ Добавить пару…", self.add_pair))
        top.addWidget(big_button("✖ Убрать", self.remove_pair))
        top.addWidget(big_button("✍ Проверить во вкладке «Подписи»", self.review))
        top.addWidget(big_button("⤶ Взять сопоставление", self.take_mapping))
        top.addStretch()
        lay.addLayout(top)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["№", "Акты", "Расписки", "Шаблон", "Результат", "Сопоставление", "Итог"])
        for c, w in ((C_N, 40), (C_ACTS, 220), (C_REC, 220), (C_TPL, 170), (C_OUT, 260), (C_MAP, 150)):
            self.table.setColumnWidth(c, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self.item_edited)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setFormat("%p%")
        bottom.addWidget(self.bar, 1)
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        bottom.addWidget(self.btn_cancel)
        self.btn_folder = big_button("📂 Открыть папку результатов", self.open_folder)
        bottom.addWidget(self.btn_folder)
        self.btn_run = big_button("▶ Обработать все", self.run_all, primary=True)
        bottom.addWidget(self.btn_run)
        lay.addLayout(bottom)
        self._filling = False
        self.reset_undo()

    # ---------- Ctrl+Z ----------
    def get_state(self):
        return [asdict(it) for it in self.items]

    def set_state(self, st):
        self.items = [batch.BatchItem(**copy.deepcopy(d)) for d in (st or [])]
        self.status = [""] * len(self.items)
        self.fill()

    # ---------- таблица ----------
    def _ro(self, text, tip=""):
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setToolTip(tip or text)
        return it

    def fill(self):
        self._filling = True
        names = [t.name for t in tp.load_all()]
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            self.table.setItem(r, C_N, self._ro(str(r + 1)))
            self.table.setItem(r, C_ACTS, self._ro(os.path.basename(it.acts_path), it.acts_path))
            self.table.setItem(r, C_REC, self._ro(os.path.basename(it.receipts_path), it.receipts_path))
            cb = self.table.cellWidget(r, C_TPL)
            if not isinstance(cb, QComboBox):   # виджеты не пересоздаём — только обновляем список
                cb = QComboBox()
                cb.currentTextChanged.connect(lambda txt, c=cb: self.template_changed(self._row_of(c), txt))
                self.table.setCellWidget(r, C_TPL, cb)
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(NONE)
            cb.addItems(names)
            cb.setCurrentIndex(max(0, cb.findText(it.template or NONE)))
            cb.blockSignals(False)
            out = QTableWidgetItem(it.out_path)
            out.setToolTip("Двойной щелчок — изменить путь результата")
            self.table.setItem(r, C_OUT, out)
            self.table.setItem(r, C_MAP, self._ro("по порядку" if it.mapping is None else "проверено ✓"))
            st = self.status[r] if r < len(self.status) else ""
            si = self._ro(st)
            if st.startswith("готово") and "без подписи" not in st:
                si.setBackground(QColor("#c8e6c9"))
            elif st:
                si.setBackground(QColor("#ffe082") if st.startswith("готово") else QColor("#ffcdd2"))
            self.table.setItem(r, C_STAT, si)
        self._filling = False

    def refresh_templates(self):
        self.fill()

    def _unique_out(self, path: str) -> str:
        taken = {os.path.normcase(os.path.abspath(i.out_path)) for i in self.items}
        base, ext = os.path.splitext(path)
        k, p = 2, path
        while os.path.normcase(os.path.abspath(p)) in taken:
            p = f"{base}_{k}{ext}"
            k += 1
        return p

    def add_pair(self, acts: str = "", receipts: str = "", template: str | None = None, out: str = ""):
        acts = acts or ask_open_pdf(self, "Пара: файл с АКТАМИ")
        if not acts:
            return
        receipts = receipts or ask_open_pdf(self, "Пара: файл с РАСПИСКАМИ")
        if not receipts:
            return
        out = out or self._unique_out(safe_output_path(receipts, "с_подписями"))
        self.items.append(batch.BatchItem(acts, receipts, out, template=template))
        self.status.append("")
        self.fill()
        self.commit("пара добавлена")

    def remove_pair(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.items):
            del self.items[r]
            del self.status[r]
            self.fill()
            self.commit("пара убрана")

    def _row_of(self, cb) -> int:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, C_TPL) is cb:
                return r
        return -1

    def template_changed(self, row, txt):
        if self._filling or not (0 <= row < len(self.items)):
            return
        self.items[row].template = None if txt == NONE else txt
        self.commit("шаблон пары")

    def item_edited(self, item):
        if self._filling or item.column() != C_OUT:
            return
        self.items[item.row()].out_path = item.text().strip()
        self.commit("имя результата")

    # ---------- проверка сопоставления ----------
    def review(self):
        r = self.table.currentRow()
        if not (0 <= r < len(self.items)) or self.sign is None:
            return error(self, "Выберите пару в таблице.")
        it = self.items[r]
        self.sign.load_pair(it.acts_path, it.receipts_path, it.template, it.mapping,
                            it.src_overrides, it.dst_overrides)
        self._review_row = r
        self.show_sign()

    def take_mapping(self, row: int | None = None):
        r = row if row is not None else getattr(self, "_review_row", self.table.currentRow())
        if not (0 <= r < len(self.items)) or self.sign is None:
            return error(self, "Выберите пару в таблице.")
        st = self.sign.export_pair()
        it = self.items[r]
        if not st or os.path.abspath(st["acts"]) != os.path.abspath(it.acts_path) or \
                os.path.abspath(st["receipts"]) != os.path.abspath(it.receipts_path):
            return error(self, "Во вкладке «Подписи» открыта другая пара файлов. "
                               "Нажмите «Проверить во вкладке «Подписи»» для этой пары.")
        it.mapping = st["mapping"]
        it.src_overrides, it.dst_overrides = st["src_overrides"], st["dst_overrides"]
        if st["template"]:
            it.template = st["template"]
        self.fill()
        self.commit("сопоставление из вкладки")

    # ---------- обработка ----------
    def run_all(self, wait: bool = False):
        if not self.items:
            return error(self, "Добавьте хотя бы одну пару.")
        errs = batch.check_items(self.items)
        if errs:
            return error(self, "Нельзя начать:\n" + "\n".join(errs))
        items = copy.deepcopy(self.items)
        temps = tp.load_all()
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.bar.setValue(0)

        def done(results):
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.last_results = results
            for res in results:
                self.status[res["pair"]] = self.describe(res)
            self.fill()
            self.show_summary(results)

        def fail(msg):
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            error(self, "Пакет остановлен: " + msg)

        self.task = run_task(lambda prog: batch.run_batch(items, temps, prog), done, fail,
                             lambda i, n: self.bar.setValue(int(100 * i / max(n, 1))))

    def cancel(self):
        if self.task:
            self.task.cancelled = True
            self.btn_cancel.setEnabled(False)

    @staticmethod
    def describe(res) -> str:
        if res["status"] == "error":
            return "ошибка: " + res["error"]
        if res["status"] == "cancelled":
            return "отменено (файл не создан)"
        if res["status"] == "skipped":
            return "не обработано (отмена)"
        s = f"готово: подписей {res['placed']}"
        if res["blank"]:
            s += f", пустых {len(res['blank'])}"
        if res["empty_ink"]:
            s += ", без подписи (нет чернил): стр. " + ", ".join(str(i + 1) for i in res["empty_ink"])
        return s

    def summary_text(self, results) -> str:
        lines = []
        for res in results:
            it = self.items[res["pair"]] if res["pair"] < len(self.items) else None
            name = os.path.basename(it.receipts_path) if it else "?"
            lines.append(f"Пара {res['pair'] + 1} ({name}): {self.describe(res)}")
            if res["status"] == "ok":
                lines.append(f"    → {res['out']}")
        ok = sum(r["status"] == "ok" for r in results)
        return f"Обработано пар: {ok} из {len(results)}\n\n" + "\n".join(lines)

    def show_summary(self, results):
        dlg = QDialog(self)
        dlg.setWindowTitle("Итог пакетной обработки")
        dlg.resize(760, 420)
        v = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(self.summary_text(results))
        v.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        b = bb.addButton("📂 Открыть папку результатов", QDialogButtonBox.ActionRole)
        b.clicked.connect(self.open_folder)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        self._summary = dlg
        dlg.open()

    def open_folder(self):
        outs = [r["out"] for r in self.last_results if r["status"] == "ok"] or [i.out_path for i in self.items]
        if not outs:
            return
        d = os.path.dirname(os.path.abspath(outs[0]))
        remember_dir(d)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))
