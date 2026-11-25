"""«Текст»: распознавание сканов, клавиатура при правке строк, пути и цвет заплатки. Данные выдуманные."""
import os

import pymupdf as pdf
import pytest

import ocr_samples as samples
from app.core import ocr
from app.core import textedit as text
from app.core.pdfutil import PdfError


@pytest.mark.parametrize("condition, max_cer", [("clean_300", 0.02), ("skew_7", 0.05),
                                                ("shadow", 0.05), ("speckle", 0.15)])
def test_ocr_on_degraded_scans(condition, max_cer):
    """До предобработки: skew_7 — CER 0.10–0.18, тень — 0.06–0.11, пыль — 0.31–0.57."""
    with pdf.open() as doc:
        page, rect = samples.scan_page(doc, condition=condition)
        result = text.recognize(page, rect)
    reference = "\n".join(samples.LINES)
    assert samples.cer(reference, result) <= max_cer, result
    assert samples.line_order_ok(samples.LINES, result), result


@pytest.mark.parametrize("rotation", [0, 90])
def test_ocr_lines_keep_positions(rotation):
    with pdf.open() as doc:
        page, rect = samples.scan_page(doc, condition="skew_3", rotation=rotation)
        lines = ocr.recognize_lines(page, rect)
    assert len(lines) == len(samples.LINES)
    area = pdf.Rect(rect) + (-2, -2, 2, 2)
    centres = []
    for line in lines:
        box = pdf.Rect(line.rect)
        assert area.contains(box), (line.text, box, rect)
        centres.append((box.y0 + box.y1) / 2 if rotation == 0 else (box.x0 + box.x1) / 2)
        assert line.height > 4
    # Строки идут сверху вниз (на повёрнутой странице — вдоль страницы) и не налезают друг на друга.
    assert centres == sorted(centres) or centres == sorted(centres, reverse=True)
    assert min(abs(a - b) for a, b in zip(centres, centres[1:])) > 8


def test_scan_patch_takes_paper_colour():
    paper = (0.93, 0.89, 0.78)
    with pdf.open() as doc:
        page = doc.new_page(width=300, height=160)
        page.draw_rect(page.rect, color=None, fill=paper)
        page.insert_text((30, 70), "Квитанция № 15", fontsize=20)
        page.insert_image(page.rect, stream=page.get_pixmap(dpi=144).tobytes("png"))
        page.add_redact_annot(page.rect)
        page.apply_redactions(images=0, graphics=2, text=0)     # остаётся только картинка — «скан»
        target = text.Target((25, 45, 200, 80))
        text.apply(page, text.Edit(0, "scan", target, ""))
        r, g, b = page.get_pixmap(clip=(100, 60, 102, 62)).pixel(0, 0)
    assert abs(r / 255 - paper[0]) < .03 and abs(g / 255 - paper[1]) < .03 and abs(b / 255 - paper[2]) < .03


def _sheet(path):
    with pdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        for y, value in [(40, "Акт № 3"), (80, "Иванова Т. С."), (80, "кв. 12"), (120, "Итого 500")]:
            page.insert_text((20 if value != "кв. 12" else 220, y), value, fontsize=12,
                             fontfile=samples.INTER, fontname="inter")
        doc.save(path)


def when_dialog(action, previous=None):
    """Выполнить action, как только откроется (новое) окно правки."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.ui.tab_text import EditDialog
    timer = QTimer(interval=10)

    def poll():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, EditDialog) and dialog is not previous and dialog.isVisible():
            timer.stop()
            action(dialog)
    timer.timeout.connect(poll)
    timer.start()
    return timer


def test_tab_enter_escape_and_marks(win, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    path = str(tmp_path / "лист.pdf")
    _sheet(path)
    tab = win.text
    win.show_screen(tab)
    assert tab.open(path)
    view = tab.view
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Tab)
    assert view.current.text == "Акт № 3"
    QTest.keyClick(view, Qt.Key_Tab)
    QTest.keyClick(view, Qt.Key_Tab)
    assert view.current.text == "кв. 12"            # в строке — слева направо
    QTest.keyClick(view, Qt.Key_Backtab)
    assert view.current.text == "Иванова Т. С."

    seen = []

    timers = []

    def type_and_tab(dialog):
        seen.append(dialog.input.toPlainText())
        if len(seen) == 1:
            dialog.input.setPlainText("Петрова Т. С.")
            timers.append(when_dialog(type_and_tab, dialog))
            QTest.keyClick(dialog.input, Qt.Key_Tab)          # применить и к следующей строке
        elif len(seen) == 2:
            dialog.input.setPlainText("кв. 14")
            QTest.keyClick(dialog.input, Qt.Key_Return)       # применить и закрыть

    timers.append(when_dialog(type_and_tab))
    QTest.keyClick(view, Qt.Key_Return)
    assert seen == ["Иванова Т. С.", "кв. 12"]
    words = tab.doc[0].get_text()
    assert "Петрова Т. С." in words and "кв. 14" in words and "Иванова" not in words
    assert len(view.marks) == 2 and len(tab.edits) == 2

    def escape(dialog):
        dialog.input.setPlainText("не применять")
        QTest.keyClick(dialog.input, Qt.Key_Escape)

    timers.append(when_dialog(escape))
    tab.edit(view.targets[0])
    assert "не применять" not in tab.doc[0].get_text() and len(tab.edits) == 2
    win.undo()
    assert len(tab.edits) == 1 and len(view.marks) == 1
    win.redo()
    assert len(tab.edits) == 2


def test_click_outside_text_explains(win, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    path = str(tmp_path / "лист.pdf")
    _sheet(path)
    tab = win.text
    win.show_screen(tab)
    tab.open(path)
    w, h = tab.view._size()
    pos = tab.view.mapFromScene(w * 0.9, h * 0.9)
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, pos=pos)
    assert "нет строки текста" in tab.hint.text()


def test_same_file_other_spelling_keeps_edits(win, tmp_path, monkeypatch):
    from app.ui import tab_text
    path = str(tmp_path / "лист.pdf")
    _sheet(path)
    tab = win.text
    assert tab.open(path)
    tab.change([text.Edit(0, "line", tab.view.targets[0], "Акт № 4")])
    asked = []
    monkeypatch.setattr(tab_text.QMessageBox, "question", lambda *a: asked.append(a) or tab_text.QMessageBox.No)
    variant = os.path.join(str(tmp_path), ".", "лист.pdf")
    if os.name == "nt":
        variant = variant.replace("\\", "/").upper()
    assert tab.open(variant, 0)
    assert not asked and len(tab.edits) == 1
    assert tab_text.same_path(path, os.path.join(str(tmp_path), "x", "..", "лист.pdf"))
    assert not tab_text.same_path(path, str(tmp_path / "другой.pdf")) and not tab_text.same_path("", path)


def test_vanished_field_gives_plain_message(win, tmp_path):
    from app.ui import tab_text
    path = str(tmp_path / "форма.pdf")
    with pdf.open() as doc:
        page = doc.new_page()
        field = pdf.Widget()
        field.field_name, field.field_type = "Квартира", pdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pdf.Rect(20, 20, 200, 44)
        page.add_widget(field)
        doc.save(path)
    tab = win.text
    tab.open(path)
    tab.mode.setCurrentIndex(tab.mode.findData("field"))
    with pytest.raises(PdfError, match="больше не найдено"):
        tab_text.EditDialog(tab, text.Target((1, 1, 5, 5), "Нет такого"))
