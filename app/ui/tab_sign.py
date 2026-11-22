"""Вкладка «Перенос подписей»."""
from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QLabel, QProgressDialog, QRadioButton, QScrollArea, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import numbers
from ..core import templates as tp
from ..core import signatures as sig
from ..core.ink import INK_MODES
from ..core.logsetup import log
from ..core.pdfutil import Cancelled, NRect, safe_output_path
from . import style
from .infra import UndoMixin, load_rect, run_task, save_rect, settings
from .chrome import Collapsible, DropZone, ScreenHeader
from .templbar import TemplateBar
from .thumbs import ThumbLoader, page_pixmap, placeholder_icon, visible_rows
from .widgets import (try_open_pdf, open_folder, PageView, ask_open_pdf, ask_save, big_button, error, info, np_to_pixmap, zoom_bar, card, progress_window)

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
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(12)
        self.header = ScreenHeader("Перенос подписей",
                                   "Подписи жильцов из актов переносятся в расписки. Исходные файлы не меняются.",
                                   ["Откройте акты и расписки", "Проверьте пары и рамки", "Сохраните новый PDF"])
        lay.addWidget(self.header)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.dz_acts = DropZone("Акты", "Перетащите сюда PDF с актами (откуда берём подписи) или нажмите")
        self.dz_acts.clicked.connect(self.open_acts)
        self.dz_acts.files.connect(lambda ps: self.open_acts(ps[0]))
        self.dz_rec = DropZone("Расписки", "Перетащите сюда PDF с расписками (куда ставим подписи) или нажмите")
        self.dz_rec.clicked.connect(self.open_receipts)
        self.dz_rec.files.connect(lambda ps: self.open_receipts(ps[0]))
        top.addWidget(self.dz_acts, 1)
        top.addWidget(self.dz_rec, 1)
        self.btn_recognize = big_button("🔢 Распознать номера и сопоставить", self.recognize)
        top.addWidget(self.btn_recognize)
        lay.addLayout(top)
        self.tbar = TemplateBar(self.capture_template)
        self.tbar.applied.connect(self.apply_template)
        self.page_tpl: dict = {"act": {}, "rec": {}}
        self._reviewed = False
        self._saved = False

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

        g1, v1 = card("Акт", "Откуда взять подпись — красная рамка")
        self.view_act = PageView(color="#e53935")
        self.view_act.rectChanged.connect(self.act_rect_changed)
        v1.addWidget(self.view_act, 1)
        v1.addLayout(zoom_bar(self.view_act))
        self.chk_src_own = QCheckBox("Рамка только для этой страницы акта")
        v1.addWidget(self.chk_src_own)
        self.chk_src_own.setToolTip("Отметьте, если у этого акта подпись в другом месте: рамка изменится только здесь")
        self.card_act = g1
        split.addWidget(g1)

        g2, v2 = card("Расписка", "Куда поставить подпись — синяя рамка")
        self.view_rec = PageView(color="#1565c0")
        self.view_rec.rectChanged.connect(self.rec_rect_changed)
        v2.addWidget(self.view_rec, 1)
        v2.addLayout(zoom_bar(self.view_rec))
        self.chk_dst_own = QCheckBox("Рамка только для этой расписки")
        v2.addWidget(self.chk_dst_own)
        self.chk_dst_own.setToolTip("Отметьте, чтобы рамка изменилась только для этой расписки")
        self.card_rec = g2
        split.addWidget(g2)
        self.split = split
        split.setSizes([520, 380, 380])
        lay.addWidget(split, 1)

        # ---- Дополнительно: шаблоны, режим рисования, чернила ----
        self.advanced = Collapsible("Дополнительно: шаблоны бланков, зоны номера, цвет чернил")
        self.advanced.add(self.tbar)
        draw = QHBoxLayout()
        draw.addWidget(QLabel("Рисовать мышью:"))
        self.r_sig = QRadioButton("рамку подписи")
        self.r_sig.setToolTip("Мышью на странице рисуется красная/синяя рамка подписи")
        self.r_zone = QRadioButton("зелёную зону номера квартиры")
        self.r_zone.setToolTip("Мышью рисуется зелёная зона, где написан номер квартиры (для распознавания)")
        self.r_sig.setChecked(True)
        grp = QButtonGroup(self)
        for b_ in (self.r_sig, self.r_zone):
            grp.addButton(b_)
            draw.addWidget(b_)
        self.r_sig.toggled.connect(self.draw_mode_changed)
        draw.addStretch()
        self.advanced.add(draw)
        draw = QHBoxLayout()
        draw.addWidget(QLabel("Цвет чернил:"))
        self.cb_mode = QComboBox()
        self.cb_mode.setToolTip("Какой ручкой подписывались: синяя — обычно; чёрная — убирает линии бланка")
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
        self.sl_sens.setToolTip("Больше — захватывает бледные штрихи, меньше — меньше лишних точек")
        self.sl_sens.valueChanged.connect(self.ink_changed)
        draw.addWidget(self.sl_sens)
        draw.addStretch()
        self.btn_reset = big_button("↺ Рамки по умолчанию", self.reset_rects)
        draw.addWidget(self.btn_reset)
        self.advanced.add(draw)
        lay.addWidget(self.advanced)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Так будет выглядеть подпись:"))
        self.lbl_ink = QLabel()
        self.lbl_ink.setObjectName("InkPreview")
        self.lbl_ink.setMinimumSize(160, 56)
        self.lbl_ink.setMaximumSize(300, 56)
        self.lbl_ink.setAlignment(Qt.AlignCenter)
        self.lbl_ink.setToolTip("Вырезанная подпись из выбранного акта — проверьте, что она целая и без лишнего")
        bottom.addWidget(self.lbl_ink, 1)
        bottom.addStretch()
        self.btn_preview = big_button("👁 Предпросмотр", self.preview)
        bottom.addWidget(self.btn_preview)
        self.btn_save = big_button("💾 Сохранить новый PDF…", self.save, primary=True)
        bottom.addWidget(self.btn_save)
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
    def open_acts(self, path: str = "") -> bool:
        path = path or ask_open_pdf(self, "Файл с актами")
        if path:
            doc = try_open_pdf(self, path)
            if doc is None:
                return False
            self.acts_path, self.acts = path, doc
            self.dz_acts.set_loaded(f"{os.path.basename(path)} · {len(self.acts)} стр.")
            self.nums_act = []
            self.page_tpl = {"act": {}, "rec": {}}
            self.rebuild()
            return True
        return False

    def open_receipts(self, path: str = "") -> bool:
        path = path or ask_open_pdf(self, "Файл с расписками")
        if path:
            doc = try_open_pdf(self, path)
            if doc is None:
                return False
            self.rec_path, self.rec = path, doc
            self.dz_rec.set_loaded(f"{os.path.basename(path)} · {len(self.rec)} стр.")
            self.nums_rec = []
            self.page_tpl = {"act": {}, "rec": {}}
            self.rebuild()
            return True
        return False

    def rebuild(self):
        if not (self.acts and self.rec):
            return
        from ..core.pdfutil import same_file
        if same_file(self.acts_path, self.rec_path):
            error(self, "Акты и расписки — один и тот же файл. Проверьте, что открыли нужные файлы.")
        self._reviewed = self._saved = False
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
            ta = self.page_tpl["act"].get(m) if m is not None else None
            tr = self.page_tpl["rec"].get(r)
            self.table.item(r, C_ANUM).setToolTip(f"Бланк акта: {ta or 'не узнан'}")
            self.table.item(r, C_RNUM).setToolTip(f"Бланк расписки: {tr or 'не узнан'}")
            c = self.conf[r]
            self.table.item(r, C_CONF).setText("" if c is None else f"{int(round(c * 100))}%")
            color = style.mark("white")
            if c is not None and (c < 0.5 or (rn and an and rn != an)):
                color = style.mark("#ffe082")   # проверить
            if m is None:
                color = style.mark("#eeeeee")
            for col in (C_RNUM, C_ANUM, C_CONF):
                self.table.item(r, col).setBackground(color)

    # ---------- распознавание ----------
    def recognize(self):
        if not self.job:
            return error(self, "Сначала откройте акты и расписки.")
        mode = "blue" if self.job.ink_mode == "blue" else "any"
        za, zr = [self.zone_act], [self.zone_rec]
        ap, rp = self.acts_path, self.rec_path
        temps = self.tbar.auto_templates()
        dlg = QProgressDialog("Распознаю номера квартир…", "Отмена", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        n_a, n_r = len(self.acts), len(self.rec)

        def work(progress):
            dr, da = [], []
            ra = numbers.read_document(rp, zr, mode, progress=lambda i, n: progress(i, n_a + n_r),
                                       temps=temps, role="rec", detected=dr)
            aa = numbers.read_document(ap, za, mode, progress=lambda i, n: progress(n_r + i, n_a + n_r),
                                       temps=temps, role="act", detected=da)
            names = {"rec": {i: d.template.name for i, d in enumerate(dr) if d.template},
                     "act": {i: d.template.name for i, d in enumerate(da) if d.template}}
            return ra, aa, names

        def done(res):
            dlg.close()
            self.nums_rec, self.nums_act, self.page_tpl = res
            mapping, conf = numbers.auto_match(self.nums_rec, self.nums_act)
            self._loading = True
            for r, m in enumerate(mapping):
                self.job.mapping[r] = m
                self.table.cellWidget(r, C_ACT).setCurrentIndex(0 if m is None else m + 1)
            self._loading = False
            self.conf = [c if m is not None else 0.0 for m, c in zip(mapping, conf)]
            if temps:   # рамки подписи — из шаблона, узнанного на каждой странице
                tp.apply_detected_frames(self.job, temps)
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

    # ---------- шаги, подсказка, перетаскивание ----------
    def step_index(self) -> int:
        if not self.job:
            return 0
        if self._saved and not self.is_dirty():
            return 3
        return 2 if (self._reviewed or self.undo_stack.count()) else 1

    def accept_files(self, paths: list[str]):
        """Файлы, брошенные на экран: первый — акты (если ещё нет), затем расписки."""
        for p in paths[:2]:
            if self.acts is None or (len(paths) == 2 and p == paths[0]):
                self.open_acts(p)
            else:
                self.open_receipts(p)

    def tour_steps(self):
        from .tour import Step
        adv = lambda: self.advanced.set_expanded(True)   # noqa: E731
        return [
            Step(None, "Перенос подписей за 3 шага",
                 "Программа берёт подпись жильца из <b>акта</b> и ставит её в <b>расписку</b> той же квартиры. "
                 "Исходные файлы не меняются — сохраняется новый PDF.<br><br>Нажимайте «Далее»."),
            Step(self.header.steps, "Шаги вверху", "Здесь видно, что уже сделано (зелёная галочка) "
                 "и что делать дальше (выделенный шаг)."),
            Step(self.dz_acts, "1. Откройте акты", "Нажмите сюда и выберите PDF с <b>актами</b> — "
                 "там стоят подписи жильцов. Можно просто перетащить файл мышью из папки."),
            Step(self.dz_rec, "…и расписки", "Сюда — PDF с <b>расписками</b>, в которые нужно поставить подписи."),
            Step(self.table, "2. Проверьте пары", "Каждая строка — одна расписка. В столбце «Подпись из акта» "
                 "выберите, из какой страницы акта брать подпись. «не ставить» — расписка останется пустой.<br>"
                 "Щёлкните строку — справа появятся обе страницы."),
            Step(self.btn_recognize, "Автоподбор (по желанию)", "Программа попробует прочитать номера квартир и "
                 "сама подобрать пары. Рукописные цифры читаются неточно — <b>жёлтые строки проверьте обязательно</b>, "
                 "остальные тоже просмотрите."),
            Step(self.card_act, "Красная рамка — откуда брать",
                 "На странице акта красная рамка показывает, где подпись. Потяните рамку мышью, чтобы сдвинуть, "
                 "или за синий квадратик в углу — чтобы изменить размер. Можно нарисовать новую рамку мышью."),
            Step(self.card_rec, "Синяя рамка — куда ставить", "На расписке синяя рамка — место, куда встанет "
                 "подпись. Двигается так же. Масштаб — кнопками внизу или Ctrl + колесо мыши."),
            Step(self.lbl_ink, "Проверьте подпись", "Здесь видно, как вырезалась подпись. Если она обрезана — "
                 "увеличьте красную рамку. Если много лишнего — уменьшите."),
            Step(self.advanced, "Дополнительно", "Шаблоны бланков (запомнить рамки для таких же бумаг), "
                 "зоны номера квартиры и цвет чернил. Нужны не всегда.", adv),
            Step(self.tbar, "Шаблоны бланков", "Настроили рамки — нажмите «Сохранить» и дайте имя, например "
                 "«Акт ЦО». В следующий раз выберите шаблон в списке, и всё встанет само. "
                 "«Экспорт» — файл шаблона для другого компьютера.", adv),
            Step(self.cb_mode, "Цвет чернил", "Если подписывались чёрной ручкой — выберите «чёрная». Ползунок "
                 "«Чувствительность» правее: больше — берёт бледные штрихи.", adv),
            Step(self.btn_preview, "Предпросмотр", "Покажет все расписки с подписями, ничего не сохраняя."),
            Step(self.btn_save, "3. Сохраните", "Создаёт новый PDF с подписями. Программа предложит имя рядом с "
                 "исходным файлом. Ошиблись — Ctrl + Z отменяет последнее действие."),
        ]

    # ---------- шаблоны ----------
    def capture_template(self, name: str) -> tp.Template:
        j = self.job
        t = tp.Template(name, src_rect=j.src_rect if j else load_rect("sign/src", sig.DEFAULT_SRC_RECT),
                        dst_rect=j.dst_rect if j else load_rect("sign/dst", sig.DEFAULT_DST_RECT),
                        zones_act=[self.zone_act], zones_rec=[self.zone_rec],
                        ink_mode=self.cb_mode.currentData(), sensitivity=self.sl_sens.value() / 100)
        r, a = self.current()
        if self.acts is not None:
            t.fp_act = tp.fingerprint(self.acts, a if a is not None else 0)
        if self.rec is not None:
            t.fp_rec = tp.fingerprint(self.rec, r if r is not None and r >= 0 else 0)
        return t

    def apply_template(self, t: tp.Template):
        if t.zones_act:
            self.zone_act = t.zones_act[0]
            save_rect("sign/zone_act", self.zone_act)
        if t.zones_rec:
            self.zone_rec = t.zones_rec[0]
            save_rect("sign/zone_rec", self.zone_rec)
        save_rect("sign/src", t.src_rect)
        save_rect("sign/dst", t.dst_rect)
        self._loading = True
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(t.ink_mode)))
        self.sl_sens.setValue(int(round(t.sensitivity * 100)))
        self._loading = False
        if self.job:
            self.job.src_rect = t.src_rect or self.job.src_rect
            self.job.dst_rect = t.dst_rect or self.job.dst_rect
            self.job.ink_mode, self.job.sensitivity = t.ink_mode, t.sensitivity
            self.show_row(self.table.currentRow())
            self.commit("шаблон «%s»" % t.name)

    # ---------- для пакетного режима ----------
    def load_pair(self, acts: str, receipts: str, template: str | None = None, mapping=None,
                  src_overrides=None, dst_overrides=None):
        if not (self.open_acts(acts) and self.open_receipts(receipts)):
            return False
        if template:
            self.tbar.reload(template)
            t = tp.get(template)
            if t:
                self.apply_template(t)
        if self.job and mapping is not None:
            self.set_mapping(list(mapping))
            self.job.src_overrides = dict(src_overrides or {})
            self.job.dst_overrides = dict(dst_overrides or {})
            self.show_row(self.table.currentRow())
        self.reset_undo()
        return True

    def export_pair(self) -> dict | None:
        if not self.job:
            return None
        return dict(acts=self.acts_path, receipts=self.rec_path, mapping=list(self.job.mapping),
                    src_overrides=dict(self.job.src_overrides), dst_overrides=dict(self.job.dst_overrides),
                    template=self.tbar.current_name())

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
        self._reviewed = True
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
                try:
                    img = sig.preview_page(self.job, i, dpi=70)
                except Exception as e:  # noqa: BLE001
                    log.exception("Предпросмотр")
                    dlg.deleteLater()
                    return error(self, f"Не удалось показать предпросмотр: {e}")
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
            with progress_window(self, "Переношу подписи в расписки…") as prog:
                rep = prog.run(sig.apply_transfer, self.job, out)
        except Cancelled:
            return info(self, "Отменено — новый файл не создан.")
        except Exception as e:  # noqa: BLE001
            log.exception("Сохранение подписей")
            return error(self, str(e))
        self.mark_saved()
        self._saved = True
        msg = (f"Готово! Сохранён файл {os.path.basename(out)}.\nПодписей поставлено: {len(rep['placed'])}, "
               f"пустых расписок: {len(rep['blank'])}.")
        if rep["empty_ink"]:
            msg += "\nВнимание: не нашлось подписи в рамке для расписок: " + \
                   ", ".join(str(i + 1) for i in rep["empty_ink"]) + ". Проверьте красную рамку."
        info(self, msg, action=("Открыть папку", lambda: open_folder(out)))
