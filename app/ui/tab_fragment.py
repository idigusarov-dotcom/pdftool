"""Экран «Вырезать и перенести»: любой фрагмент страницы PDF A (подпись, печать, надпись) — на страницу PDF B."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..core import fragment as fr
from ..core.ink import FRAGMENT_MODES
from ..core.logsetup import log
from ..core.pdfutil import NRect, render_page, safe_output_path
from .chrome import DropZone, ScreenHeader
from .infra import UndoMixin, settings
from .thumbs import page_pixmap
from .widgets import (PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap, open_folder,
                      try_open_pdf, zoom_bar)

PREVIEW_DPI = 150
VIEW_DPI = 90


class FragmentTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.src = self.dst = None
        self.src_path = self.dst_path = ""
        self.placed: list[fr.Placement] = []
        self.cur_rgba: np.ndarray | None = None
        self.cur_frac = (0.0, 0.0, 1.0, 1.0)
        self._loading = False
        self._saved = False
        s = settings()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(12)
        self.header = ScreenHeader(
            "Вырезать и перенести",
            "Подпись, печать, дата, любая надпись от руки — из одного PDF в любое место другого, без белого фона.",
            ["Откройте оба PDF", "Выделите и поставьте", "Сохраните новый PDF"])
        lay.addWidget(self.header)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.dz_src = DropZone("Откуда", "Перетащите PDF, из которого вырезать (или нажмите)")
        self.dz_src.clicked.connect(self.open_src)
        self.dz_src.files.connect(lambda ps: self.open_src(ps[0]))
        self.dz_dst = DropZone("Куда", "Перетащите PDF, в который вставить (или нажмите)")
        self.dz_dst.clicked.connect(self.open_dst)
        self.dz_dst.files.connect(lambda ps: self.open_dst(ps[0]))
        top.addWidget(self.dz_src, 1)
        top.addWidget(self.dz_dst, 1)
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        # --- откуда ---
        g1 = QGroupBox("Откуда — обведите фрагмент мышью (красная рамка)")
        v1 = QVBoxLayout(g1)
        self.view_src = PageView(color="#e53935")
        self.view_src.rectChanged.connect(self.src_rect_changed)
        v1.addWidget(self.view_src, 1)
        zb = zoom_bar(self.view_src)
        zb.addWidget(QLabel("Страница:"))
        self.sp_src = QSpinBox()
        self.sp_src.setMinimum(1)
        self.sp_src.setToolTip("Страница, с которой вырезать")
        self.sp_src.valueChanged.connect(self.show_src)
        zb.addWidget(self.sp_src)
        v1.addLayout(zb)
        self.card_src = g1
        split.addWidget(g1)
        # --- настройки ---
        mid = QFrame()
        mid.setObjectName("Card")
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(14, 12, 14, 12)
        mv.setSpacing(6)
        mv.addWidget(QLabel("Что вырезать:"))
        self.cb_mode = QComboBox()
        for k, v in FRAGMENT_MODES.items():
            self.cb_mode.addItem(v, k)
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(s.value("frag/mode", "blue"))))
        self.cb_mode.setToolTip("Синие чернила — подписи и надписи синей ручкой.\n"
                                "Чёрные — чёрная ручка, карандаш (линии бланка убираются).\n"
                                "Цветная печать — синие/фиолетовые/красные штампы (чёрный текст не берётся).\n"
                                "Всё, кроме белого — и печать, и текст под ней.\n"
                                "Без удаления фона — прямоугольник как есть, вместе с бумагой.")
        self.cb_mode.currentIndexChanged.connect(self.settings_changed)
        mv.addWidget(self.cb_mode)
        mv.addWidget(QLabel("Чувствительность:"))
        self.sl_sens = QSlider(Qt.Horizontal)
        self.sl_sens.setRange(0, 100)
        self.sl_sens.setValue(int(s.value("frag/sens", 50)))
        self.sl_sens.setToolTip("Больше — берёт бледные штрихи; меньше — меньше лишних точек")
        self.sl_sens.valueChanged.connect(self.settings_changed)
        mv.addWidget(self.sl_sens)
        self.chk_keep = QCheckBox("Сохранить настоящий цвет")
        self.chk_keep.setChecked(str(s.value("frag/keep", "true")) == "true")
        self.chk_keep.setToolTip("Для печатей и цветных надписей — оставить их цвет.\n"
                                 "Если снять — всё станет одного цвета «синей ручки».")
        self.chk_keep.toggled.connect(self.settings_changed)
        mv.addWidget(self.chk_keep)
        mv.addWidget(QLabel("Так вырезалось:"))
        self.lbl_prev = QLabel()
        self.lbl_prev.setObjectName("InkPreview")
        self.lbl_prev.setMinimumSize(200, 90)
        self.lbl_prev.setAlignment(Qt.AlignCenter)
        self.lbl_prev.setToolTip("Вырезанный фрагмент на клетчатом фоне: клетки — это прозрачность")
        mv.addWidget(self.lbl_prev)
        self.btn_place = big_button("Поставить и взять ещё", self.place_current, icon_name="plus",
                                    tip="Закрепить этот фрагмент на месте и вырезать следующий")
        mv.addWidget(self.btn_place)
        rowl = QHBoxLayout()
        rowl.addWidget(QLabel("Уже поставлено:"))
        rowl.addStretch()
        mv.addLayout(rowl)
        self.lst = QListWidget()
        self.lst.setToolTip("Закреплённые фрагменты. Выделите и нажмите «Убрать», чтобы удалить")
        self.lst.setMaximumHeight(90)
        mv.addWidget(self.lst, 1)
        self.btn_remove = big_button("", self.remove_placed, icon_name="x", ghost=True,
                                     tip="Убрать выделенный фрагмент из списка")
        rowl.addWidget(self.btn_remove)
        mid.setMinimumWidth(240)
        mid.setMaximumWidth(300)
        self.card_mid = mid
        split.addWidget(mid)
        # --- куда ---
        g2 = QGroupBox("Куда — перетащите фрагмент мышью (или щёлкните место)")
        v2 = QVBoxLayout(g2)
        self.view_dst = PageView(color="#1565c0")
        self.view_dst.rectChanged.connect(self.dst_rect_changed)
        v2.addWidget(self.view_dst, 1)
        zb2 = zoom_bar(self.view_dst)
        zb2.addWidget(QLabel("Страница:"))
        self.sp_dst = QSpinBox()
        self.sp_dst.setMinimum(1)
        self.sp_dst.setToolTip("Страница, на которую поставить фрагмент")
        self.sp_dst.valueChanged.connect(self.show_dst)
        zb2.addWidget(self.sp_dst)
        self.btn_natural = big_button("1:1", self.natural_size, icon_name="maximize", ghost=True,
                                      tip="Вернуть натуральный размер (как на исходной странице)")
        zb2.addWidget(self.btn_natural)
        v2.addLayout(zb2)
        self.card_dst = g2
        split.addWidget(g2)
        split.setSizes([420, 260, 420])
        lay.addWidget(split, 1)

        bottom = QHBoxLayout()
        hint = QLabel("Размер — за синий квадратик в углу. Масштаб страницы — Ctrl + колесо мыши.")
        hint.setObjectName("Muted")
        bottom.addWidget(hint)
        bottom.addStretch()
        self.btn_save = big_button("Сохранить новый PDF…", self.save, primary=True, icon_name="save",
                                   tip="Сохранить PDF «Куда» со всеми поставленными фрагментами. Исходники не меняются")
        bottom.addWidget(self.btn_save)
        lay.addLayout(bottom)
        self._timer = QTimer(self, singleShot=True, interval=200, timeout=self.recut)
        self.reset_undo()

    # ---------- состояние ----------
    def _pl_tuple(self, p: fr.Placement):
        return (p.src_path, p.src_page, p.src_rect, p.dst_page, p.dst_rect, p.mode, p.sensitivity, p.keep_colors)

    def get_state(self):
        return dict(placed=[self._pl_tuple(p) for p in self.placed], src=self.view_src.get_nrect(),
                    dst=self.view_dst.get_nrect(), mode=self.cb_mode.currentData(), sens=self.sl_sens.value(),
                    keep=self.chk_keep.isChecked())

    def set_state(self, st):
        self._loading = True
        self.placed = [fr.Placement(*t) for t in st["placed"]]
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(st["mode"])))
        self.sl_sens.setValue(st["sens"])
        self.chk_keep.setChecked(st["keep"])
        self._loading = False
        self.refresh_list()
        self.show_dst()
        if st["src"] is not None and self.src is not None:
            self.view_src.set_nrect(st["src"], emit=False)
            self.recut(keep_dst=st["dst"])
        else:
            self.view_src.remove_box("main")
            self.view_dst.remove_box("main")
            self.cur_rgba = None
            self.lbl_prev.clear()

    def step_index(self) -> int:
        if not (self.src and self.dst):
            return 0
        if self._saved and not self.is_dirty():
            return 3
        return 2 if (self.placed or self.cur_rgba is not None) else 1

    def accept_files(self, paths):
        for p in paths[:2]:
            if self.src is None or (len(paths) == 2 and p == paths[0]):
                self.open_src(p)
            else:
                self.open_dst(p)

    # ---------- файлы ----------
    def open_src(self, path: str = ""):
        path = path or ask_open_pdf(self, "PDF, из которого вырезать")
        if not path:
            return
        doc = try_open_pdf(self, path)
        if doc is None:
            return
        self.src_path, self.src = path, doc
        self.dz_src.set_loaded(f"{os.path.basename(path)} · {len(self.src)} стр.")
        self.sp_src.setMaximum(len(self.src))
        self.view_src.clear_boxes()
        self.show_src()

    def open_dst(self, path: str = ""):
        path = path or ask_open_pdf(self, "PDF, в который вставить")
        if not path:
            return
        doc = try_open_pdf(self, path)
        if doc is None:
            return
        self.dst_path, self.dst = path, doc
        self.dz_dst.set_loaded(f"{os.path.basename(path)} · {len(self.dst)} стр.")
        self.sp_dst.setMaximum(len(self.dst))
        self.placed = []
        self.refresh_list()
        self.view_dst.clear_boxes()
        self._saved = False
        self.show_dst()
        if self.view_src.get_nrect() is not None:
            self.recut()
        self.reset_undo()

    def show_src(self, *_):
        if self.src is not None:
            self.view_src.set_page(page_pixmap(self.src, self.sp_src.value() - 1, VIEW_DPI))
            if self.view_src.get_nrect() is not None:
                self._timer.start()

    def _dst_doc(self):
        """Целевой документ с уже поставленными фрагментами (в памяти)."""
        if not self.placed:
            return self.dst
        return fr.apply(self.dst_path, self.placed)

    def show_dst(self, *_):
        if self.dst is None:
            return
        doc = self._dst_doc()
        self.view_dst.set_page(page_pixmap(doc, self.sp_dst.value() - 1, VIEW_DPI))
        if doc is not self.dst:
            doc.close()

    # ---------- вырезание ----------
    def src_rect_changed(self, key="main"):
        if not self._loading:
            self._timer.start()

    def settings_changed(self, *_):
        if self._loading:
            return
        s = settings()
        s.setValue("frag/mode", self.cb_mode.currentData())
        s.setValue("frag/sens", self.sl_sens.value())
        s.setValue("frag/keep", "true" if self.chk_keep.isChecked() else "false")
        self._timer.start()

    def _params(self):
        return self.cb_mode.currentData(), self.sl_sens.value() / 100, self.chk_keep.isChecked()

    def recut(self, keep_dst: NRect | None = None):
        nr = self.view_src.get_nrect()
        if self.src is None or nr is None:
            return
        mode, sens, keep = self._params()
        rgba = fr.cut(self.src, self.sp_src.value() - 1, nr, mode, sens, keep, dpi=PREVIEW_DPI)
        self.cur_rgba, self.cur_frac = fr.trim(rgba)
        self._show_preview()
        if self.dst is None:
            return
        old = keep_dst or self.view_dst.get_nrect()
        pm = np_to_pixmap(self.cur_rgba)
        page_rect = self.dst[self.sp_dst.value() - 1].rect
        nat = fr.natural_rect(self.src, self.sp_src.value() - 1, nr, self.cur_frac, page_rect)
        if old is None:
            target = nat
        else:   # оставляем место и ширину, высоту подгоняем под пропорции фрагмента
            w = old.x1 - old.x0
            asp = (nat.y1 - nat.y0) / max(nat.x1 - nat.x0, 1e-6)
            target = NRect(old.x0, old.y0, old.x0 + w, old.y0 + w * asp) if keep_dst is None else keep_dst
        self.view_dst.set_nrect(target, pm, emit=False)
        if not self._restoring and keep_dst is None:
            self.commit("фрагмент")

    def _show_preview(self):
        a = self.cur_rgba
        h, w = a.shape[:2]
        yy, xx = np.mgrid[:h, :w]
        bg = np.where(((yy // 8 + xx // 8) % 2)[..., None] == 0, 240, 210).astype(np.float32)
        al = a[..., 3:4] / 255.0
        img = (bg * (1 - al) + a[..., :3] * al).astype(np.uint8)
        pm = np_to_pixmap(img)
        self.lbl_prev.setPixmap(pm.scaled(self.lbl_prev.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def natural_size(self):
        nr = self.view_src.get_nrect()
        cur = self.view_dst.get_nrect()
        if self.cur_rgba is None or nr is None or cur is None:
            return
        nat = fr.natural_rect(self.src, self.sp_src.value() - 1, nr, self.cur_frac,
                              self.dst[self.sp_dst.value() - 1].rect, at=(cur.x0, cur.y0))
        self.view_dst.set_nrect(nat, np_to_pixmap(self.cur_rgba), emit=False)
        self.commit("натуральный размер")

    def dst_rect_changed(self, key="main"):
        if not self._loading:
            self.commit("место фрагмента")

    # ---------- список ----------
    def current_placement(self) -> fr.Placement | None:
        s, d = self.view_src.get_nrect(), self.view_dst.get_nrect()
        if self.src is None or self.dst is None or s is None or d is None or self.cur_rgba is None:
            return None
        mode, sens, keep = self._params()
        return fr.Placement(self.src_path, self.sp_src.value() - 1, s, self.sp_dst.value() - 1, d, mode, sens, keep)

    def place_current(self):
        pl = self.current_placement()
        if pl is None:
            return error(self, "Сначала обведите фрагмент на странице «Откуда» и откройте PDF «Куда».")
        self.placed.append(pl)
        self.view_src.remove_box("main")
        self.view_dst.remove_box("main")
        self.cur_rgba = None
        self.lbl_prev.clear()
        self.refresh_list()
        self.show_dst()
        self.commit("фрагмент поставлен")
        info(self, "Фрагмент закреплён. Обведите следующий или сохраните PDF.")

    def remove_placed(self):
        r = self.lst.currentRow()
        if 0 <= r < len(self.placed):
            del self.placed[r]
            self.refresh_list()
            self.show_dst()
            self.commit("фрагмент убран")

    def refresh_list(self):
        self.lst.clear()
        for p in self.placed:
            self.lst.addItem(f"{os.path.basename(p.src_path)} стр.{p.src_page + 1} → стр.{p.dst_page + 1} "
                             f"({FRAGMENT_MODES.get(p.mode, p.mode)})")

    # ---------- сохранение ----------
    def all_placements(self) -> list[fr.Placement]:
        cur = self.current_placement()
        return self.placed + ([cur] if cur else [])

    def save(self, out: str = ""):
        pls = self.all_placements()
        if self.dst is None or not pls:
            return error(self, "Нечего сохранять: откройте оба PDF и обведите фрагмент.")
        out = out or ask_save(self, safe_output_path(self.dst_path, "с_фрагментами"))
        if not out:
            return
        try:
            fr.apply(self.dst_path, pls, out).close()
        except Exception as e:  # noqa: BLE001
            log.exception("Вырезать и перенести")
            return error(self, str(e))
        self.mark_saved()
        self._saved = True
        info(self, f"Готово! Поставлено фрагментов: {len(pls)}. Файл: {os.path.basename(out)}",
             action=("Открыть папку", lambda: open_folder(out)))

    # ---------- подсказка ----------
    def tour_steps(self):
        from .tour import Step
        return [
            Step(None, "Вырезать и перенести", "Здесь можно взять <b>любой</b> кусок страницы — подпись, печать, "
                 "дату, телефон, галочку — и поставить его в другой PDF, в любое место. Белый фон убирается, "
                 "остаются только чернила. Нажимайте «Далее»."),
            Step(self.dz_src, "1. Откуда", "Откройте PDF, в котором есть нужная подпись или печать."),
            Step(self.dz_dst, "…и куда", "Откройте PDF, в который нужно её поставить. Можно тот же самый файл."),
            Step(self.card_src, "2. Обведите фрагмент", "Выберите страницу и обведите мышью то, что нужно "
                 "вырезать. Рамку можно двигать и тянуть за синий уголок."),
            Step(self.cb_mode, "Что вырезать", "<b>Синие чернила</b> — подписи синей ручкой. <b>Чёрные</b> — "
                 "чёрная ручка. <b>Цветная печать</b> — штампы; чёрный печатный текст под печатью не попадёт, "
                 "в этих местах в оттиске будут просветы. <b>Без удаления фона</b> — кусок вместе с бумагой."),
            Step(self.chk_keep, "Цвет", "Для печатей оставьте галочку — сохранится их цвет. Если снять, всё "
                 "станет цвета синей ручки."),
            Step(self.lbl_prev, "Проверьте", "Здесь видно, что вырезалось. Клетки — прозрачный фон. Если "
                 "попало лишнее — уменьшите рамку или чувствительность."),
            Step(self.card_dst, "Поставьте на место", "Фрагмент появился на странице «Куда». Перетащите его "
                 "мышью или просто щёлкните нужное место. Размер — за синий уголок, «1:1» — исходный размер."),
            Step(self.btn_place, "Ещё фрагмент", "Нужно несколько (подпись и печать)? Нажмите — текущий "
                 "закрепится, и можно обвести следующий."),
            Step(self.btn_save, "3. Сохраните", "Создаётся новый PDF, исходные не меняются. "
                 "Ctrl + Z отменяет последнее действие."),
        ]
