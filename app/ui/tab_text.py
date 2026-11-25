"""Редактирование текста, сканов и полей произвольного PDF."""
from __future__ import annotations

import os
import re

import pymupdf as pdf
from PySide6.QtCore import QEvent, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressDialog,
                               QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core import textedit as editor
from ..core.pdfutil import Cancelled, PdfError, human_error, safe_output_path
from .chrome import DropZone, FileStartPanel, ScreenHeader
from .infra import UndoMixin
from .thumbs import page_pixmap, visible_rows
from .tour import Step
from .widgets import (PageView, ask_open_pdf, ask_save, big_button, error, info, progress_window,
                      try_open_pdf, zoom_bar)

MODES = {
    "line": ("Изменить строку", "Щёлкните по строке, которую хотите изменить. Tab — следующая строка, Enter — править."),
    "add": ("Добавить текст", "Обведите место для новой надписи."),
    "block": ("Заменить абзац", "Обведите строки целиком. Новый текст будет перенесён внутри рамки."),
    "scan": ("Исправить скан", "Обведите текст на скане. Участок закрасится цветом бумаги вокруг него."),
    "field": ("Заполнить поле", "Щёлкните по полю PDF-формы. Tab — следующее поле, Enter — заполнить."),
}

EDIT_KEYS = "Enter — применить, Esc — отмена, Tab — применить и перейти к следующей строке."


def same_path(a, b):
    """Один и тот же файл, даже если путь записан по-разному (C:/x и C:\\X на Windows)."""
    if not a or not b:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))
    return norm(a) == norm(b)


def reading_order(targets):
    """Строки в порядке чтения: сверху вниз, в строке — слева направо."""
    def key(t):
        r = pdf.Rect(t.rect)
        return round(r.y0 / max(2.0, r.height * 0.6)), r.x0
    return sorted(targets, key=key)


