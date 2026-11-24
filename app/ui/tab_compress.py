"""Экран «Сжатие»: уменьшить PDF со сканами, чтобы отправить по почте. Исходный файл не меняется."""
from __future__ import annotations

import os
import time

from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget

from ..core import compress as cz
from ..core.logsetup import log
from ..core.pdfutil import Cancelled, safe_output_path
from .chrome import DropZone, ScreenHeader
from .infra import UndoMixin, settings
from .widgets import ask_open_pdf, ask_save, big_button, card, error, info, open_folder, progress_window, \
    try_open_pdf

MAIL_LIMIT = 25 * 1024 * 1024     # почта обычно принимает вложения до 25 МБ


class CompressTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.path = ""
        self.pages = 0
        self.last_out = ""
        self._saved = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        self.header = ScreenHeader("Сжатие", "Уменьшает PDF со сканами, чтобы его можно было отправить по почте. "
                                   "Сохраняется новый файл, исходный не меняется.",
                                   ["Откройте PDF", "Выберите степень", "Сохраните"])
        outer.addWidget(self.header)

        self.dz = DropZone("PDF для сжатия", "Перетащите сюда PDF или нажмите", icon_name="file-down")
        self.dz.clicked.connect(self.open)
        self.dz.files.connect(lambda ps: self.open(ps[0]))
        outer.addWidget(self.dz)
        self.lbl_file = QLabel("")
        self.lbl_file.setObjectName("Muted")
        self.lbl_file.setWordWrap(True)
        outer.addWidget(self.lbl_file)

        g, gv = card("Степень сжатия", "Чем сильнее сжатие, тем меньше файл и мягче картинка. "
                 "Для отправки по почте обычно хватает «Среднего».")
        self.level_group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        for key in ("strong", "medium", "light"):
            lv = cz.LEVELS[key]
            row = QVBoxLayout()
            row.setSpacing(0)
            rb = QRadioButton(lv.title)
            rb.setProperty("level", key)
            rb.setStyleSheet("font-weight: 600;")
            self.level_group.addButton(rb)
            self.radios[key] = rb
            hint = QLabel(lv.hint)
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            hint.setContentsMargins(26, 0, 0, 4)
            row.addWidget(rb)
            row.addWidget(hint)
            gv.addLayout(row)
        lvl = str(settings().value("compress/level", cz.DEFAULT_LEVEL))
        self.radios.get(lvl, self.radios[cz.DEFAULT_LEVEL]).setChecked(True)
        self.level_group.buttonToggled.connect(self._level_changed)
        self.card_level = g
        outer.addWidget(g)

        self.btn_save = big_button("Сжать и сохранить…", self.save, primary=True, icon_name="file-down",
                                   tip="Сохранить уменьшенную копию PDF рядом с исходным")
        outer.addWidget(self.btn_save)

        # итог: было → стало
        self.result = QFrame()
        self.result.setObjectName("Card")
        rv = QVBoxLayout(self.result)
        rv.setContentsMargins(16, 12, 16, 12)
        self.lbl_sizes = QLabel("")
        self.lbl_sizes.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.lbl_sizes.setWordWrap(True)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setObjectName("Muted")
        self.lbl_detail.setWordWrap(True)
        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("WarnBanner")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.hide()
        rv.addWidget(self.lbl_sizes)
        rv.addWidget(self.lbl_detail)
        rv.addWidget(self.lbl_warn)
        rr = QHBoxLayout()
        self.btn_folder = big_button("Открыть папку", lambda: open_folder(self.last_out), icon_name="folder-open")
        rr.addWidget(self.btn_folder)
        rr.addStretch()
        rv.addLayout(rr)
        self.result.hide()
        outer.addWidget(self.result)
        outer.addStretch(1)
        self.reset_undo()

    # ---------- уровень ----------
    def level(self) -> str:
        b = self.level_group.checkedButton()
        return b.property("level") if b else cz.DEFAULT_LEVEL

    def _level_changed(self, btn, on):
        if on:
            settings().setValue("compress/level", self.level())
            self.commit("степень сжатия")

    def get_state(self):
        return dict(level=self.level())

    def set_state(self, st):
        self.radios[st["level"]].setChecked(True)

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not self.path:
            return 0
        return 3 if self._saved else 1

    def accept_files(self, paths: list[str]):
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        if pdfs:
            self.open(pdfs[0])

    def tour_steps(self):
        from .tour import Step
        return [
            Step(None, "Сжатие PDF", "Делает файл со сканами меньше, чтобы он прошёл по почте. "
                 "Исходный файл остаётся как был. Нажимайте «Далее»."),
            Step(self.dz, "Откройте PDF", "Перетащите сюда файл или нажмите и выберите."),
            Step(self.card_level, "Степень сжатия", "«Сильное» — самый маленький файл, мелкий текст может "
                 "стать мягче. «Среднее» подходит почти всегда. «Лёгкое» — почти без потери качества."),
            Step(self.btn_save, "Сохраните", "Программа сохранит новый файл и покажет, сколько он весил и "
                 "сколько стал весить."),
        ]

    # ---------- действия ----------
    def open(self, path: str = ""):
        path = path or ask_open_pdf(self, "PDF для сжатия")
        if not path:
            return
        doc = try_open_pdf(self, path)
        if doc is None:
            return
        try:
            self.pages = doc.page_count
            n_img = len({x[0] for p in doc for x in p.get_images(full=True)})
        finally:
            doc.close()
        self.path = path
        self._saved = False
        self.result.hide()
        self.dz.set_loaded(os.path.basename(path))
        size = cz.human_size(os.path.getsize(path))
        self.lbl_file.setText(f"Сейчас: {size} · страниц: {self.pages} · картинок (сканов): {n_img}"
                              + ("" if n_img else " — сжимать нечего, в файле только текст и линии."))

    def save(self, out: str = ""):
        if not self.path:
            return error(self, "Сначала откройте PDF, который нужно сжать.")
        out = out or ask_save(self, safe_output_path(self.path, "сжатый"))
        if not out:
            return
        level = self.level()
        t0 = time.monotonic()
        try:
            with progress_window(self, "Сжимаю картинки…") as prog:
                rep = prog.run(cz.compress, self.path, out, level, only_if_smaller=True)
        except Cancelled:
            return info(self, "Отменено — новый файл не создан.")
        except Exception as e:  # noqa: BLE001
            log.exception("Сжатие PDF")
            return error(self, str(e))
        sec = time.monotonic() - t0
        log.info("Сжатие %s (%s): %d → %d за %.1f с", self.path, level, rep["before"], rep["after"], sec)
        self.show_result(rep, out)

    def show_result(self, rep: dict, out: str):
        before, after = rep["before"], rep["after"]
        self.result.show()
        self.lbl_warn.hide()
        if not rep["saved"]:
            self._saved = False
            self.last_out = self.path
            self.lbl_sizes.setText("Меньше не стало")
            self.lbl_detail.setText(f"Файл {cz.human_size(before)} уже хорошо сжат для этой степени. "
                                    "Новый файл не сохранён. Попробуйте степень сильнее.")
            info(self, "Меньше не стало — новый файл не создан.")
            return
        self.last_out = out
        self._saved = True
        pct = round(100 * (1 - after / before))
        self.lbl_sizes.setText(f"{cz.human_size(before)}  →  {cz.human_size(after)}   (−{pct}%)")
        self.lbl_detail.setText(f"Сохранён файл {os.path.basename(out)}. Сжато картинок: {rep['changed']} из "
                                f"{rep['images']}. Исходный файл не изменён.")
        if after > MAIL_LIMIT:
            self.lbl_warn.setText("Файл всё ещё больше 25 МБ — почта может его не принять. Выберите "
                                  "«Сильное» или разделите файл на части на экране «Страницы».")
            self.lbl_warn.show()
        info(self, f"Готово: {cz.human_size(before)} → {cz.human_size(after)} (−{pct}%).",
             action=("Открыть папку", lambda: open_folder(out)))
