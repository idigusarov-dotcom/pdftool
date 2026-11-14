"""Вкладка «Вставка картинки»."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core import imageinsert, pages
from ..core.logsetup import log
from ..core.pdfutil import NRect, PdfError, safe_output_path
from .infra import UndoMixin, last_dir, remember_dir
from .thumbs import page_pixmap
from .chrome import DropZone, ScreenHeader
from .widgets import try_open_pdf, open_folder, PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap, zoom_bar

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
        self._saved = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(12)
        self.header = ScreenHeader("Вставка картинки",
                                   "Печать, подпись или логотип ставится на нужные страницы PDF.",
                                   ["Откройте PDF и картинку", "Поставьте картинку на место", "Сохраните новый PDF"])
        lay.addWidget(self.header)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.dz_pdf = DropZone("PDF", "Перетащите сюда PDF или нажмите")
        self.dz_pdf.clicked.connect(self.open_pdf)
        self.dz_pdf.files.connect(lambda ps: self.open_pdf(ps[0]))
        self.dz_img = DropZone("Картинка", "Перетащите PNG/JPG (печать, подпись) или нажмите",
                               exts=(".png", ".jpg", ".jpeg"), icon_name="image")
        self.dz_img.clicked.connect(self.open_image)
        self.dz_img.files.connect(lambda ps: self.open_image(ps[0]))
        top.addWidget(self.dz_pdf, 1)
        top.addWidget(self.dz_img, 1)
        lay.addLayout(top)
        opts = QHBoxLayout()
        self.chk_white = QCheckBox("Убрать белый фон")
        self.chk_white.setToolTip("Сделать белый фон картинки прозрачным — как настоящая печать на бумаге")
        self.chk_white.toggled.connect(self.white_toggled)
        opts.addWidget(self.chk_white)
        opts.addSpacing(20)
        opts.addWidget(QLabel("Страница:"))
        self.spin = QSpinBox()
        self.spin.setMinimum(1)
        self.spin.setToolTip("Какую страницу показать")
        self.spin.valueChanged.connect(self.show_page)
        opts.addWidget(self.spin)
        self.lbl = QLabel("")
        self.lbl.setObjectName("Muted")
        opts.addWidget(self.lbl)
        opts.addStretch()
        lay.addLayout(opts)

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
        self.r_cur.setToolTip("Картинка встанет только на показанную страницу")
        self.r_all.setToolTip("Картинка встанет на каждую страницу файла")
        self.r_sel.setToolTip("Картинка встанет на перечисленные страницы")
        self.where = bottom
        self.btn_save = big_button("💾 Сохранить новый PDF…", self.save, primary=True)
        bottom.addWidget(self.btn_save)
        lay.addLayout(bottom)
        self.reset_undo()

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not (self.doc and self.img_path):
            return 0
        if not self.undo_stack.count() and not self._saved:
            return 1
        return 3 if self._saved and not self.is_dirty() else 2

    def accept_files(self, paths: list[str]):
        for p in paths:
            if p.lower().endswith(".pdf"):
                self.open_pdf(p)
            else:
                self.open_image(p)

    def tour_steps(self):
        from .tour import Step
        return [
            Step(None, "Вставка картинки", "Здесь можно поставить печать, подпись или логотип на страницы PDF. "
                 "Нажимайте «Далее»."),
            Step(self.dz_pdf, "1. Откройте PDF", "Нажмите или перетащите сюда документ."),
            Step(self.dz_img, "…и картинку", "PNG или JPG. Лучше всего — скан печати на белом фоне."),
            Step(self.chk_white, "Убрать белый фон", "Отметьте, чтобы белый фон картинки стал прозрачным и не "
                 "закрывал текст документа."),
            Step(self.view, "2. Поставьте на место", "Перетащите картинку мышью. Размер — за синий квадратик "
                 "в углу. Ctrl + колесо мыши — масштаб страницы."),
            Step(self.spin, "Другая страница", "Выберите, какую страницу показать."),
            Step(self.r_cur, "Куда ставить", "Только на эту страницу, на все страницы или на перечисленные "
                 "(например «1-3, 5»)."),
            Step(self.btn_save, "3. Сохраните", "Создаётся новый PDF, исходный не меняется. "
                 "Ctrl + Z отменяет последнее действие."),
        ]

    # ---------- Ctrl+Z ----------
    def get_state(self):
        return dict(rect=self.view.get_nrect(), img=self.img_path, white=self.chk_white.isChecked())

    def set_state(self, st):
        self.chk_white.blockSignals(True)
        self.chk_white.setChecked(st["white"])
        self.chk_white.blockSignals(False)
        self.img_path = st["img"]
        if self.img_path:
            try:
                self.img = imageinsert.load_image(self.img_path, st["white"])
            except PdfError as e:
                return error(self, str(e))
            if st["rect"] is not None and self.doc:
                self.view.set_nrect(st["rect"], np_to_pixmap(np.array(self.img)), emit=False)
        else:
            self.img = None
            self.view.remove_box("main")

    def open_pdf(self, path: str = ""):
        path = path or ask_open_pdf(self)
        if path:
            doc = try_open_pdf(self, path)
            if doc is None:
                return
            self.path, self.doc = path, doc
            self.spin.setMaximum(len(self.doc))
            self.lbl.setText(f"из {len(self.doc)}")
            self.dz_pdf.set_loaded(f"{os.path.basename(path)} · {len(self.doc)} стр.")
            self.show_page()
            if self.img_path:
                self.reload_image(initial=self.view.get_nrect() is None)
            self.reset_undo()

    def open_image(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Картинка", last_dir(), "Картинки (*.png *.jpg *.jpeg)")
            remember_dir(path)
        if path:
            try:
                imageinsert.load_image(path, False)   # проверка до того, как менять экран
            except PdfError as e:
                log.warning("Картинка %s: %s", path, e)
                return error(self, str(e))
            self.img_path = path
            self.dz_img.set_loaded(os.path.basename(path))
            self.reload_image(initial=True)
            self.commit("картинка")

    def white_toggled(self, *_):
        self.reload_image()
        self.commit("белый фон")

    def reload_image(self, initial=False):
        if not self.img_path:
            return
        try:
            self.img = imageinsert.load_image(self.img_path, self.chk_white.isChecked())
        except PdfError as e:   # картинку удалили/переместили, пока программа открыта
            return error(self, str(e))
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
                self._saved = True
                info(self, f"Готово! Картинка поставлена на {len(tp)} стр. Файл: {os.path.basename(out)}",
                     action=("Открыть папку", lambda: open_folder(out)))
        except Exception as e:  # noqa: BLE001
            log.exception("Вставка картинки")
            error(self, str(e))
