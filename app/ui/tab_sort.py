"""Сортировка страниц PDF по номерам и текстовым меткам."""
from __future__ import annotations

import os

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMessageBox, QProgressDialog, QRadioButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import numbers, sorting
from ..core.logsetup import log
from ..core.pdfutil import NRect, PdfError, check_output_path, safe_output_path
from . import style
from .infra import UndoMixin, load_rect, run_task, save_rect, settings
from .chrome import Collapsible, DropZone, ScreenHeader
from .templbar import TemplateBar
from .thumbs import ThumbLoader, page_pixmap, placeholder_icon, visible_rows
from .widgets import try_open_pdf, open_folder, PageView, ask_open_pdf, ask_save, big_button, error, info, zoom_bar

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
        self.tpl_names: dict[int, str] = {}
        self.issue_pages = set()
        self._reading = None
        self._saved = False
        self.zones = {"zone": load_rect("sort/zone1", DEFAULT_ZONE), "zone2": load_rect("sort/zone2", None)}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(12)
        self.header = ScreenHeader("Сортировка страниц",
                                   "Введите для страниц номера или текстовые метки. Номера квартир можно получить из текста или распознать на скане.",
                                   ["Откройте PDF", "Проверьте метки", "Сохраните по порядку"])
        lay.addWidget(self.header)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.dz = DropZone("PDF для сортировки", "Перетащите сюда PDF или нажмите, чтобы выбрать")
        self.dz.clicked.connect(self.open)
        self.dz.files.connect(lambda ps: self.open(ps[0]))
        top.addWidget(self.dz, 1)
        self.btn_recognize = big_button("🔢 Распознать номера", self.recognize)
        self.btn_guess = big_button("🔎 Из текста PDF", self.guess, ghost=True)
        self.btn_order = big_button("Упорядочить", self.sort_table, icon_name="arrow-up-down",
                                    tip="Показать страницы в порядке сохранения")
        for b_ in (self.btn_recognize, self.btn_guess):
            top.addWidget(b_)
        lay.addLayout(top)
        controls = QHBoxLayout()
        self.direction = QComboBox()
        self.direction.addItems(["По возрастанию", "По убыванию"])
        self.direction.setAccessibleName("Порядок сортировки PDF и CSV")
        self.direction.setToolTip("Одинаковые номера сохраняют исходный порядок. Пустые остаются в конце.")
        self.direction.currentIndexChanged.connect(self.sort_table)
        controls.addWidget(self.direction)
        controls.addWidget(self.btn_order)
        controls.addStretch()
        self.btn_next = big_button("Следующая для проверки", self.next_issue, icon_name="scan-search",
                                   tip="Перейти к пропущенному, повторному или неуверенно распознанному номеру")
        self.btn_confirm = big_button("Номер верен", self.confirm_number, icon_name="check",
                                      tip="Подтвердить неуверенно распознанный номер выбранной страницы")
        controls.addWidget(self.btn_next)
        controls.addWidget(self.btn_confirm)
        lay.addLayout(controls)
        self.tbar = TemplateBar(self.capture_template)
        self.tbar.applied.connect(self.apply_template)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Страница", "Номер / метка", "Примечание"])
        self.table.setIconSize(QSize(60, 84))
        self.table.verticalHeader().setDefaultSectionSize(90)
        self.table.setColumnWidth(0, 126)
        self.table.setColumnWidth(1, 164)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet("QTableWidget { font-size: 16px; }")
        self.table.itemChanged.connect(self.item_edited)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_page(r))
        self.table.currentCellChanged.connect(lambda *_: self.sync_actions())
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

        self.advanced = Collapsible("Дополнительно: шаблоны бланков")
        self.advanced.add(self.tbar)
        lay.addWidget(self.advanced)
        self.zone_bar = right
        bottom = QHBoxLayout()
        self.stat = QLabel("")
        self.stat.setObjectName("Muted")
        self.stat.setWordWrap(True)
        bottom.addWidget(self.stat, 1)
        self.btn_csv = big_button("📋 Сохранить список (CSV)…", self.save_csv)
        self.btn_save = big_button("💾 Сохранить отсортированный PDF…", self.save, primary=True)
        bottom.addWidget(self.btn_csv)
        bottom.addWidget(self.btn_save)
        lay.addLayout(bottom)
        self.sync_actions()

    # ---------- Ctrl+Z ----------
    def get_state(self):
        if not self.doc:
            return None
        order = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]
        return dict(order=order, values=self.values_by_page(), unsure=set(self.unsure), direction=self.direction.currentIndex(),
                    templates=dict(self.tpl_names))

    def set_state(self, st):
        if not st:
            return
        self.unsure = set(st["unsure"])
        self.tpl_names = dict(st.get("templates", {}))
        with QSignalBlocker(self.direction):
            self.direction.setCurrentIndex(st.get("direction", 0))
        self._fill(st["order"], st["values"])

    # ---------- загрузка ----------
    def open(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if not path:
            return
        doc = try_open_pdf(self, path)
        if doc is None:
            return
        if self.is_dirty():
            box = QMessageBox(QMessageBox.Question, "Открыть другой PDF?",
                              f"Сохранить текущий порядок страниц в «{os.path.basename(self.path)}»?",
                              QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
            box.button(QMessageBox.Save).setText("Сохранить PDF…")
            box.button(QMessageBox.Discard).setText("Пропустить")
            box.button(QMessageBox.Cancel).setText("Отмена")
            box.setDefaultButton(QMessageBox.Save)
            answer = box.exec()
            if answer == QMessageBox.Save:
                self.save()
            if answer != QMessageBox.Discard and (answer != QMessageBox.Save or self.is_dirty()):
                doc.close()
                return
        if self._reading is not None:
            self._reading.cancelled = True
            self._reading = None
        if self.doc is not None:
            self.doc.close()
        self.path, self.doc = path, doc
        self.dz.set_loaded(f"{os.path.basename(path)} · {len(self.doc)} стр.")
        self.unsure = set()
        self._saved = False
        self.tpl_names = {}
        self._icons = {}
        if self.loader:
            self.loader.stop()
        self.loader = ThumbLoader(path, 30)
        self.loader.ready.connect(self.thumb_ready)
        with QSignalBlocker(self.direction):
            self.direction.setCurrentIndex(0)
        self._fill(list(range(len(self.doc))), sorting.guess_all(self.doc), preserve=False)
        self.reset_undo()

    def _fill(self, order, values, preserve=True):
        current = self.table.item(self.table.currentRow(), 0)
        selected = current.data(Qt.UserRole) if preserve and current is not None else order[0]
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
        row = order.index(selected) if selected in order else 0
        self.table.setCurrentCell(row, 1)
        self.table.blockSignals(False)
        self.show_page(row)
        self.refresh_marks()
        QTimer.singleShot(0, self, self.request_thumbs)

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
        if item.column() != 1:
            return
        self.unsure.discard(self.table.item(item.row(), 0).data(Qt.UserRole))
        self.refresh_marks()
        self.commit("метка страницы")

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

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not self.doc:
            return 0
        if self.issue_pages:
            return 1
        return 3 if self._saved and not self.is_dirty() else 2

    def accept_files(self, paths: list[str]):
        self.open(paths[0])

    def tour_steps(self):
        from .tour import Step
        adv = lambda: self.advanced.set_expanded(True)   # noqa: E731
        return [
            Step(None, "Сортировка страниц", "Задайте номера или текстовые метки. Доступны оба направления сортировки. Страницы без метки всегда идут последними."),
            Step(self.dz, "1. Откройте PDF", "Нажмите или перетащите сюда файл со сканами."),
            Step(self.table, "Таблица страниц", "Каждая строка — страница файла. В столбце «Номер / метка» дважды "
                 "щёлкните и впишите номер. <b>Красные</b> — номер повторяется, <b>оранжевые</b> — распознано "
                 "неуверенно, их нужно проверить."),
            Step(self.view, "Страница крупно", "Здесь видна выбранная страница. Зелёная рамка — <b>зона номера</b>: "
                 "место, где на бланке написан номер квартиры. Нарисуйте её мышью вокруг номера."),
            Step(self.r_z2, "Второй вид бланка", "Если в файле бланки двух видов и номер стоит в разных местах — "
                 "отметьте «зона 2» и нарисуйте вторую зону."),
            Step(self.btn_recognize, "Распознать номера", "Программа прочитает рукописные номера в зелёной зоне и "
                 "впишет их в пустые ячейки. Читает неточно — проверьте каждую строку по картинке."),
            Step(self.btn_guess, "Из текста PDF", "Если PDF не скан, а создан на компьютере — номера можно взять "
                 "из текста файла. Для сканов не работает."),
            Step(self.btn_next, "Страницы для проверки", "Перейдите к пропуску, повтору или неуверенному номеру. Сверьте номер со страницей и исправьте его или нажмите «Номер верен»."),
            Step(self.btn_order, "2. Проверьте и упорядочьте", "Расставляет строки по номеру квартиры, чтобы "
                 "было удобно проверять. Внизу видно, сколько страниц без номера и есть ли повторы."),
            Step(self.advanced, "Шаблоны бланков", "Чтобы не рисовать зоны каждый раз, сохраните их как шаблон "
                 "и потом просто выбирайте в списке.", adv),
            Step(self.btn_save, "3. Сохраните", "Новый PDF со страницами по порядку квартир. "
                 "«Сохранить список» — таблица для Excel. Ctrl + Z отменяет последнее действие."),
        ]

    # ---------- шаблоны ----------
    def capture_template(self, name: str):
        from ..core import templates as tp
        zs = [z for z in (self.zones["zone"], self.zones["zone2"]) if z is not None]
        old = tp.get(name)   # рамки подписи из шаблона вкладки «Подписи» сохраняем
        t = tp.Template(name, zones_rec=zs, src_rect=old.src_rect if old else None,
                        dst_rect=old.dst_rect if old else None, zones_act=old.zones_act if old else [],
                        fp_act=old.fp_act if old else None,
                        ink_mode=old.ink_mode if old else "blue", sensitivity=old.sensitivity if old else 0.5,
                        keep_colors=old.keep_colors if old else True)
        if self.doc is not None:
            r = self.table.currentRow()
            p = self.table.item(r, 0).data(Qt.UserRole) if r >= 0 else 0
            t.fp_rec = tp.fingerprint(self.doc, p)
        return t

    def apply_template(self, t):
        if not t.zones_rec:
            return
        self.zones["zone"] = t.zones_rec[0]
        self.zones["zone2"] = t.zones_rec[1] if len(t.zones_rec) > 1 else None
        save_rect("sort/zone1", self.zones["zone"])
        if self.zones["zone2"]:
            save_rect("sort/zone2", self.zones["zone2"])
        else:
            settings().remove("sort/zone2")
        self.view.remove_box("zone")
        self.view.remove_box("zone2")
        self.show_page(self.table.currentRow())

    def clear_zone2(self):
        self.zones["zone2"] = None
        self.view.remove_box("zone2")
        from .infra import settings
        settings().remove("sort/zone2")

    def sync_actions(self):
        for control in (self.btn_recognize, self.btn_guess, self.direction, self.btn_order, self.btn_save, self.btn_csv):
            control.setEnabled(self.doc is not None)
        self.btn_recognize.setEnabled(self.doc is not None and self._reading is None)
        self.btn_next.setEnabled(bool(self.issue_pages))
        item = self.table.item(self.table.currentRow(), 0)
        self.btn_confirm.setEnabled(item is not None and item.data(Qt.UserRole) in self.unsure)

    def confirm_number(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is not None:
            self.unsure.discard(item.data(Qt.UserRole))
            self.refresh_marks()
            self.commit("подтвердить номер")

    def next_issue(self):
        count = self.table.rowCount()
        for offset in range(1, count + 1):
            row = (self.table.currentRow() + offset) % count
            if self.table.item(row, 0).data(Qt.UserRole) in self.issue_pages:
                self.table.setCurrentCell(row, 1)
                self.table.scrollToItem(self.table.currentItem())
                self.table.setFocus()
                return

    def recognize(self):
        if self._reading is not None:
            return
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
        temps = self.tbar.auto_templates()
        det: list = []

        def done(res):
            if self._reading is not t:
                dlg.close()
                return
            cancelled = t.cancelled
            self._reading = None
            dlg.close()
            self.sync_actions()
            vals = self.values_by_page()
            got = 0
            for p, r in zip(todo, res):
                if r.text and not vals[p]:
                    vals[p] = r.text
                    got += 1
                    if not r.ok:
                        self.unsure.add(p)
            for p, d in zip(todo, det):
                self.tpl_names[p] = d.template.name if d.template else ""
            self.set_values(vals)
            prefix = "Распознавание остановлено. " if cancelled else ""
            info(self, prefix + f"Распознано: {got} из {len(todo)} пустых.\nНеуверенных (оранжевые): "
                       f"{len([p for p in todo if p in self.unsure])}.\nРукописные цифры распознаются неточно — "
                       f"проверьте все номера по картинке справа.")

        def fail(msg):
            dlg.close()
            if self._reading is not t:
                return
            self._reading = None
            self.sync_actions()
            error(self, "Не удалось распознать: " + msg)

        t = run_task(lambda prog: numbers.read_document(path, zones, "blue", progress=prog, pages=todo,
                                                           temps=temps, role="rec", detected=det), done, fail,
                     lambda i, n: dlg.setValue(int(100 * i / max(n, 1))))
        self._reading = t
        self.sync_actions()
        dlg.canceled.connect(lambda: setattr(t, "cancelled", True))

    def refresh_marks(self, *_):
        if not self.doc:
            return
        vals = self.values_by_page()
        dup = sorting.duplicates(vals)
        self.issue_pages = set()
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).data(Qt.UserRole)
            v = self.table.item(r, 1).text().strip()
            note, color = "", style.mark("white")
            if not v:
                note, color = "нет номера", style.mark("#fff8e1")
            elif v in dup:
                note, color = "ПОВТОР", style.mark("#ffcdd2")
            elif p in self.unsure:
                note, color = "проверьте (распознано неуверенно)", style.mark("#ffcc80")
            if note:
                self.issue_pages.add(p)
            for c in (1, 2):
                self.table.item(r, c).setBackground(color)
            if self.tpl_names.get(p):
                note = (note + "  " if note else "") + f"[бланк: {self.tpl_names[p]}]"
            self.table.item(r, 2).setText(note)
            self.table.item(r, 1).setToolTip(v)
            self.table.item(r, 2).setToolTip(note)
        self.table.blockSignals(False)
        empty = sum(1 for v in vals if not v)
        self.sync_actions()
        self.stat.setToolTip(", ".join(sorted(dup, key=sorting.apt_key)))
        shown = sorted(dup, key=sorting.apt_key)[:4]
        self.stat.setText(f"Страниц: {len(vals)}   без номера: {empty}   повторы: "
                          f"{', '.join(shown) or 'нет'}" + (f" и ещё {len(dup) - len(shown)}" if len(dup) > len(shown) else ""))

    def sort_table(self):
        if not self.doc:
            return
        vals = self.values_by_page()
        self._fill(sorting.sorted_order(vals, bool(self.direction.currentIndex())), vals)
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
        out = out or ask_save(self, safe_output_path(self.path, "отсортирован"))
        if out:
            try:
                sorting.export_sorted(self.path, self.values_by_page(), out, bool(self.direction.currentIndex()))
                self.mark_saved()
                self._saved = True
                info(self, f"Готово! Страницы сохранены в выбранном порядке: {os.path.basename(out)}",
                     action=("Открыть папку", lambda: open_folder(out)))
            except Exception as e:  # noqa: BLE001
                log.exception("Сортировка")
                error(self, str(e))

    def save_csv(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out = out or ask_save(self, safe_output_path(self.path, "порядок_страниц", ".csv"), "CSV (*.csv)")
        if out:
            try:
                check_output_path(out, (self.path,))
                sorting.export_csv(self.values_by_page(), out, bool(self.direction.currentIndex()))
            except PdfError as e:
                return error(self, str(e))
            except PermissionError:
                return error(self, f"Не удалось записать «{os.path.basename(out)}»: файл открыт в Excel или другой "
                                   "программе (закройте его) или нет прав на запись в эту папку.")
            except OSError as e:
                log.exception("CSV")
                return error(self, f"Не удалось записать «{os.path.basename(out)}»: {e.strerror or e}")
            info(self, f"Список сохранён: {out}\n(открывается в Excel)")
