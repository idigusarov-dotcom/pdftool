import os

import pymupdf as pdf
import pytest

from app.core import textedit as text
from app.core.pdfutil import PdfError


def contract(path):
    with pdf.open() as doc:
        page = doc.new_page(width=400, height=270)
        for y, value in [(30, "01.01.2025"), (60, "Ivanov"), (100, "125.00")]:
            page.insert_text((20, y), value, fontsize=12)
        page.draw_rect((12, 78, 95, 112), color=(0, 0, 0))
        page.insert_text((105, 100), "USD")
        doc.new_page(width=270, height=400).insert_text((25, 35), "Invoice")
        doc[1].set_rotation(90)
        doc.save(path)


def test_contract_roundtrip_and_overflow(tmp_path):
    source, out = map(str, (tmp_path / "original.pdf", tmp_path / "edited.pdf"))
    contract(source)
    original = open(source, "rb").read()
    with pdf.open(source) as doc:
        page = doc[0]
        date, name, amount, label = text.spans(page)
        with pytest.raises(PdfError, match="помещается"):
            text.apply(page, text.Edit(0, "line", amount, "9" * 18))
        assert text.spans(page) == [date, name, amount, label]
        edits = [text.Edit(0, "line", date, "25.11.2026"),
                 text.Edit(0, "line", name, "Александрова-Знаменская"),
                 text.Edit(0, "line", amount, "350.00")]
        reference = page.get_pixmap(clip=(100, 75, 200, 115)).samples
    text.save(source, out, edits, text.file_stamp(source))
    with pdf.open(out) as doc:
        p = doc[0]
        assert [p.search_for(v) != [] for v in ("25.11.2026", "Александрова-Знаменская", "350.00")] == [True] * 3
        assert not any(p.search_for(v) for v in ("01.01.2025", "Ivanov", "125.00"))
        assert len(p.get_drawings()) == 1
        assert p.get_pixmap(clip=(100, 75, 200, 115)).samples == reference
        assert doc[1].rotation == 90 and doc[1].get_text().strip() == "Invoice"
    assert open(source, "rb").read() == original
    with pytest.raises(PdfError, match="поверх исходного"):
        text.save(source, source, edits, text.file_stamp(source))


def test_offline_ocr_scanned_image_and_wrapped_block(tmp_path):
    with pdf.open() as original, pdf.open() as doc:
        page = original.new_page(width=380, height=190)
        t = text.Target((15, 12, 340, 75))
        text.apply(page, text.Edit(0, "add", t, "Проверка текста", size=22))
        page.draw_rect((0, 100, 370, 180), fill=(0.35, 0.3, 0.2))
        scan = doc.new_page(width=380, height=190)
        scan.insert_image(scan.rect, stream=page.get_pixmap(dpi=144).tobytes("png"))
        before = scan.get_pixmap(clip=(0, 98, 320, 182)).samples
        assert text.recognize(scan, t.rect).strip() == "Проверка текста"
        text.apply(scan, text.Edit(0, "scan", t, "Исправлено"))
        assert scan.get_text().strip() == "Исправлено"
        assert scan.get_pixmap(clip=(0, 98, 320, 182)).samples == before
        new = "Это абзац с переносом на несколько строк."
        text.apply(scan, text.Edit(0, "block", text.Target((12, 10, 156, 90)), new))
        assert " ".join(scan.get_text().split()) == new
        assert len(scan.get_text().splitlines()) > 1


def test_widget_and_source_changed(tmp_path):
    source, out = str(tmp_path / "form.pdf"), str(tmp_path / "out.pdf")
    with pdf.open() as doc:
        p = doc.new_page()
        w = pdf.Widget()
        w.field_name = "Name"
        w.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pdf.Rect(22, 15, 235, 42)
        p.add_widget(w)
        doc.save(source)
    stamp = text.file_stamp(source)
    edits = [text.Edit(0, "field", text.Target(tuple(w.rect), "Name"), "Example")]
    text.save(source, out, edits, stamp)
    with pdf.open(out) as doc:
        p = doc[0]
        assert next(p.widgets()).field_value == "Example"
    os.utime(source, ns=(stamp[-1], stamp[-1] + 10**9))
    with pytest.raises(PdfError, match="изменился"):
        text.save(source, out, edits, stamp)


