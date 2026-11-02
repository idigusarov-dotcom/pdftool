"""Вкладка «Перенос подписей»."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout,
                               QLabel, QProgressDialog, QRadioButton, QScrollArea, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import numbers
from ..core import signatures as sig
from ..core.ink import INK_MODES
from ..core.logsetup import log
from ..core.pdfutil import NRect, safe_output_path
from .infra import UndoMixin, load_rect, run_task, save_rect, settings
from .thumbs import ThumbLoader, page_pixmap, placeholder_icon, visible_rows
from .widgets import (PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap, zoom_bar)

HELP = ("1) Откройте АКТЫ и РАСПИСКИ.  2) Сопоставьте: вручную в таблице или кнопкой «Распознать номера» "
        "(сначала задайте зелёную «зону номера» на акте и на расписке).  3) Красная рамка — откуда берём подпись, "
        "синяя — куда ставим. 4) «Предпросмотр» → «Сохранить».  Ctrl+Z — отменить.")

C_REC, C_RNUM, C_ACT, C_ANUM, C_CONF = range(5)
DEFAULT_ZONE_ACT = NRect(0.20, 0.33, 0.40, 0.37)
DEFAULT_ZONE_REC = NRect(0.33, 0.21, 0.47, 0.26)


class SignTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.acts: pymupdf.Document | None = None
        self.rec: pymupdf.Document | None = None
        self.acts_path = self.rec_path = ""
        self.job: sig.TransferJob | None = None
        self.nums_rec: list = []
        self.nums_act: list = []
        self.conf: list = []
        self._loading = False
        self.loader: ThumbLoader | None = None
        s = settings()
        self.zone_act = load_rect("sign/zone_act", DEFAULT_ZONE_ACT)
        self.zone_rec = load_rect("sign/zone_rec", DEFAULT_ZONE_REC)

        lay = QVBoxLayout(self)
        h = QLabel(HELP)
        h.setWordWrap(True)
        lay.addWidget(h)
        top = QHBoxLayout()
        top.addWidget(big_button("📄 Открыть акты…", self.open_acts))
        self.lbl_acts = QLabel("акты не выбраны")
        top.addWidget(self.lbl_acts)
        top.addSpacing(16)
        top.addWidget(big_button("📄 Открыть расписки…", self.open_receipts))
        self.lbl_rec = QLabel("расписки не выбраны")
        top.addWidget(self.lbl_rec)
        top.addStretch()
        top.addWidget(big_button("🔢 Распознать номера и сопоставить", self.recognize))
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Расписка", "Кв.", "Подпись из акта", "Кв. в акте", "Увер."])
        self.table.setIconSize(QSize(48, 64))
        self.table.verticalHeader().setDefaultSectionSize(70)
        for c, w in ((C_REC, 110), (C_RNUM, 55), (C_ACT, 190), (C_ANUM, 70), (C_CONF, 55)):
            self.table.setColumnWidth(c, w)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_row(r))
        self.table.verticalScrollBar().valueChanged.connect(self.request_thumbs)
        split.addWidget(self.table)

        g1 = QGroupBox("Акт — откуда взять подпись (красная рамка)")
        v1 = QVBoxLayout(g1)
        self.view_act = PageView(color="#e53935")
        self.view_act.rectChanged.connect(self.act_rect_changed)
        v1.addWidget(self.view_act)
        v1.addLayout(zoom_bar(self.view_act))
        self.chk_src_own = QCheckBox("Рамка только для этой страницы акта")
        v1.addWidget(self.chk_src_own)
        split.addWidget(g1)

        g2 = QGroupBox("Расписка — куда поставить (синяя рамка)")
        v2 = QVBoxLayout(g2)
        self.view_rec = PageView(color="#1565c0")
        self.view_rec.rectChanged.connect(self.rec_rect_changed)
        v2.addWidget(self.view_rec)
        v2.addLayout(zoom_bar(self.view_rec))
        self.chk_dst_own = QCheckBox("Рамка только для этой расписки")
        v2.addWidget(self.chk_dst_own)
        split.addWidget(g2)
        split.setSizes([480, 420, 420])
        lay.addWidget(split, 1)

        # что рисуем мышью
        draw = QHBoxLayout()
        draw.addWidget(QLabel("Рисовать мышью:"))
        self.r_sig = QRadioButton("рамку подписи")
        self.r_zone = QRadioButton("зелёную зону номера квартиры")
        self.r_sig.setChecked(True)
        grp = QButtonGroup(self)
        for b in (self.r_sig, self.r_zone):
            grp.addButton(b)
            draw.addWidget(b)
        self.r_sig.toggled.connect(self.draw_mode_changed)
        draw.addSpacing(20)
        draw.addWidget(QLabel("Цвет чернил:"))
        self.cb_mode = QComboBox()
        for k, v in INK_MODES.items():
            self.cb_mode.addItem(v, k)
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(s.value("sign/ink_mode", "blue"))))
        self.cb_mode.currentIndexChanged.connect(self.ink_changed)
        draw.addWidget(self.cb_mode)
        draw.addWidget(QLabel("Чувствительность:"))
        self.sl_sens = QSlider(Qt.Horizontal)
        self.sl_sens.setRange(0, 100)
        self.sl_sens.setValue(int(float(s.value("sign/sens", 50))))
        self.sl_sens.setFixedWidth(150)
        self.sl_sens.valueChanged.connect(self.ink_changed)
        draw.addWidget(self.sl_sens)
        self.lbl_ink = QLabel()
        self.lbl_ink.setMinimumSize(280, 70)
        self.lbl_ink.setStyleSheet("background: white; border: 1px solid #bbb;")
        self.lbl_ink.setToolTip("Так будет выглядеть вырезанная подпись")
        draw.addWidget(QLabel("Подпись:"))
        draw.addWidget(self.lbl_ink, 1)
        lay.addLayout(draw)

        bottom = QHBoxLayout()
        bottom.addWidget(big_button("↺ Рамки по умолчанию", self.reset_rects))
        bottom.addStretch()
        bottom.addWidget(big_button("👁 Предпросмотр", self.preview))
        bottom.addWidget(big_button("💾 Сохранить новый PDF…", self.save, primary=True))
        lay.addLayout(bottom)
        self._ink_timer = QTimer(self, singleShot=True, interval=250, timeout=self.update_ink_preview)

    # ---------- состояние для Ctrl+Z ----------
    def get_state(self):
        j = self.job
        if not j:
            return None
        return dict(mapping=list(j.mapping), src=j.src_rect, dst=j.dst_rect, so=dict(j.src_overrides),
                    do=dict(j.dst_overrides), mode=j.ink_mode, sens=j.sensitivity)

    def set_state(self, st):
        if not st or not self.job:
            return
        j = self.job
        j.mapping, j.src_rect, j.dst_rect = list(st["mapping"]), st["src"], st["dst"]
        j.src_overrides, j.dst_overrides = dict(st["so"]), dict(st["do"])
        j.ink_mode, j.sensitivity = st["mode"], st["sens"]
        self._loading = True
        for r, m in enumerate(j.mapping):
            self.table.cellWidget(r, C_ACT).setCurrentIndex(0 if m is None else m + 1)
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(j.ink_mode)))
        self.sl_sens.setValue(int(j.sensitivity * 100))
        self._loading = False
        self.refresh_marks()
        self.show_row(self.table.currentRow())

    # ---------- загрузка ----------
    def open_acts(self, path: str = ""):
        path = path or ask_open_pdf(self, "Файл с актами")
        if path:
            self.acts_path, self.acts = path, pymupdf.open(path)
            self.lbl_acts.setText(f"{os.path.basename(path)} ({len(self.acts)} стр.)")
            self.nums_act = []
            self.rebuild()

    def open_receipts(self, path: str = ""):
        path = path or ask_open_pdf(self, "Файл с расписками")
        if path:
            self.rec_path, self.rec = path, pymupdf.open(path)
            self.lbl_rec.setText(f"{os.path.basename(path)} ({len(self.rec)} стр.)")
            self.nums_rec = []
            self.rebuild()

    def rebuild(self):
        if not (self.acts and self.rec):
            return
        self._loading = True
        s = settings()
        self.job = sig.TransferJob(self.acts_path, self.rec_path, sig.guess_mapping(len(self.rec), len(self.acts)),
                                   src_rect=load_rect("sign/src", sig.DEFAULT_SRC_RECT),
                                   dst_rect=load_rect("sign/dst", sig.DEFAULT_DST_RECT),
                                   ink_mode=str(s.value("sign/ink_mode", "blue")),
                                   sensitivity=float(s.value("sign/sens", 50)) / 100)
        self.conf = [None] * len(self.rec)
        self.table.setRowCount(len(self.rec))
        ph = placeholder_icon()
        for i in range(len(self.rec)):
            it = QTableWidgetItem(ph, f"стр. {i + 1}")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, C_REC, it)
            for c in (C_RNUM, C_ANUM, C_CONF):
                x = QTableWidgetItem("")
                x.setFlags(x.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, c, x)
            cb = QComboBox()
            cb.addItem("— не ставить (пусто) —", None)
            for a in range(len(self.acts)):
                cb.addItem(f"Акт, стр. {a + 1}", a)
            m = self.job.mapping[i]
            cb.setCurrentIndex(0 if m is None else m + 1)
            cb.currentIndexChanged.connect(lambda _=0, row=i, c=cb: self.map_changed(row, c))
            self.table.setCellWidget(i, C_ACT, cb)
        self._loading = False
        if self.loader:
            self.loader.stop()
        self.loader = ThumbLoader(self.rec_path, 25)
        self.loader.ready.connect(self.thumb_ready)
        self.refresh_marks()
        self.table.setCurrentCell(0, C_ACT)
        self.show_row(0)
        QTimer.singleShot(0, self.request_thumbs)
        self.reset_undo()

    def request_thumbs(self, *_):
        if self.loader:
            self.loader.request(visible_rows(self.table))

    def thumb_ready(self, path, i, img):
        if path == self.rec_path and i < self.table.rowCount():
            self.table.item(i, C_REC).setIcon(QIcon(QPixmap.fromImage(img)))

    def set_mapping(self, mapping):
        """Для тестов/скриптов: задать соответствие (0-based, None — пусто)."""
        for i, m in enumerate(mapping):
            self.table.cellWidget(i, C_ACT).setCurrentIndex(0 if m is None else m + 1)

    def map_changed(self, row, cb):
        if self._loading:
            return
        self.job.mapping[row] = cb.currentData()
        self.conf[row] = None
        self.refresh_marks()
        if row == self.table.currentRow():
            self.show_row(row)
        self.commit("соответствие")

    def refresh_marks(self):
        if not self.job:
            return
        for r in range(self.table.rowCount()):
            m = self.job.mapping[r]
            rn = self.nums_rec[r].text if r < len(self.nums_rec) else ""
            an = self.nums_act[m].text if (m is not None and m < len(self.nums_act)) else ""
            self.table.item(r, C_RNUM).setText(rn)
            self.table.item(r, C_ANUM).setText(an)
            c = self.conf[r]
            self.table.item(r, C_CONF).setText("" if c is None else f"{int(round(c * 100))}%")
            color = QColor("white")
            if c is not None and (c < 0.5 or (rn and an and rn != an)):
                color = QColor("#ffe082")   # проверить
            if m is None:
                color = QColor("#eeeeee")
            for col in (C_RNUM, C_ANUM, C_CONF):
                self.table.item(r, col).setBackground(color)

    # ---------- распознавание ----------
    def recognize(self):
        if not self.job:
            return error(self, "Сначала откройте акты и расписки.")
        mode = "blue" if self.job.ink_mode == "blue" else "any"
        za, zr = [self.zone_act], [self.zone_rec]
        ap, rp = self.acts_path, self.rec_path
        dlg = QProgressDialog("Распознаю номера квартир…", "Отмена", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        n_a, n_r = len(self.acts), len(self.rec)

        def work(progress):
            ra = numbers.read_document(rp, zr, mode, progress=lambda i, n: progress(i, n_a + n_r))
            aa = numbers.read_document(ap, za, mode, progress=lambda i, n: progress(n_r + i, n_a + n_r))
            return ra, aa

        def done(res):
            dlg.close()
            self.nums_rec, self.nums_act = res
            mapping, conf = numbers.auto_match(self.nums_rec, self.nums_act)
            self._loading = True
            for r, m in enumerate(mapping):
                self.job.mapping[r] = m
                self.table.cellWidget(r, C_ACT).setCurrentIndex(0 if m is None else m + 1)
            self._loading = False
            self.conf = [c if m is not None else 0.0 for m, c in zip(mapping, conf)]
            self.refresh_marks()
            self.show_row(self.table.currentRow())
            self.commit("автосопоставление")
            low = sum(1 for r, c in enumerate(self.conf) if c < 0.5)
            info(self, f"Готово. Сопоставлено: {sum(m is not None for m in mapping)} из {len(mapping)}.\n"
                       f"Строк для проверки (жёлтые): {low}.\nРаспознавание рукописных цифр неточное — "
                       f"проверьте каждую строку по миниатюрам.")

        def fail(msg):
            dlg.close()
            error(self, "Не удалось распознать: " + msg)

        t = run_task(work, done, fail, lambda i, n: dlg.setValue(int(100 * i / max(n, 1))))
        dlg.canceled.connect(lambda: setattr(t, "cancelled", True))

    # ---------- рамки ----------
    def current(self):
        r = self.table.currentRow()
        if not self.job or r < 0:
            return None, None
        return r, self.job.mapping[r]

    def draw_mode_changed(self, *_):
        key = "main" if self.r_sig.isChecked() else "zone"
        self.view_act.active = self.view_rec.active = key

    def show_row(self, r):
        if not self.job or r is None or r < 0:
            return
        self._loading = True
        a = self.job.mapping[r]
        self.view_rec.clear_boxes()
        self.view_rec.set_page(page_pixmap(self.rec, r, 90))
        self.chk_dst_own.setChecked(r in self.job.dst_overrides)
        self.view_rec.set_nrect(self.job.dst_overrides.get(r, self.job.dst_rect))
        self.view_rec.set_nrect(self.zone_rec, key="zone")
        self.view_act.clear_boxes()
        if a is None:
            self.view_act.show_message("Подпись не ставится")
        else:
            self.view_act.set_page(page_pixmap(self.acts, a, 90))
            self.chk_src_own.setChecked(a in self.job.src_overrides)
            self.view_act.set_nrect(self.job.src_overrides.get(a, self.job.src_rect))
            self.view_act.set_nrect(self.zone_act, key="zone")
        self._loading = False
        self._ink_timer.start()

    def act_rect_changed(self, key="main"):
        r, a = self.current()
        nr = self.view_act.get_nrect(key)
        if self._loading or a is None or nr is None:
            return
        if key == "zone":
            self.zone_act = nr
            save_rect("sign/zone_act", nr)
            return
        if self.chk_src_own.isChecked():
            self.job.src_overrides[a] = nr
        else:
            self.job.src_rect = nr
            save_rect("sign/src", nr)
        self._ink_timer.start()
        self.commit("рамка в акте")

    def rec_rect_changed(self, key="main"):
        r, _ = self.current()
        nr = self.view_rec.get_nrect(key)
        if self._loading or r is None or nr is None:
            return
        if key == "zone":
            self.zone_rec = nr
            save_rect("sign/zone_rec", nr)
            return
        if self.chk_dst_own.isChecked():
            self.job.dst_overrides[r] = nr
        else:
            self.job.dst_rect = nr
            save_rect("sign/dst", nr)
        self.commit("рамка в расписке")

    def ink_changed(self, *_):
        if self._loading or not self.job:
            return
        self.job.ink_mode = self.cb_mode.currentData()
        self.job.sensitivity = self.sl_sens.value() / 100
        settings().setValue("sign/ink_mode", self.job.ink_mode)
        settings().setValue("sign/sens", self.sl_sens.value())
        self._ink_timer.start()
        self.commit("чернила")

    def update_ink_preview(self):
        r, a = self.current()
        if a is None or not self.job:
            self.lbl_ink.clear()
            return
        src = self.job.src_overrides.get(a, self.job.src_rect)
        rgba = sig.cut_signature(self.acts, a, src, 110, True, self.job.ink_mode, self.job.sensitivity)
        if rgba.size == 0:
            return
        # показываем на белом фоне
        import numpy as np
        al = rgba[..., 3:4].astype(np.float32) / 255
        rgb = (255 * (1 - al) + rgba[..., :3] * al).astype(np.uint8)
        pm = np_to_pixmap(rgb)
        self.lbl_ink.setPixmap(pm.scaled(self.lbl_ink.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def reset_rects(self):
        if self.job:
            self.job.src_rect, self.job.dst_rect = sig.DEFAULT_SRC_RECT, sig.DEFAULT_DST_RECT
            self.job.src_overrides.clear()
            self.job.dst_overrides.clear()
            self.show_row(self.table.currentRow())
            self.commit("рамки по умолчанию")

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
            log.exception("Сохранение подписей")
            return error(self, str(e))
        QApplication.restoreOverrideCursor()
        self.mark_saved()
        msg = f"Сохранено: {out}\nПодписей поставлено: {len(rep['placed'])}, пустых: {len(rep['blank'])}."
        if rep["empty_ink"]:
            msg += "\nВНИМАНИЕ: в рамке не найдено чернил для расписок: " + \
                   ", ".join(str(i + 1) for i in rep["empty_ink"])
        info(self, msg)
