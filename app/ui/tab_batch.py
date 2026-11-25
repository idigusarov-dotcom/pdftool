"""Вкладка «Пакет»: несколько пар «акты + расписки» обрабатываются за один раз."""
from __future__ import annotations

import copy
import os
import re

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core import batch, compress
from ..core import templates as tp
from ..core.pdfutil import safe_output_path
from . import style
from .infra import UndoMixin, last_dir, remember_dir, run_task
from .templbar import NONE
from .chrome import DropZone, ScreenHeader
from .widgets import ask_open_pdf, big_button, error, info, open_folder, progress_window

HELP = ("Пакетный режим: добавьте несколько пар (файл АКТОВ + файл РАСПИСОК), выберите для каждой шаблон бланка "
        "и имя результата. Сопоставление по умолчанию — по порядку страниц; чтобы проверить и исправить его, "
        "нажмите «Проверить пару», поправьте таблицу и вернитесь — «Применить проверку». "
        "Затем «Обработать все». Исходные файлы никогда не перезаписываются.")
C_N, C_ACTS, C_REC, C_TPL, C_OUT, C_MAP, C_STAT = range(7)


class _MiddleElide(QStyledItemDelegate):
    """Путь результата сокращается посередине: «/Users/…/Расписки_с_подписями.pdf» — имя файла видно."""

    def paint(self, painter, option, index):
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = opt.fontMetrics.elidedText(opt.text, Qt.ElideMiddle, max(20, opt.rect.width() - 12))
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)


def pair_order(first, second):
    def role(path):
        name = os.path.splitext(os.path.basename(path))[0].casefold()
        if re.search(r"(?:^|[^а-яё])акт(?:ы|ов|а)?(?:$|[^а-яё])", name):
            return "acts"
        if re.search(r"(?:^|[^а-яё])расписк[аиу]?(?:$|[^а-яё])", name):
            return "receipts"
        return None

    left, right = role(first), role(second)
    if left != right and (right == "acts" or left == "receipts"):
        return second, first
    return first, second


class PairFilesDialog(QDialog):
    """В очереди соседние строки образуют пару. Роли видны до добавления в пакет."""
    def __init__(self, parent, paths):
        super().__init__(parent)
        self.setWindowTitle("Собрать пары файлов")
        self.resize(600, 380)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Расположите акты перед соответствующими расписками."))
        self.files = QListWidget()
        self.files.setAccessibleName("Файлы в порядке обработки")
        self.files.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(self.files, 1)
        row = QHBoxLayout()
        row.addWidget(big_button("Добавить PDF…", self.choose, icon_name="plus"))
        self.up = big_button("Выше", lambda: self.move(-1), icon_name="chevron-up")
        self.down = big_button("Ниже", lambda: self.move(1), icon_name="chevron-down")
        self.remove = big_button("Убрать", self.remove_current, icon_name="x")
        for button in (self.up, self.down, self.remove):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setProperty("kind", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.files.currentRowChanged.connect(self.refresh)
        self.files.model().rowsMoved.connect(self.refresh)
        initial = list(paths)
        for i in range(0, len(initial) - 1, 2):
            initial[i:i+2] = pair_order(*initial[i:i+2])
        self.append_files(initial)

    def append_files(self, paths):
        for path in paths:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, max(30, self.files.fontMetrics().height() + 10)))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.files.addItem(item)
        if self.files.count() and self.files.currentRow() < 0:
            self.files.setCurrentRow(0)
        self.refresh()

    def choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить PDF в пары", last_dir(), "PDF (*.pdf)")
        if paths:
            remember_dir(paths[0])
            self.append_files(paths)

    def move(self, step):
        row = self.files.currentRow()
        if 0 <= row + step < self.files.count() and row >= 0:
            item = self.files.takeItem(row)
            self.files.insertItem(row + step, item)
            self.files.setCurrentRow(row + step)
            self.refresh()

    def remove_current(self):
        self.files.takeItem(self.files.currentRow())
        self.refresh()

    def refresh(self, *_):
        n = self.files.count()
        for i in range(n):
            item = self.files.item(i)
            role = "Расписки" if i % 2 else "Акты"
            item.setText(f"{i // 2 + 1}. {role}  ·  {os.path.basename(item.data(Qt.UserRole))}")
        self.note.setText("Для последней пары нужен второй PDF." if n % 2 else f"Полных пар: {n // 2}")
        self.ok.setText(f"Добавить в пакет ({n // 2})")
        self.ok.setEnabled(n > 0 and n % 2 == 0)
        row = self.files.currentRow()
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < n - 1)
        self.remove.setEnabled(row >= 0)

    def pairs(self):
        paths = [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]
        return list(zip(paths[::2], paths[1::2]))


