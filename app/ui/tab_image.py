"""Вкладка «Вставка картинки»."""
from __future__ import annotations

import os

import numpy as np
import pymupdf
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core import imageinsert, pages
from ..core.logsetup import log
from ..core.pdfutil import NRect, safe_output_path
from .infra import UndoMixin, last_dir, remember_dir
from .thumbs import page_pixmap
from .widgets import PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap, zoom_bar

HELP = ("Откройте PDF и картинку (PNG/JPG). Перетащите картинку мышью, размер — синим квадратиком в углу. "
        "Масштаб — Ctrl+колесо. Выберите страницы и нажмите «Сохранить». Ctrl+Z — отменить.")


class ImageTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.doc = None
        self.path = ""
        self.img_path = ""
        self.img = None
        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть PDF…", self.open_pdf))
        top.addWidget(big_button("🖼 Выбрать картинку…", self.open_image))
        self.chk_white = QCheckBox("Убрать белый фон")
        self.chk_white.setStyleSheet("font-size: 15px;")
        self.chk_white.toggled.connect(self.white_toggled)
        top.addWidget(self.chk_white)
        top.addSpacing(20)
        top.addWidget(QLabel("Страница:"))
        self.spin = QSpinBox()
        self.spin.setMinimum(1)
        self.spin.setStyleSheet("font-size: 15px; min-width: 60px;")
        self.spin.valueChanged.connect(self.show_page)
        top.addWidget(self.spin)
        self.lbl = QLabel("")
        top.addWidget(self.lbl)
        top.addStretch()
        lay.addLayout(top)

        self.view = PageView(color="#1565c0")
        self.view.rectChanged.connect(lambda *_: self.commit("положение картинки"))
        lay.addWidget(self.view, 1)
        lay.addLayout(zoom_bar(self.view))

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Куда:"))
        self.r_cur = QRadioButton("только эта страница")
        self.r_cur.setChecked(True)
        self.r_all = QRadioButton("все страницы")
        self.r_sel = QRadioButton("страницы:")
        self.ed_pages = QLineEdit()
        self.ed_pages.setPlaceholderText("например 1-3, 5")
        for w in (self.r_cur, self.r_all, self.r_sel, self.ed_pages):
            bottom.addWidget(w)
        bottom.addStretch()
        bottom.addWidget(big_button("💾 Сохранить новый PDF…", self.save, primary=True))
        lay.addLayout(bottom)
        self.reset_undo()

    # ---------- Ctrl+Z ----------
    def get_state(self):
        return dict(rect=self.view.get_nrect(), img=self.img_path, white=self.chk_white.isChecked())

    def set_state(self, st):
        self.chk_white.blockSignals(True)
        self.chk_white.setChecked(st["white"])
        self.chk_white.blockSignals(False)
        self.img_path = st["img"]
        if self.img_path:
            self.img = imageinsert.load_image(self.img_path, st["white"])
            if st["rect"] is not None and self.doc:
                self.view.set_nrect(st["rect"], np_to_pixmap(np.array(self.img)), emit=False)
        else:
            self.img = None
            self.view.remove_box("main")

    def open_pdf(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if path:
            self.path, self.doc = path, pymupdf.open(path)
            self.spin.setMaximum(len(self.doc))
            self.lbl.setText(f"из {len(self.doc)} — {os.path.basename(path)}")
            self.show_page()
            if self.img_path:
                self.reload_image(initial=self.view.get_nrect() is None)
            self.reset_undo()

    def open_image(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Картинка", last_dir(), "Картинки (*.png *.jpg *.jpeg)")
            remember_dir(path)
        if path:
            self.img_path = path
            self.reload_image(initial=True)
            self.commit("картинка")

    def white_toggled(self, *_):
        self.reload_image()
        self.commit("белый фон")

    def reload_image(self, initial=False):
        if not self.img_path:
            return
        self.img = imageinsert.load_image(self.img_path, self.chk_white.isChecked())
        if not self.doc:
            return
        pm = np_to_pixmap(np.array(self.img))
        nr = None if initial else self.view.get_nrect()
        if nr is None:
            pr = self.doc[self.spin.value() - 1].rect
            w = 0.3
            h = w * pr.width / pr.height * self.img.height / self.img.width
            nr = NRect(0.35, 0.4, 0.35 + w, 0.4 + h)
        self.view.set_nrect(nr, pm, emit=False)

    def show_page(self, *_):
        if self.doc:
            self.view.set_page(page_pixmap(self.doc, self.spin.value() - 1, 90))

    def target_pages(self) -> list[int]:
        n = len(self.doc)
        if self.r_all.isChecked():
            return list(range(n))
        if self.r_sel.isChecked():
            return [i for g in pages.parse_ranges(self.ed_pages.text(), n) for i in g]
        return [self.spin.value() - 1]

    def save(self, out: str = ""):
        if not (self.doc and self.img is not None and self.view.get_nrect()):
            return error(self, "Откройте PDF и выберите картинку.")
        try:
            tp = self.target_pages()
            out = out or ask_save(self, safe_output_path(self.path, "с_картинкой"))
            if out:
                imageinsert.insert_image(self.path, self.img, self.view.get_nrect(), tp, out)
                self.mark_saved()
                info(self, f"Сохранено: {out}\nКартинка поставлена на {len(tp)} стр.")
        except Exception as e:  # noqa: BLE001
            log.exception("Вставка картинки")
            error(self, str(e))