def test_click_rotated_page_edit_undo_search_and_save(win, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QListWidget, QPushButton
    from app.ui import tab_text

    source, out = map(str, (tmp_path / "generic.pdf", tmp_path / "copy.pdf"))
    contract(source)
    assert win.current() is win.pages
    win.open_files([source])
    win.pages.grid.setCurrentRow(1)
    win.pages.btn_text.click()
    tab = win.text
    assert win.current() is tab and tab.number == 1
    target = tab.view.targets[0]
    box = pdf.Rect(target.rect) * tab.view.page.rotation_matrix
    w, h = tab.view._size()
    pos = tab.view.mapFromScene((box.x0 + box.x1) / 2 * w / tab.view.page.rect.width,
                                (box.y0 + box.y1) / 2 * h / tab.view.page.rect.height)

    def fill():
        dialog = QApplication.activeModalWidget()
        dialog.input.setPlainText("Счёт")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    QTimer.singleShot(0, fill)
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, pos=pos)
    assert tab.doc[1].get_text().strip() == "Счёт"
    win.undo()
    assert tab.doc[1].get_text().strip() == "Invoice"
    win.redo()
    assert tab.doc[1].get_text().strip() == "Счёт"

    def replace():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QListWidget).item(0).setCheckState(Qt.Checked)
        dialog.findChild(QLineEdit).setText("Итог")
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Заменить отмеченные").click()

    tab.query.setText("Счёт")
    QTimer.singleShot(0, replace)
    tab.btn_search.click()
    assert tab.doc[1].get_text().strip() == "Итог"
    monkeypatch.setattr(tab_text, "ask_save", lambda *_: out)
    tab.btn_save.click()
    assert not tab.is_dirty()
    with pdf.open(out) as result:
        assert result[1].get_text().strip() == "Итог"


def test_rotated_region_custom_font_and_checked_matches(win, tmp_path):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QPushButton
    from app.core.resources import resource

    source = str(tmp_path / "notes.pdf")
    with pdf.open() as doc:
        for label in ("OLD | OLD", "OLD"):
            doc.new_page().insert_text((30, 50), label)
        doc[1].set_rotation(90)
        doc.save(source)

    tab = win.text
    win.edit_document(source, 0)
    tab.query.setText("old")

    def select_matches():
        dialog = QApplication.activeModalWidget()
        rows = dialog.findChild(QListWidget)
        assert rows.count() == 3
        for row in (1, 2):
            rows.item(row).setCheckState(Qt.Checked)
        dialog.findChild(QLineEdit).setText("NEW")
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Заменить отмеченные").click()

    QTimer.singleShot(0, select_matches)
    tab.search()
    assert tab.doc[0].get_text().strip() == "OLD | NEW"
    assert tab.doc[1].get_text().strip() == "NEW"
    # Если последняя из нескольких правок не помещается, откатывается вся операция.
    first, second = (text.spans(tab.doc[i])[0] for i in (0, 1))
    with pytest.raises(PdfError, match="помещается"):
        tab.change([text.Edit(0, "line", first, "one"), text.Edit(1, "line", second, "two" * 1000)])
    assert tab.doc[0].get_text().strip() == "OLD | NEW"

    with open(resource("ui", "assets", "fonts", "Inter-Medium.ttf"), "rb") as stream:
        data = stream.read()
    page = tab.doc[1]
    area = pdf.Rect(70, 95, 310, 138) * page.derotation_matrix
    tab.change([text.Edit(1, "add", text.Target(tuple(area)), "Новый заголовок", font_data=data)])
    page = tab.doc[1]
    block = page.get_text("dict")["blocks"][-1]
    line = block["lines"][0]
    assert line["dir"] == (0, -1)
    assert "Medium" in line["spans"][0]["font"]
    assert line["spans"][0]["text"] == "Новый заголовок"


def test_handoff_keeps_page_changes(win, tmp_path, monkeypatch):
    from app.ui import tab_text
    source, out = map(str, (tmp_path / "pages.pdf", tmp_path / "short.pdf"))
    contract(source)
    win.open_files([source])
    pages = win.pages
    pages.grid.setCurrentRow(0)
    pages.delete()
    pages.grid.setCurrentRow(0)
    pages.rotate(90)
    pages.btn_text.click()
    tab = win.text
    assert len(tab.doc) == 1 and tab.doc[0].rotation == 180
    target = text.spans(tab.doc[0])[0]
    tab.change([text.Edit(0, "line", target, "Report")])
    monkeypatch.setattr(tab_text, "ask_save", lambda *_: out)
    tab.btn_save.click()
    with pdf.open(out) as doc:
        assert len(doc) == 1 and doc[0].rotation == 180
        assert doc[0].get_text().strip() == "Report"