class BatchTab(QWidget, UndoMixin):
    def __init__(self, sign_tab=None, show_sign=None):
        super().__init__()
        self.init_undo()
        self.sign = sign_tab
        self.show_sign = show_sign or (lambda: None)
        self.items: list[batch.BatchItem] = []
        self.status: list[str] = []
        self._review_item = None
        self.task = None
        self.last_results: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(12)
        self.header = ScreenHeader("Пакетная обработка",
                                   "Сожмите несколько PDF со сканами или перенесите подписи для нескольких пар файлов.",
                                   ["Добавьте пары файлов", "Проверьте пары страниц", "Обработайте все"])
        lay.addWidget(self.header)
        top = QHBoxLayout()
        self.btn_add = big_button("➕ Добавить пару…", self.add_pair, primary=False)
        top.addWidget(self.btn_add)
        self.btn_import = big_button("Добавить несколько…", self.import_files, icon_name="files",
                                     tip="Выбрать PDF и проверить состав каждой пары")
        top.addWidget(self.btn_import)
        self.btn_remove = big_button("✖ Убрать", self.remove_pair, ghost=True)
        top.addWidget(self.btn_remove)
        top.addStretch()
        self.btn_compress = big_button("Сжать несколько PDF…", self.compress_files, icon_name="file-down",
                                       tip="Сжать PDF независимо друг от друга и сохранить копии рядом с исходными файлами")
        top.addWidget(self.btn_compress)
        lay.addLayout(top)
        actions = QHBoxLayout()
        self.queue_label = QLabel()
        self.queue_label.setObjectName("Muted")
        actions.addWidget(self.queue_label, 1)
        self.btn_review = big_button("Проверить пару", self.review, icon_name="eye",
                                     tip="Открыть выбранную пару на экране «Подписи», чтобы проверить пары страниц")
        actions.addWidget(self.btn_review)
        self.btn_take = big_button("Применить проверку", lambda: self.take_mapping(), icon_name="check",
                                   tip="Взять сопоставление, рамки и настройки из экрана «Подписи»")
        actions.addWidget(self.btn_take)
        self.table = QTableWidget(0, 7)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setTextElideMode(Qt.ElideMiddle)     # длинный путь: видно и начало, и имя файла
        self.table.setItemDelegateForColumn(C_OUT, _MiddleElide(self.table))
        self.table.setHorizontalHeaderLabels(["№", "Акты", "Расписки", "Шаблон", "Результат", "Сопоставление", "Итог"])
        for c, w in ((C_N, 36), (C_ACTS, 150), (C_REC, 150), (C_TPL, 170), (C_OUT, 210), (C_MAP, 115)):
            self.table.setColumnWidth(c, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self.item_edited)
        self.dz = DropZone("Добавить пару", "Перетащите PDF сюда. Для нескольких пар можно проверить роли и порядок файлов.",
                           icon_name="files")
        self.dz.clicked.connect(self.add_pair)
        self.dz.files.connect(self.accept_files)
        lay.addWidget(self.dz)
        lay.addLayout(actions)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setFormat("%p%")
        bottom.addWidget(self.bar, 1)
        self.btn_cancel = big_button("Отмена", None, icon_name="x", tip="Остановить обработку (готовые файлы останутся)")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        bottom.addWidget(self.btn_cancel)
        self.btn_folder = big_button("📂 Папка результата", self.open_folder)
        bottom.addWidget(self.btn_folder)
        self.btn_retry = big_button("Обработать оставшиеся", self.run_remaining, icon_name="refresh-ccw",
                                    tip="Обработать новые, отменённые и ошибочные пары")
        bottom.addWidget(self.btn_retry)
        self.btn_run = big_button("▶ Обработать все", self.run_all, primary=True)
        bottom.addWidget(self.btn_run)
        lay.addLayout(bottom)
        self._filling = False
        self.reset_undo()
        self.table.currentCellChanged.connect(self.sync_actions)
        self.sync_actions()

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not self.items:
            return 0
        if len(self.last_results) == len(self.items) and all(r["status"] == "ok" for r in self.last_results):
            return 3
        return 2 if any(i.mapping is not None for i in self.items) else 1

    # ---------- пока идёт обработка, список пар менять нельзя ----------
    running = False
    BUSY_MSG = "Идёт обработка пакета. Дождитесь окончания или нажмите «Отмена»."

    def _busy(self) -> bool:
        if self.running:
            error(self, self.BUSY_MSG)
        return self.running

    def _set_running(self, on: bool):
        self.running = on
        self.table.setEnabled(not on)
        self.dz.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        self.sync_actions()

    def remaining_rows(self):
        finished = {r["pair"] for r in self.last_results if r["status"] == "ok"}
        return [i for i in range(len(self.items)) if i not in finished]

    def sync_actions(self, *_):
        ready = not self.running
        selected = 0 <= self.table.currentRow() < len(self.items)
        self.btn_add.setEnabled(ready)
        self.btn_import.setEnabled(ready)
        self.btn_compress.setEnabled(ready)
        self.btn_remove.setEnabled(ready and selected)
        self.btn_review.setEnabled(ready and selected and self.sign is not None)
        self.btn_take.setEnabled(ready and selected and self.items[self.table.currentRow()] is self._review_item)
        self.btn_run.setEnabled(ready and bool(self.items))
        self.btn_retry.setVisible(bool(self.last_results and self.remaining_rows()))
        self.btn_retry.setEnabled(ready)
        self.btn_folder.setEnabled(any(r["status"] == "ok" for r in self.last_results))
        completed = sum(r["status"] == "ok" for r in self.last_results)
        self.queue_label.setText(f"Пар в очереди: {len(self.items)} · Файлов создано: {completed}"
                                 if self.items else "Добавьте акты и соответствующие им расписки.")
        self.bar.setVisible(self.running)
        self.btn_cancel.setVisible(self.running)

    def invalidate_result(self, row):
        self.last_results = [r for r in self.last_results if r["pair"] != row]
        self.status[row] = ""

    def accept_files(self, paths: list[str]):
        if self._busy():
            return
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        if not pdfs:
            return
        if len(pdfs) == 2:
            a, r = pair_order(*pdfs)
            self.add_pair(a, r)
            info(self, f"Пара добавлена: акты — {os.path.basename(a)}, расписки — {os.path.basename(r)}.")
        else:
            self.import_files(pdfs)

    def import_files(self, paths=None):
        if self._busy():
            return
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "PDF для пакетной обработки", last_dir(), "PDF (*.pdf)")
        if not paths:
            return
        remember_dir(paths[0])
        dlg = PairFilesDialog(self, paths)
        if dlg.exec() == QDialog.Accepted:
            for acts, receipts in dlg.pairs():
                self.append_pair(acts, receipts)
            self.fill()
            self.table.setCurrentCell(len(self.items) - 1, C_ACTS)
            self.commit("добавить несколько пар")
        dlg.deleteLater()

    def compress_files(self):
        if self._busy():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "PDF для пакетного сжатия", last_dir(), "PDF (*.pdf)")
        if not paths:
            return
        remember_dir(paths[0])
        levels = {level.title: key for key, level in compress.LEVELS.items()}
        title, ok = QInputDialog.getItem(self, "Сжать несколько PDF",
                                        f"Файлов: {len(paths)}. Копии сохранятся рядом с оригиналами.\nСтепень сжатия",
                                        list(levels), list(levels.values()).index(compress.DEFAULT_LEVEL), False)
        if not ok:
            return
        with progress_window(self, "Сжимаю PDF. Обработано файлов") as prog:
            results = prog.run(compress.compress_many, paths, levels[title])
        dlg = QDialog(self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.setWindowTitle("Результаты сжатия")
        dlg.resize(680, 370)
        layout = QVBoxLayout(dlg)
        saved = [out for _, out, _ in results if out]
        layout.addWidget(QLabel(f"Создано сжатых копий: {len(saved)} из {len(results)}"))
        report = QTextEdit()
        report.setReadOnly(True)
        report.setPlainText("\n\n".join(f"{path}\n{status}" + (f"\n→ {out}" if out else "")
                                        for path, out, status in results))
        layout.addWidget(report)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(dlg.reject)
        if saved and len({os.path.dirname(p) for p in saved}) == 1:
            folder = buttons.addButton("Открыть папку", QDialogButtonBox.ActionRole)
            folder.clicked.connect(lambda: open_folder(saved[0]))
        layout.addWidget(buttons)
        dlg.open()

    def tour_steps(self):
        from .tour import Step
        return [
            Step(None, "Пакетный режим", "Когда пар «акты + расписки» много (например, по разным домам), их можно "
                 "обработать за один раз. Нажимайте «Далее»."),
            Step(self.btn_compress, "Сжать несколько PDF", "Выберите документы и степень сжатия. Копии появятся рядом с исходниками, а в конце откроется отчёт по каждому файлу."),
            Step(self.btn_add, "1. Добавьте пару", "Нажмите и выберите сначала файл <b>актов</b>, потом файл "
                 "<b>расписок</b>. Или перетащите оба файла на плашку ниже."),
            Step(self.btn_import, "Несколько пар сразу", "Выберите PDF и расставьте их попарно. Для каждой пары первым идёт файл актов, вторым — файл расписок."),
            Step(self.table, "Список пар", "Каждая строка — одна пара. В столбце «Шаблон» выберите шаблон бланка "
                 "(если сохраняли). Путь результата можно изменить двойным щелчком."),
            Step(self.btn_review, "2. Проверьте пары страниц", "Без проверки страницы сопоставляются по порядку. "
                 "Нажмите — пара откроется на экране «Подписи», там поправьте таблицу и рамки…"),
            Step(self.btn_take, "…и заберите обратно", "Вернитесь сюда и нажмите — исправления запишутся в пакет. "
                 "В столбце «Сопоставление» появится «проверено ✓»."),
            Step(self.btn_run, "3. Обработайте все", "Программа создаст файлы для всех пар по очереди. Можно "
                 "остановить кнопкой «Отмена». Чтобы продолжить, нажмите «Обработать оставшиеся». В конце покажется итог по каждой паре."),
            Step(self.btn_folder, "Готовые файлы", "Открывает папку, где лежат результаты."),
        ]

    # ---------- Ctrl+Z ----------
    def get_state(self):
        return copy.deepcopy(self.items)

    def set_state(self, st):
        # У уже готовых неизменённых пар сохраняем результат при отмене правки соседней строки.
        old_items, old_results, old_status = self.items, self.last_results, self.status
        self.items = copy.deepcopy(st or [])
        self.last_results = []
        self._review_item = None
        self.status = [""] * len(self.items)
        for res in old_results:
            old_row = res["pair"]
            if old_items[old_row] in self.items:
                row = self.items.index(old_items[old_row])
                self.last_results.append(dict(res, pair=row))
                self.status[row] = old_status[old_row]
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
            out.setToolTip(it.out_path or "Двойной щелчок — выбрать имя результата")
            self.table.setItem(r, C_OUT, out)
            self.table.setItem(r, C_MAP, self._ro("по порядку" if it.mapping is None else "проверено ✓"))
            st = self.status[r] if r < len(self.status) else ""
            si = self._ro(st or "В очереди")
            if st.startswith("готово") and "без подписи" not in st:
                si.setBackground(style.mark("#c8e6c9"))
            elif st:
                warn = st.startswith("готово") or st.startswith("проверьте")
                si.setBackground(style.mark("#ffe082") if warn else style.mark("#ffcdd2"))
                si.setToolTip(st)
            self.table.setItem(r, C_STAT, si)
        self._filling = False
        self.sync_actions()

    def refresh_templates(self):
        self.fill()

    def _unique_out(self, path: str) -> str:
        taken = {batch.path_key(i.out_path) for i in self.items}
        base, ext = os.path.splitext(path)
        k, p = 2, path
        while batch.path_key(p) in taken or os.path.exists(p):
            p = f"{base}_{k}{ext}"
            k += 1
        return p

    def add_pair(self, acts: str = "", receipts: str = "", template: str | None = None, out: str = ""):
        if self._busy():
            return
        acts = acts or ask_open_pdf(self, "Пара: файл с АКТАМИ")
        if not acts:
            return
        receipts = receipts or ask_open_pdf(self, "Пара: файл с РАСПИСКАМИ")
        if not receipts:
            return
        self.append_pair(acts, receipts, template, out)
        self.fill()
        self.table.setCurrentCell(len(self.items) - 1, C_ACTS)
        self.commit("пара добавлена")

    def append_pair(self, acts, receipts, template=None, out=""):
        out = out or self._unique_out(safe_output_path(receipts, "с_подписями"))
        self.items.append(batch.BatchItem(acts, receipts, out, template=template))
        self.status.append("")

    def remove_pair(self):
        if self._busy():
            return
        r = self.table.currentRow()
        if 0 <= r < len(self.items):
            if self._review_item is self.items[r]:
                self._review_item = None
            self.last_results = [dict(res, pair=res["pair"] - (res["pair"] > r))
                                 for res in self.last_results if res["pair"] != r]
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
        if self._filling or self.running or not (0 <= row < len(self.items)):
            return
        self.items[row].template = None if txt == NONE else txt
        self.items[row].reviewed_settings = None
        self.invalidate_result(row)
        self.fill()
        self.commit("шаблон пары")

    def item_edited(self, item):
        if self._filling or self.running or item.column() != C_OUT:
            return
        self.items[item.row()].out_path = item.text().strip()
        self.invalidate_result(item.row())
        self.fill()
        self.commit("имя результата")

    # ---------- проверка сопоставления ----------
    def review(self):
        r = self.table.currentRow()
        if not (0 <= r < len(self.items)) or self.sign is None:
            return error(self, "Выберите пару в таблице.")
        it = self.items[r]
        if not self.sign.load_pair(it.acts_path, it.receipts_path, it.template, it.mapping,
                                   it.src_overrides, it.dst_overrides, it.reviewed_settings):
            return   # причина уже показана уведомлением
        self._review_item = it
        self.sync_actions()
        self.show_sign()

    def take_mapping(self, row: int | None = None):
        if self._busy():
            return
        r = row if row is not None else self.table.currentRow()
        if not (0 <= r < len(self.items)) or self.sign is None:
            return error(self, "Выберите пару в таблице.")
        st = self.sign.export_pair()
        it = self.items[r]
        if it is not self._review_item or not st or os.path.abspath(st["acts"]) != os.path.abspath(it.acts_path) or \
                os.path.abspath(st["receipts"]) != os.path.abspath(it.receipts_path):
            return error(self, "Во вкладке «Подписи» открыта другая пара файлов. "
                               "Нажмите «Проверить пару» для этой пары.")
        it.mapping = st["mapping"]
        it.src_overrides, it.dst_overrides = st["src_overrides"], st["dst_overrides"]
        it.reviewed_settings = st["reviewed_settings"]
        it.template = st["template"]
        self.invalidate_result(r)
        self.fill()
        self.commit("сопоставление из вкладки")

    # ---------- обработка ----------
    def run_all(self, wait: bool = False):
        self.run_rows(list(range(len(self.items))))

    def run_remaining(self):
        self.run_rows(self.remaining_rows())

    def run_rows(self, rows):
        if self.running:
            return
        if not rows:
            return error(self, "Нет пар для обработки.")
        # Проверяем конфликты путей со всей очередью, включая уже завершённые пары.
        errs = batch.check_items(self.items, check_files=False)
        if errs:
            return error(self, "Нельзя начать:\n" + "\n".join(errs))
        existing = [self.items[row].out_path for row in rows if os.path.isfile(self.items[row].out_path)]
        if existing:
            box = QMessageBox(QMessageBox.Question, "Заменить готовые файлы?",
                              f"Уже существуют файлы результата ({len(existing)}). Заменить их?",
                              QMessageBox.Yes | QMessageBox.Cancel, self)
            box.setDetailedText("\n".join(existing))
            box.button(QMessageBox.Yes).setText("Заменить")
            box.button(QMessageBox.Cancel).setText("Отмена")
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Yes:
                return
        items = copy.deepcopy([self.items[row] for row in rows])
        temps = tp.load_all()
        self._set_running(True)
        self.bar.setValue(0)

        def done(results):
            self.task = None
            previous = [res for res in self.last_results if res["pair"] not in rows]
            for res in results:
                res["pair"] = rows[res["pair"]]
                self.status[res["pair"]] = self.describe(res)
            self.last_results = sorted(previous + results, key=lambda r: r["pair"])
            self._set_running(False)
            self.fill()
            self.show_summary(self.last_results)

        def fail(msg):
            self.task = None
            self._set_running(False)
            error(self, "Нельзя начать обработку: " + msg)

        self.task = run_task(lambda prog: batch.run_batch(items, temps, prog, pair_numbers=[row + 1 for row in rows]), done, fail,
                             lambda i, n: self.bar.setValue(int(100 * i / max(n, 1))))

    def cancel(self):
        if self.task:
            self.task.cancelled = True
            self.btn_cancel.setEnabled(False)

    @staticmethod
    def describe(res) -> str:
        if res["status"] == "error":
            from ..core.pdfutil import human_error
            return "ошибка: " + human_error(res["error"]).replace("\n", " ")
        if res["status"] == "cancelled":
            return "отменено (файл не создан)"
        if res["status"] == "skipped":
            return "не обработано (отмена)"
        if not res["placed"]:
            # файл создан, но подписей в нём нет — это не «готово», а повод проверить рамки
            if res["empty_ink"]:
                return f"проверьте рамки: подписей 0 (пустая рамка на {len(res['empty_ink'])} стр.)"
            return "проверьте сопоставление: подписей 0 (все расписки — «не ставить»)"
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
        head = f"Обработано пар: {ok} из {len(results)}"
        zero = [r["pair"] + 1 for r in results if r["status"] == "ok" and not r["placed"]]
        if zero:
            head += (f"\n\n⚠ Без единой подписи: пары {', '.join(map(str, zero))}. Скорее всего красная рамка "
                     "(откуда брать подпись) не попала на подпись в актах. Выделите пару и нажмите "
                     "«Проверить пару», поправьте рамку и обработайте снова.")
        return head + "\n\n" + "\n".join(lines)

    def show_summary(self, results):
        dlg = QDialog(self)
        dlg.setWindowTitle("Итог пакетной обработки")
        dlg.resize(760, 420)
        v = QVBoxLayout(dlg)
        zero = [r["pair"] + 1 for r in results if r["status"] == "ok" and not r["placed"]]
        self._summary_warn = None
        if zero:
            w = QLabel(f"Проверьте рамки: в {'паре' if len(zero) == 1 else 'парах'} {', '.join(map(str, zero))} "
                       "не поставлено ни одной подписи.")
            w.setObjectName("WarnBanner")
            w.setWordWrap(True)
            v.addWidget(w)
            self._summary_warn = w
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(self.summary_text(results))
        v.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Закрыть")
        b = bb.addButton("📂 Открыть папку результатов", QDialogButtonBox.ActionRole)
        b.setEnabled(any(r["status"] == "ok" for r in results))
        b.clicked.connect(self.open_folder)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        self._summary = dlg
        dlg.open()

    def open_folder(self):
        completed = {r["pair"]: r["out"] for r in self.last_results if r["status"] == "ok"}
        if not completed:
            return
        path = completed.get(self.table.currentRow()) or next(iter(completed.values()))
        d = os.path.dirname(os.path.abspath(path))
        remember_dir(d)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))
