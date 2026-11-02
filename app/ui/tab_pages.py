"""Вкладка «Страницы»: объединение, разделение, поворот, удаление, порядок."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QPixmap, QTransform

from ..core import pages
from ..core.logsetup import log
from ..core.pdfutil import safe_output_path
from .infra import UndoMixin, last_dir, remember_dir
from .thumbs import ThumbLoader, placeholder_icon, visible_rows
from .widgets import ask_open_pdf, ask_save, big_button, error, info


class PagesTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.loader = None
        self._pix: dict[int, QPixmap] = {}
        self.doc = None
        self.path = ""
        lay = QHBoxLayout(self)

        # --- объединение ---
        g = QGroupBox("Объединить несколько PDF")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("Добавьте файлы, порядок меняйте перетаскиванием."))
        self.merge_list = QListWidget()
        self.merge_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.merge_list.setStyleSheet("font-size: 14px;")
        self.merge_list.model().rowsMoved.connect(lambda *_: self.commit("порядок файлов"))
        v.addWidget(self.merge_list, 1)
        row = QHBoxLayout()
        row.addWidget(big_button("➕ Добавить…", self.add_files))
        row.addWidget(big_button("➖ Убрать", self.remove_file))
        v.addLayout(row)
        v.addWidget(big_button("💾 Объединить и сохранить…", self.do_merge, primary=True))
        lay.addWidget(g, 1)

        # --- редактор страниц ---
        g2 = QGroupBox("Страницы одного PDF")
        v2 = QVBoxLayout(g2)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть PDF…", self.open))
        self.lbl = QLabel("файл не выбран")
        top.addWidget(self.lbl)
        top.addStretch()
        v2.addLayout(top)
        v2.addWidget(QLabel("Выделяйте страницы мышью (Ctrl — несколько), перетаскивайте для смены порядка."))
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
        ops.addWidget(big_button("⟳ Повернуть вправо", lambda: self.rotate(90)))
        ops.addWidget(big_button("⟲ Повернуть влево", lambda: self.rotate(-90)))
        ops.addWidget(big_button("🗑 Удалить выбранные", self.delete))
        ops.addStretch()
        ops.addWidget(big_button("💾 Сохранить как новый PDF…", self.save, primary=True))
        v2.addLayout(ops)
        sp = QHBoxLayout()
        sp.addWidget(QLabel("Разделить:"))
        self.ed_ranges = QLineEdit()
        self.ed_ranges.setPlaceholderText("например 1-3, 4-6, 7")
        sp.addWidget(self.ed_ranges)
        sp.addWidget(big_button("✂ По диапазонам…", self.split_ranges))
        sp.addWidget(big_button("✂ Каждая страница отдельно…", self.split_every))
        v2.addLayout(sp)
        lay.addWidget(g2, 2)
        self.reset_undo()

    # --- объединение ---
    def add_files(self, paths=None):
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "PDF для объединения", last_dir(), "PDF (*.pdf)")
            if paths:
                remember_dir(paths[0])
        for p in paths or []:
            self._add_merge_item(p)
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
                n = pages.merge(paths, out)
                self.mark_saved()
                info(self, f"Сохранено: {out}\nВсего страниц: {n}")
            except Exception as e:  # noqa: BLE001
                log.exception("Страницы")
                error(self, str(e))

    # --- страницы ---
    def open(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if not path:
            return
        self.path, self.doc = path, pymupdf.open(path)
        self.lbl.setText(f"{os.path.basename(path)} ({len(self.doc)} стр.)")
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
                n = pages.rebuild(self.path, order, rot, out)
                self.mark_saved()
                info(self, f"Сохранено: {out}\nСтраниц: {n}")
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
            outs = pages.split(self.path, groups, out_dir)
            info(self, f"Создано файлов: {len(outs)} в папке {out_dir}")

    def split_every(self, out_dir: str = ""):
        if not self.doc:
            return error(self, "Сначала откройте PDF.")
        out_dir = out_dir or self._out_dir()
        if out_dir:
            outs = pages.split_every_page(self.path, out_dir)
            info(self, f"Создано файлов: {len(outs)} в папке {out_dir}")
