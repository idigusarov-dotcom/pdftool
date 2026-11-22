"""Вкладка «Страницы»: объединение, разделение, поворот, удаление, порядок."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QPixmap, QTransform

from ..core import pages
from ..core.logsetup import log
from ..core.pdfutil import Cancelled, safe_output_path
from .infra import UndoMixin, last_dir, remember_dir
from .thumbs import ThumbLoader, placeholder_icon, visible_rows
from .chrome import DropZone, ScreenHeader
from .widgets import try_open_pdf, open_folder, ask_open_pdf, ask_save, big_button, error, info, progress_window


class PagesTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.loader = None
        self._pix: dict[int, QPixmap] = {}
        self.doc = None
        self.path = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        self.header = ScreenHeader("Страницы", "Объединить несколько PDF, повернуть, удалить, переставить или "
                                   "разделить страницы. Исходные файлы не меняются.",
                                   ["Откройте файлы", "Измените страницы", "Сохраните"])
        outer.addWidget(self.header)
        lay = QHBoxLayout()
        lay.setSpacing(14)
        outer.addLayout(lay, 1)
        self._saved = False

        # --- объединение ---
        g = QGroupBox("Объединить несколько PDF")
        v = QVBoxLayout(g)
        self.dz_merge = DropZone("Файлы для объединения", "Перетащите сюда несколько PDF или нажмите",
                                 icon_name="files")
        self.dz_merge.clicked.connect(self.add_files)
        self.dz_merge.files.connect(self.add_files)
        v.addWidget(self.dz_merge)
        hint = QLabel("Порядок файлов меняйте перетаскиванием в списке.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.merge_list = QListWidget()
        self.merge_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.merge_list.setStyleSheet("font-size: 14px;")
        self.merge_list.model().rowsMoved.connect(lambda *_: self.commit("порядок файлов"))
        v.addWidget(self.merge_list, 1)
        row = QHBoxLayout()
        row.addWidget(big_button("➕ Добавить…", self.add_files))
        row.addWidget(big_button("➖ Убрать", self.remove_file))
        v.addLayout(row)
        self.btn_merge = big_button("Объединить и сохранить…", self.do_merge, primary=True, icon_name="combine",
                                    tip="Склеить файлы списка в один новый PDF")
        v.addWidget(self.btn_merge)
        self.card_merge = g
        lay.addWidget(g, 1)

        # --- редактор страниц ---
        g2 = QGroupBox("Страницы одного PDF")
        v2 = QVBoxLayout(g2)
        top = QHBoxLayout()
        self.dz = DropZone("PDF для правки", "Перетащите сюда PDF или нажмите")
        self.dz.clicked.connect(self.open)
        self.dz.files.connect(lambda ps: self.open(ps[0]))
        top.addWidget(self.dz, 1)
        v2.addLayout(top)
        hint2 = QLabel("Выделяйте страницы мышью (Ctrl — несколько), перетаскивайте для смены порядка.")
        hint2.setObjectName("Muted")
        hint2.setWordWrap(True)
        v2.addWidget(hint2)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(110, 150))
        self.grid.setGridSize(QSize(130, 185))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Snap)
        self.grid.setDragDropMode(QAbstractItemView.InternalMove)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.model().rowsMoved.connect(lambda *_: self.commit("порядок страниц"))
        self.grid.verticalScrollBar().valueChanged.connect(self.request_thumbs)
        v2.addWidget(self.grid, 1)
        ops = QHBoxLayout()
        self.btn_rot = big_button("Вправо", lambda: self.rotate(90), icon_name="rotate-cw",
                                  tip="Повернуть выделенные страницы по часовой стрелке")
        ops.addWidget(self.btn_rot)
        ops.addWidget(big_button("Влево", lambda: self.rotate(-90), icon_name="rotate-ccw",
                                  tip="Повернуть выделенные страницы против часовой стрелки"))
        self.btn_del = big_button("Удалить", self.delete, icon_name="trash-2",
                                  tip="Удалить выделенные страницы (в новом файле)")
        ops.addWidget(self.btn_del)
        ops.addStretch()
        self.btn_save = big_button("Сохранить PDF…", self.save, primary=True, icon_name="save",
                                   tip="Сохранить изменённые страницы в новый файл. Исходный не меняется")
        ops.addWidget(self.btn_save)
        v2.addLayout(ops)
        sp = QHBoxLayout()
        sp.addWidget(QLabel("Разделить:"))
        self.ed_ranges = QLineEdit()
        self.ed_ranges.setPlaceholderText("например 1-3, 4-6, 7")
        sp.addWidget(self.ed_ranges)
        self.ed_ranges.setToolTip("Номера страниц для каждой части через запятую")
        self.btn_split = big_button("✂ По диапазонам…", self.split_ranges)
        sp.addWidget(self.btn_split)
        sp.addWidget(big_button("Каждая отдельно…", self.split_every, icon_name="scissors",
                              tip="Сохранить каждую страницу отдельным PDF"))
        v2.addLayout(sp)
        lay.addWidget(g2, 2)
        self.reset_undo()

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not (self.doc or self.merge_list.count()):
            return 0
        if not self.undo_stack.count() and not self._saved:
            return 1
        return 3 if self._saved and not self.is_dirty() else 2

    def accept_files(self, paths: list[str]):
        if len(paths) > 1:
            self.add_files(paths)
        else:
            self.open(paths[0])

    def tour_steps(self):
        from .tour import Step
        return [
            Step(None, "Работа со страницами", "Слева — объединение нескольких PDF в один. Справа — правка "
                 "страниц одного файла. Нажимайте «Далее»."),
            Step(self.dz_merge, "Объединить файлы", "Перетащите сюда несколько PDF (или нажмите и выберите)."),
            Step(self.merge_list, "Порядок файлов", "Файлы склеятся сверху вниз. Порядок меняйте перетаскиванием."),
            Step(self.btn_merge, "Сохранить объединённый", "Создаёт один новый PDF из всех файлов списка."),
            Step(self.dz, "Правка одного PDF", "Откройте файл, страницы которого нужно изменить."),
            Step(self.grid, "Страницы", "Щёлкните страницу, чтобы выделить (Ctrl — несколько). "
                 "Перетащите, чтобы поменять порядок."),
            Step(self.btn_rot, "Повернуть и удалить", "Кнопки работают с выделенными страницами."),
            Step(self.ed_ranges, "Разделить файл", "Впишите части, например «1-3, 4-6», и нажмите «По диапазонам». "
                 "Или «Каждая отдельно» — каждая страница станет отдельным файлом."),
            Step(self.btn_save, "Сохраните", "Новый PDF со всеми изменениями. Исходный файл не меняется. "
                 "Ctrl + Z отменяет последнее действие."),
        ]

    # --- объединение ---
    def add_files(self, paths=None):
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "PDF для объединения", last_dir(), "PDF (*.pdf)")
            if paths:
                remember_dir(paths[0])
        added = 0
        for p in paths or []:
            doc = try_open_pdf(self, p)      # пароль, битый, пустой — сразу понятное сообщение
            if doc is None:
                continue
            doc.close()
            self._add_merge_item(p)
            added += 1
        if added:
            self.commit("добавить файлы")

    def _add_merge_item(self, p):
        it = QListWidgetItem(os.path.basename(p))
        it.setData(Qt.UserRole, p)
        it.setToolTip(p)
        self.merge_list.addItem(it)

    def remove_file(self):
        for it in self.merge_list.selectedItems():
            self.merge_list.takeItem(self.merge_list.row(it))
        self.commit("убрать файл")

    # ---------- Ctrl+Z ----------
    def get_state(self):
        merge = [self.merge_list.item(i).data(Qt.UserRole) for i in range(self.merge_list.count())]
        pg = [(self.grid.item(i).data(Qt.UserRole), self.grid.item(i).data(Qt.UserRole + 1))
              for i in range(self.grid.count())]
        return dict(merge=merge, pages=pg)

    def set_state(self, st):
        self.merge_list.clear()
        for p in st["merge"]:
            self._add_merge_item(p)
        self.grid.clear()
        for i, rot in st["pages"]:
            self._add_page_item(i, rot)
        QTimer.singleShot(0, self.request_thumbs)

    def _icon(self, i, rot):
        pm = self._pix.get(i)
        if pm is None:
            return placeholder_icon(110, 150)
        return QIcon(pm.transformed(QTransform().rotate(rot)) if rot else pm)

    def _add_page_item(self, i, rot=0):
        it = QListWidgetItem(self._icon(i, rot), f"стр. {i + 1}")
        it.setData(Qt.UserRole, i)
        it.setData(Qt.UserRole + 1, rot)
        self.grid.addItem(it)

    def request_thumbs(self, *_):
        if self.loader and self.grid.count():
            self.loader.request([self.grid.item(r).data(Qt.UserRole) for r in visible_rows(self.grid)])

    def thumb_ready(self, path, i, img):
        if path != self.path:
            return
        self._pix[i] = QPixmap.fromImage(img)
        for r in range(self.grid.count()):
            it = self.grid.item(r)
            if it.data(Qt.UserRole) == i:
                it.setIcon(self._icon(i, it.data(Qt.UserRole + 1)))

    def do_merge(self, out: str = ""):
        paths = [self.merge_list.item(i).data(Qt.UserRole) for i in range(self.merge_list.count())]
        if len(paths) < 2:
            return error(self, "Добавьте хотя бы два файла.")
        out = out or ask_save(self, safe_output_path(paths[0], "объединённый"))
        if out:
            try:
                with progress_window(self, "Объединяю файлы…") as prog:
                    n = prog.run(pages.merge, paths, out)
                self.mark_saved()
                self._saved = True
                info(self, f"Готово! Файлы объединены: {os.path.basename(out)}, страниц: {n}",
                     action=("Открыть папку", lambda: open_folder(out)))
            except Cancelled:
                info(self, "Отменено — новый файл не создан.")
            except Exception as e:  # noqa: BLE001
                log.exception("Страницы")
                error(self, str(e))

    # --- страницы ---
    def open(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if not path:
            return
        doc = try_open_pdf(self, path)
        if doc is None:
            return
        self.path, self.doc = path, doc
        self.dz.set_loaded(f"{os.path.basename(path)} · {len(self.doc)} стр.")
        self.grid.clear()
        self._pix = {}
        if self.loader:
            self.loader.stop()
        self.loader = ThumbLoader(path, 25)
        self.loader.ready.connect(self.thumb_ready)
        for i in range(len(self.doc)):
            self._add_page_item(i)
        QTimer.singleShot(0, self.request_thumbs)
        self.reset_undo()

    def rotate(self, deg):
        for it in self.grid.selectedItems():
            rot = (it.data(Qt.UserRole + 1) + deg) % 360
            it.setData(Qt.UserRole + 1, rot)
            it.setIcon(self._icon(it.data(Qt.UserRole), rot))
        self.commit("поворот")

    def delete(self):
        for it in self.grid.selectedItems():
            self.grid.takeItem(self.grid.row(it))
        self.commit("удаление")

    def state(self):
        items = [self.grid.item(i) for i in range(self.grid.count())]
        return [it.data(Qt.UserRole) for it in items], {it.data(Qt.UserRole): it.data(Qt.UserRole + 1) for it in items}

    def save(self, out: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        order, rot = self.state()
        if not order:
            return error(self, "Не осталось ни одной страницы.")
        out = out or ask_save(self, safe_output_path(self.path, "изменён"))
        if out:
            try:
                with progress_window(self, "Сохраняю страницы…") as prog:
                    n = prog.run(pages.rebuild, self.path, order, rot, out)
                self.mark_saved()
                self._saved = True
                info(self, f"Готово! Сохранено страниц: {n}. Файл: {os.path.basename(out)}",
                     action=("Открыть папку", lambda: open_folder(out)))
            except Cancelled:
                info(self, "Отменено — новый файл не создан.")
            except Exception as e:  # noqa: BLE001
                log.exception("Страницы")
                error(self, str(e))

    def _out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для файлов", os.path.dirname(self.path))
        remember_dir(d)
        return d

    def split_ranges(self, out_dir: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        try:
            groups = pages.parse_ranges(self.ed_ranges.text(), len(self.doc))
        except ValueError as e:
            return error(self, str(e))
        out_dir = out_dir or self._out_dir()
        if out_dir:
            try:
                with progress_window(self, "Разделяю файл…") as prog:
                    outs = prog.run(pages.split, self.path, groups, out_dir)
            except Cancelled:
                return info(self, "Остановлено. Уже созданные части остались в папке.")
            except Exception as e:  # noqa: BLE001
                log.exception("Разделение")
                return error(self, str(e))
            self._saved = True
            info(self, f"Готово! Создано файлов: {len(outs)}", action=("Открыть папку", lambda: open_folder(out_dir)))

    def split_every(self, out_dir: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out_dir = out_dir or self._out_dir()
        if out_dir:
            try:
                with progress_window(self, "Сохраняю каждую страницу отдельно…") as prog:
                    outs = prog.run(pages.split_every_page, self.path, out_dir)
            except Cancelled:
                return info(self, "Остановлено. Уже созданные файлы остались в папке.")
            except Exception as e:  # noqa: BLE001
                log.exception("Разделение")
                return error(self, str(e))
            self._saved = True
            info(self, f"Готово! Создано файлов: {len(outs)}", action=("Открыть папку", lambda: open_folder(out_dir)))