class TextView(PageView):
    picked = Signal(object)
    missed = Signal()

    def __init__(self):
        super().__init__()
        self.draw_enabled = False
        self.setMouseTracking(True)
        self.setAccessibleName("Страница PDF")
        self.page = None
        self.targets = []
        self.mode = "line"
        self.start = None
        self.highlight = None
        self.current = None         # строка, выбранная с клавиатуры
        self.marks = []
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self):
        # Без этого подсказка размера страницы (~334×472) выталкивала нижнюю панель за край окна 720 px.
        return QSize(480, 260)

    def minimumSizeHint(self):
        return QSize(200, 160)

    def display(self, doc, number):
        page = doc[number]
        dpi = max(1, min(135, int(2200 * 72 / max(page.rect.width, page.rect.height))))
        self.set_page(page_pixmap(doc, number, dpi))
        self.page = page
        self.start = None
        pen = QPen(QColor("#0879db"), 2)
        pen.setCosmetic(True)
        self.highlight = self.scene().addRect(QRectF(), pen, QColor(44, 143, 239, 34))
        self.highlight.setZValue(2)
        self.highlight.hide()
        self.marks = []
        self.current = None

    def scene_rect(self, rect):
        box = pdf.Rect(rect) * self.page.rotation_matrix
        w, h = self._size()
        sx, sy = w / self.page.rect.width, h / self.page.rect.height
        return QRectF(box.x0 * sx, box.y0 * sy, box.width * sx, box.height * sy)

    def mark(self, rects):
        """Подсветка уже изменённых мест текущей страницы."""
        for item in self.marks:
            self.scene().removeItem(item)
        self.marks = []
        if self.page is None:
            return
        pen = QPen(QColor("#1f9d55"), 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        for rect in rects:
            item = self.scene().addRect(self.scene_rect(rect).adjusted(-1, -1, 1, 1), pen, QColor(31, 157, 85, 28))
            item.setZValue(1)
            item.setToolTip("Изменено")
            self.marks.append(item)

    def step(self, delta):
        """Tab/Shift+Tab: выбрать следующую/предыдущую строку или поле."""
        order = reading_order(self.targets)
        if not order:
            return None
        if self.current in order:
            index = (order.index(self.current) + delta) % len(order)
        else:
            index = 0 if delta > 0 else len(order) - 1
        self.current = order[index]
        self.outline(self.current.rect)
        self.ensureVisible(self.scene_rect(self.current.rect), 40, 40)
        return self.current

    def event(self, event):
        # Tab в QGraphicsView обычно уводит фокус; здесь он переключает строки.
        if (event.type() == QEvent.KeyPress and self.mode in ("line", "field") and self.targets
                and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)):
            self.step(-1 if event.key() == Qt.Key_Backtab or event.modifiers() & Qt.ShiftModifier else 1)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if self.mode in ("line", "field") and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            target = self.current or self.step(1)
            if target is not None:
                self.picked.emit(target)
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self.current is not None:
            self.current = None
            self.outline()
            event.accept()
            return
        super().keyPressEvent(event)

    def point(self, event):
        pos = self.mapToScene(event.position().toPoint())
        w, h = self._size()
        return pdf.Point(pos.x() * self.page.rect.width / w,
                         pos.y() * self.page.rect.height / h) * self.page.derotation_matrix

    def outline(self, rect=None):
        if self.highlight is None:
            return
        if rect is None:
            self.highlight.hide()
            return
        self.highlight.setRect(self.scene_rect(rect))
        self.highlight.show()

    def hit(self, point):
        hits = [t for t in self.targets if point in pdf.Rect(t.rect)]
        return min(hits, key=lambda t: pdf.Rect(t.rect).get_area(), default=None)

    def region(self, end):
        return pdf.Rect(self.start, end).normalize() & editor.bounds(self.page)

    def mouseMoveEvent(self, event):
        if self.page is not None:
            p = self.point(event)
            if self.start is not None:
                self.outline(self.region(p))
            else:
                target = self.hit(p)
                if target is None and self.current is not None:
                    target = self.current
                self.outline(target.rect if target else None)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self.page is not None and event.button() == Qt.LeftButton:
            p = self.point(event)
            if p in editor.bounds(self.page):
                if self.mode in ("line", "field"):
                    self.setFocus()
                    target = self.hit(p)
                    if target:
                        self.current = target
                        self.picked.emit(target)
                    else:
                        self.missed.emit()
                else:
                    self.start = p
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.start is not None and event.button() == Qt.LeftButton:
            area = self.region(self.point(event))
            self.start = None
            if area.width >= 3 and area.height >= 3:
                self.picked.emit(editor.Target(tuple(area)))
            self.outline()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class EditDialog(QDialog):
    def __init__(self, tab, target):
        super().__init__(tab)
        self.tab, self.target = tab, target
        mode = tab.mode.currentData()
        self.setWindowTitle(MODES[mode][0])
        self.resize(500, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.input = QPlainTextEdit(self)
        self.choice = None
        self.checkbox = None
        self.custom_fonts = {}
        self.advance = 0            # после «Применить»: 1 — следующая строка, -1 — предыдущая
        self.original_value = None
        self.color = QColor.fromRgb(0xff000000 | target.color)
        if mode == "field":
            field = next((w for w in tab.view.page.widgets() if w.field_name == target.text and
                          w.rect == pdf.Rect(target.rect)), None)
            if field is None:
                raise PdfError("Поле больше не найдено на странице. Обновите страницу и выберите его снова.")
            if field.field_flags & pdf.PDF_FIELD_IS_READ_ONLY:
                raise PdfError("Поле доступно только для чтения.")
            if field.field_type == pdf.PDF_WIDGET_TYPE_TEXT:
                self.original_value = field.field_value or ""
                self.input.setPlainText(self.original_value)
                form.addRow(field.field_name, self.input)
            elif field.field_type == pdf.PDF_WIDGET_TYPE_CHECKBOX:
                self.checkbox = QCheckBox(field.field_name)
                self.checkbox.setChecked(field.field_value == field.on_state())
                self.original_value = "1" if self.checkbox.isChecked() else "0"
                form.addRow(self.checkbox)
            elif field.field_type in (pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
                self.choice = QComboBox()
                self.choice.addItems(field.choice_values or [])
                self.choice.setCurrentText(field.field_value or "")
                self.original_value = field.field_value or ""
                form.addRow(field.field_name, self.choice)
            else:
                raise PdfError("Этот тип поля доступен только для просмотра.")
        else:
            value = target.text
            if mode == "block":
                value = "\n".join(t.text for t in editor.spans(tab.view.page)
                                  if pdf.Rect(target.rect).contains(pdf.Rect(t.rect)))
            self.original_value = value
            self.input.setPlainText(value)
            form.addRow("Текст", self.input)
            if mode == "line":
                # Одна строка: компактное поле, Enter применяет, Tab — к следующей строке.
                metrics = self.input.fontMetrics()
                self.input.setFixedHeight(metrics.lineSpacing() * 2 + 14)
                self.input.setTabChangesFocus(False)
                self.input.installEventFilter(self)
                self.input.moveCursor(self.input.textCursor().MoveOperation.End)
                self.resize(520, 300)
            if mode == "scan":
                form.addRow(big_button("Распознать русский и английский текст", self.recognize))
            self.font = QComboBox()
            if mode == "line":
                self.font.addItem("Как в документе", "Как в документе")
                self.font.setItemText(0, f"Как в документе ({target.font})")
            for name in ("Inter", "Inter SemiBold", "Helvetica", "Times-Roman", "Courier"):
                self.font.addItem(name, name)
            fonts = QHBoxLayout()
            fonts.addWidget(self.font, 1)
            browse = big_button("Из файла…", self.load_font, tip="Выбрать шрифт в формате TTF или OTF")
            fonts.addWidget(browse)
            form.addRow("Шрифт", fonts)
            self.size = QDoubleSpinBox()
            self.size.setDecimals(2)
            self.size.setRange(1, 400)
            self.size.setValue(target.size)
            self.initial_size = self.size.value()
            self.size.setSuffix(" пт")
            form.addRow("Размер", self.size)
            self.color_button = QPushButton()
            self.update_color()
            self.color_button.clicked.connect(self.choose_color)
            form.addRow("Цвет", self.color_button)
            self.align = QComboBox()
            self.align.addItems(["По левому краю", "По центру", "По правому краю"])
            form.addRow("Выравнивание", self.align)
        if self.choice is not None or self.checkbox is not None:
            self.input.hide()
        if mode != "field":
            self.font_hint = QLabel()
            self.font_hint.setWordWrap(True)
            layout.addWidget(self.font_hint)
            self.font.currentIndexChanged.connect(self.show_font)
            self.input.textChanged.connect(self.show_font)
            self.show_font()
        if mode in ("line", "field") and self.checkbox is None:
            keys = QLabel(EDIT_KEYS)
            keys.setWordWrap(True)
            keys.setProperty("role", "hint")
            layout.addWidget(keys)
            if mode == "field":
                self.input.installEventFilter(self)
            if self.choice is not None:
                self.choice.installEventFilter(self)
        if self.checkbox is not None:
            self.checkbox.installEventFilter(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        apply_button = buttons.button(QDialogButtonBox.Ok)
        apply_button.setText("Применить")
        apply_button.setProperty("kind", "primary")
        apply_button.setDefault(True)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self.submit()
                return True
            if key in (Qt.Key_Tab, Qt.Key_Backtab):
                self.advance = -1 if key == Qt.Key_Backtab or event.modifiers() & Qt.ShiftModifier else 1
                self.submit()
                if not self.isVisible() or self.result() == QDialog.Accepted:
                    return True
                self.advance = 0
                return True
        return super().eventFilter(obj, event)

    def load_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите шрифт", "", "Шрифты (*.ttf *.otf)")
        if not path:
            return
        try:
            with open(path, "rb") as stream:
                data = stream.read()
            probe = editor.Edit(self.tab.number, self.tab.mode.currentData(), self.target, "", font_data=data)
            actual, _, _ = editor.choose_font(self.tab.view.page, probe)
        except Exception as exc:
            self.message.setText(human_error(exc))
            return
        self.custom_fonts[path] = data
        index = self.font.findData(path)
        if index < 0:
            self.font.addItem(actual.name, path)
            index = self.font.count() - 1
        self.font.setCurrentIndex(index)

    def show_font(self):
        edit = editor.Edit(self.tab.number, self.tab.mode.currentData(), self.target, self.input.toPlainText(),
                           self.font.currentData(), font_data=self.custom_fonts.get(self.font.currentData()))
        try:
            actual, _, _ = editor.choose_font(self.tab.view.page, edit)
            self.font_hint.setText(f"Шрифт в сохранённом PDF — {actual.name}")
        except PdfError as exc:
            self.font_hint.setText(str(exc))

    def update_color(self):
        chip = QPixmap(18, 18)
        chip.fill(self.color)
        self.color_button.setIcon(QIcon(chip))
        self.color_button.setText(self.color.name())
        self.color_button.setAccessibleName(f"Выбрать цвет текста, текущий цвет {self.color.name()}")
        self.color_button.setToolTip("Выбрать цвет текста")

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "Цвет текста")
        if color.isValid():
            self.color = color
            self.update_color()

    def recognize(self):
        try:
            self.input.setPlainText(editor.recognize(self.tab.view.page, self.target.rect))
            self.message.setText("Проверьте распознанный текст." if self.input.toPlainText()
                                 else "Текст не распознан. Введите его вручную.")
        except Exception as exc:
            self.message.setText(human_error(exc))

    def submit(self):
        tab = self.tab
        mode = tab.mode.currentData()
        value = self.input.toPlainText()
        if self.checkbox is not None:
            value = "1" if self.checkbox.isChecked() else "0"
        elif self.choice is not None:
            value = self.choice.currentText()
        if mode == "field":
            if value == self.original_value:
                self.accept()
                return
            edit = editor.Edit(tab.number, mode, self.target, value)
        else:
            size = self.target.size if self.size.value() == self.initial_size else self.size.value()
            if (mode == "line" and value == self.original_value and self.font.currentData() == "Как в документе"
                    and size == self.target.size and self.color.rgb() & 0xffffff == self.target.color
                    and self.align.currentIndex() == 0):
                self.accept()
                return
            edit = editor.Edit(tab.number, mode, self.target, value, self.font.currentData(),
                               size, self.color.rgb() & 0xffffff, self.align.currentIndex(),
                               self.custom_fonts.get(self.font.currentData()))
        try:
            tab.change([edit])
        except Exception as exc:
            self.message.setText(human_error(exc))
            return
        self.accept()


class TextTab(QWidget, UndoMixin):
    def __init__(self):
        super().__init__()
        self.init_undo()
        self.path = ""
        self.doc = None
        self.source = None
        self.number = 0
        self.edits = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        self.header = ScreenHeader("Текст", "Редактируйте текст на странице, исправляйте сканы и заполняйте формы.",
                                   ["Откройте PDF", "Внесите правки", "Сохраните копию"])
        layout.addWidget(self.header)
        self.start = FileStartPanel("Откройте PDF", "Перетащите документ для редактирования текста",
                                   "Выбрать PDF…", lambda: self.open(ask_open_pdf(self)))
        self.start.files.connect(self.accept_files)
        layout.addWidget(self.start, 1)
        self.editor = QWidget()
        layout.addWidget(self.editor, 1)
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(0, 0, 0, 0)
        self.dz = DropZone("Открыть PDF")
        self.dz.files.connect(self.accept_files)
        self.dz.clicked.connect(lambda: self.open(ask_open_pdf(self)))
        layout.addWidget(self.dz)
        toolbar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName("Инструмент редактирования")
        for key, (name, _) in MODES.items():
            self.mode.addItem(name, key)
        toolbar.addWidget(self.mode)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Найти текст в документе")
        self.query.setClearButtonEnabled(True)
        for button in self.query.findChildren(QToolButton):
            button.setToolTip("Очистить поиск")
            button.setAccessibleName("Очистить поиск")
        self.query.returnPressed.connect(self.search)
        QShortcut(QKeySequence.Find, self, context=Qt.WidgetWithChildrenShortcut, activated=self.focus_search)
        toolbar.addWidget(self.query, 1)
        self.btn_search = big_button("Найти…", self.search, icon_name="scan-search")
        toolbar.addWidget(self.btn_search)
        layout.addLayout(toolbar)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.view = TextView()
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.view.picked.connect(self.edit)
        self.view.missed.connect(self.missed)
        body = QHBoxLayout()
        self.thumbs = QListWidget()
        self.thumbs.setAccessibleName("Миниатюры страниц")
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setIconSize(QSize(68, 76))
        self.thumbs.setGridSize(QSize(82, 108))
        self.thumbs.setFixedWidth(106)
        self.thumbs.currentRowChanged.connect(lambda row: self.page_num.setValue(row + 1) if row >= 0 else None)
        self.thumb_timer = QTimer(self, interval=15, singleShot=True)
        self.thumb_timer.timeout.connect(self.next_thumbnail)
        self.thumbs.verticalScrollBar().valueChanged.connect(lambda *_: self.thumb_timer.start())
        self.thumbs.viewport().installEventFilter(self)
        body.addWidget(self.thumbs)
        body.addWidget(self.view, 1)
        layout.addLayout(body, 1)
        footer = zoom_bar(self.view)
        footer.addWidget(QLabel("Страница"))
        self.page_num = QSpinBox()
        self.page_num.setAccessibleName("Номер страницы")
        self.page_num.setRange(1, 1)
        self.page_num.valueChanged.connect(self.go)
        footer.addWidget(self.page_num)
        self.btn_save = big_button("Сохранить копию…", self.save, primary=True, icon_name="save")
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)
        self.update_mode()
        self.reset_undo()

    def get_state(self):
        return tuple(self.edits)

    def set_state(self, edits):
        # Снимок исходника позволяет отменять правки и после перемещения файла на диске.
        doc = pdf.open("pdf", self.source)
        try:
            for edit in edits:
                editor.apply(doc[edit.page], edit)
            self.view.display(doc, self.number)
        except Exception:
            doc.close()
            raise
        for number in {edit.page for edit in (*self.edits, *edits)}:
            self.thumbs.item(number).setIcon(QIcon())
        self.thumb_timer.start()
        self.doc.close()
        self.doc = doc
        self.edits = list(edits)
        self.update_mode()

    def change(self, additions):
        if self.source is None:
            self.source = self.doc.tobytes()
        self.set_state(self.edits + additions)
        self.commit("правка текста")
        self.refresh_steps()

    def accept_files(self, paths):
        if len(paths) == 1:
            self.open(paths[0])
        elif paths:
            self.window().show_screen(self.window().pages)
            self.window().pages.accept_files(paths)

    def open(self, path, page_number=0, layout=None):
        if not path:
            return False
        if (same_path(self.path, path) and self.page_layout == layout and os.path.isfile(path)
                and editor.file_stamp(path) == self.stamp):
            self.page_num.setValue(page_number + 1)
            return True
        doc = try_open_pdf(self, path)
        if doc is None:
            return False
        if self.is_dirty() and QMessageBox.question(self, "Открыть другой PDF?",
                "Закрыть документ с несохранёнными изменениями?", QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            doc.close()
            return False
        try:
            editor.arrange(doc, layout)
            number = max(0, min(page_number, len(doc) - 1))
            stamp = editor.file_stamp(path)
            self.view.display(doc, number)
        except Exception as exc:
            doc.close()
            error(self, human_error(exc))
            return False
        if self.doc is not None:
            self.doc.close()
        self.path, self.doc, self.stamp, self.source = path, doc, stamp, None
        self.page_layout = layout
        self.number = number
        self.edits = []
        self.query.clear()
        self.reset_undo()
        self.dz.set_loaded(os.path.basename(path))
        with QSignalBlocker(self.thumbs):
            self.thumbs.clear()
            self.thumbs.addItems([str(i + 1) for i in range(len(doc))])
            self.thumbs.setCurrentRow(number)
        self.thumb_timer.start()
        with QSignalBlocker(self.page_num):
            self.page_num.setRange(1, len(doc))
            self.page_num.setSuffix(f" из {len(doc)}")
            self.page_num.setValue(number + 1)
        self.update_mode()
        return True

    def update_mode(self):
        ready = self.doc is not None
        self.editor.setVisible(ready)
        self.start.setVisible(not ready)
        for widget in (self.mode, self.query, self.btn_search, self.btn_save, self.page_num):
            widget.setEnabled(ready)
        key = self.mode.currentData()
        self.view.mode = key
        self.view.targets = []
        hint = MODES[key][1]
        if ready:
            page = self.view.page
            if key == "line":
                self.view.targets = editor.spans(page)
                if not self.view.targets:
                    hint = "На этой странице нет текстового слоя. Выберите «Исправить скан» и обведите текст."
            elif key == "field":
                self.view.targets = [editor.Target(tuple(w.rect), w.field_name) for w in page.widgets() or ()]
                if not self.view.targets:
                    hint = "На этой странице нет полей формы. Надпись можно добавить инструментом «Добавить текст»."
        self.hint.setText(hint if ready else "")
        self.view.outline()
        if ready:
            self.view.mark([edit.target.rect for edit in self.edits if edit.page == self.number])
        self.refresh_steps()

    def refresh_steps(self):
        # Шаги сверху обновляются по таймеру раз в 0,4 с; после открытия файла показываем сразу.
        if self.header.steps.items:
            self.header.steps.set_done(self.step_index())

    def missed(self):
        if self.view.mode == "line" and self.view.targets:
            self.hint.setText("Здесь нет строки текста. Щёлкните прямо по словам или нажмите Tab, "
                              "чтобы перебрать строки. Для новой надписи выберите «Добавить текст».")

    def place(self, dlg, target):
        """Окно правки — рядом со строкой, а не посреди экрана: видно, что именно меняем."""
        view = self.view
        box = view.mapFromScene(view.scene_rect(target.rect)).boundingRect()
        below = view.viewport().mapToGlobal(box.bottomLeft())
        above = view.viewport().mapToGlobal(box.topLeft())
        screen = (self.screen() or dlg.screen()).availableGeometry()
        size = dlg.sizeHint().expandedTo(dlg.geometry().size())   # dlg.size — поле «Размер» шрифта
        x = min(max(screen.left(), below.x() - 16), screen.right() - size.width())
        y = below.y() + 8
        if y + size.height() > screen.bottom():
            y = above.y() - size.height() - 8
        dlg.move(x, max(screen.top(), y))

    def edit(self, target):
        while target is not None:
            advance = 0
            try:
                self.view.outline(target.rect)
                dlg = EditDialog(self, target)
                self.place(dlg, target)
                dlg.exec()
                advance = dlg.advance if dlg.result() == QDialog.Accepted else 0
                dlg.deleteLater()
            except Exception as exc:
                error(self, human_error(exc))
            target = self.neighbour(target, advance) if advance else None
        self.view.current = None
        self.view.outline()

    def neighbour(self, target, delta):
        """Следующая строка после правки: список строк перестроен, ищем по положению."""
        order = reading_order(self.view.targets)
        if not order:
            return None
        here = pdf.Rect(target.rect)
        same = [t for t in order if pdf.Rect(t.rect).intersects(here)
                and abs(pdf.Rect(t.rect).y0 - here.y0) < max(1.0, here.height / 2)]
        if same:
            index = order.index(min(same, key=lambda t: abs(pdf.Rect(t.rect).x0 - here.x0)))
        else:
            index = next((i for i, t in enumerate(order) if reading_order([t, target])[0] is target), len(order))
            index -= 1 if delta > 0 else 0
        index += delta
        if not 0 <= index < len(order):
            return None
        self.view.current = order[index]
        return order[index]

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Show, QEvent.Resize):
            self.thumb_timer.start()
        return super().eventFilter(obj, event)

    def next_thumbnail(self):
        if self.doc is None:
            return
        for number in visible_rows(self.thumbs):
            item = self.thumbs.item(number)
            if item.icon().isNull():
                page = self.doc[number]
                dpi = max(1, min(24, int(110 * 72 / max(page.rect.width, page.rect.height))))
                item.setIcon(QIcon(page_pixmap(self.doc, number, dpi)))
                self.thumb_timer.start()
                break

    def go(self, value):
        self.number = value - 1
        with QSignalBlocker(self.thumbs):
            self.thumbs.setCurrentRow(self.number)
        self.view.display(self.doc, self.number)
        self.update_mode()

    def save(self):
        path = ask_save(self, safe_output_path(self.path, "текст"))
        if path:
            try:
                with progress_window(self, "Сохранение PDF…") as progress:
                    progress.run(editor.save, self.path, path, self.edits, self.stamp, self.page_layout)
                self.mark_saved()
                info(self, f"Сохранено в {path}")
            except Cancelled:
                pass
            except Exception as exc:
                error(self, human_error(exc))

    def focus_search(self):
        self.query.setFocus()
        self.query.selectAll()

    def search(self):
        term = self.query.text()
        if not term or self.doc is None:
            self.query.setFocus()
            return
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        matches = []
        progress = QProgressDialog("Поиск по страницам…", "Отмена", 0, len(self.doc), self)
        progress.setWindowModality(Qt.WindowModal)
        try:
            for page in self.doc:
                progress.setValue(page.number)
                if progress.wasCanceled():
                    return
                for t in editor.spans(page):
                    for match in pattern.finditer(t.text):
                        matches.append((page.number, t, match.span()))
        except Exception as exc:
            error(self, human_error(exc))
            return
        finally:
            progress.close()
            progress.deleteLater()
        if not matches:
            self.hint.setText("Совпадений в текстовом слое нет.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Совпадений в документе — {len(matches)}")
        dlg.resize(540, 350)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Выберите совпадение для перехода или отметьте нужные для замены."))
        rows = QListWidget()
        for num, target, (start, end) in matches:
            context = target.text[:start] + "【" + target.text[start:end] + "】" + target.text[end:]
            item = QListWidgetItem(f"Стр. {num+1} · {context}", rows)
            item.setCheckState(Qt.Unchecked)
        rows.setCurrentRow(0)
        layout.addWidget(rows)
        replacement = QLineEdit()
        replacement.setPlaceholderText("Заменить отмеченные совпадения на…")
        layout.addWidget(replacement)
        message = QLabel()
        message.setWordWrap(True)
        layout.addWidget(message)
        actions = QHBoxLayout()
        layout.addLayout(actions)

        def jump():
            num, target, _ = matches[rows.currentRow()]
            dlg.accept()
            self.page_num.setValue(num + 1)
            self.mode.setCurrentIndex(self.mode.findData("line"))
            self.edit(target)

        def replace():
            selected = [match for i, match in enumerate(matches) if rows.item(i).checkState() == Qt.Checked]
            if not selected:
                message.setText("Отметьте хотя бы одно совпадение.")
                return
            grouped = {}
            for num, target, span in selected:
                grouped.setdefault((num, target), []).append(span)
            changes = []
            for (num, target), ranges in grouped.items():
                value = target.text
                for start, end in reversed(ranges):
                    value = value[:start] + replacement.text() + value[end:]
                changes.append(editor.Edit(num, "line", target, value, size=target.size, color=target.color))
            try:
                self.change(changes)
            except Exception as exc:
                message.setText(human_error(exc))
                return
            dlg.accept()
            self.page_num.setValue(selected[0][0] + 1)

        actions.addWidget(big_button("Закрыть", dlg.reject))
        actions.addStretch()
        actions.addWidget(big_button("Перейти к строке", jump))
        actions.addWidget(big_button("Заменить отмеченные", replace, primary=True))
        rows.itemDoubleClicked.connect(lambda *_: jump())
        dlg.exec()
        dlg.deleteLater()

    def step_index(self):
        if self.doc is None:
            return 0
        if self.edits:
            return 2 if self.is_dirty() else 3
        return 1

    def tour_steps(self):
        return [Step(self.start.button, "Выбрать файл", "Откройте PDF для редактирования текста или скана."),
                Step(self.dz, "Открыть PDF", "Выберите документ или перетащите его сюда."),
                Step(self.mode, "Выбрать инструмент", "Щёлкните строку или поле формы. Для абзаца и скана обведите область."),
                Step(self.thumbs, "Страницы", "Выберите страницу по миниатюре или введите её номер внизу."),
                Step(self.view, "Проверить правку", "После применения правка видна на странице. Кнопка отмены вернёт предыдущий вариант."),
                Step(self.query, "Найти и заменить", "Введите текст и нажмите Enter. Отметьте совпадения для замены."),
                Step(self.btn_save, "Сохранить копию", "Сохраните результат под новым именем.")]
