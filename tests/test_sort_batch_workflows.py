import csv
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from app.core import batch
from app.core.pdfutil import NRect
from app.ui.tab_batch import PairFilesDialog
from e2e_lib import app, wait_until


def make_pdf(path, labels):
    font = Path(__file__).resolve().parents[1] / "app/ui/assets/fonts/Inter-Regular.ttf"
    with pymupdf.open() as doc:
        for index, label in enumerate(labels):
            page = doc.new_page()
            page.insert_text((50, 50), f"ID{index} {label}", fontname="Inter", fontfile=str(font))
        doc.save(path)
    return str(path)


def test_sort_marks_and_exports_same_page_order(win, tmp_path):
    path = make_pdf(tmp_path / "Отчёт.pdf", ["кв. 12А", "квартира: 2", "", "кв. 012а", ""])
    s = win.sort
    win.show_screen(s)
    s.open(path)
    assert s.values_by_page() == ["12А", "2", None, "012а", None]
    s.table.item(2, 1).setText("Раздел 10")
    s.table.setCurrentCell(1, 1)
    s.direction.setCurrentIndex(1)
    assert s.table.item(s.table.currentRow(), 0).data(Qt.UserRole) == 1
    assert s.issue_pages == {0, 3, 4}
    s.btn_next.click()
    assert s.table.item(s.table.currentRow(), 0).data(Qt.UserRole) == 4

    s.unsure.add(1)
    s.refresh_marks()
    s.commit("распознан номер")
    s.table.setCurrentCell(3, 1)
    assert s.btn_confirm.isEnabled()
    s.btn_confirm.click()
    assert 1 not in s.issue_pages
    s.undo_stack.undo()
    assert 1 in s.issue_pages

    out_pdf, out_csv = tmp_path / "Результат.pdf", tmp_path / "Список.csv"
    s.save(str(out_pdf))
    s.save_csv(str(out_csv))
    with pymupdf.open(out_pdf) as doc:
        assert [page.get_text().split()[0] for page in doc] == ["ID2", "ID0", "ID3", "ID1", "ID4"]
    with out_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))[1:]
    assert [int(row[2]) - 1 for row in rows] == [2, 0, 3, 1, 4]
    assert [row[3] for row in rows] == ["", "да", "да", "", ""]
    s.table.item(4, 1).setText("Раздел 2")
    s.direction.setCurrentIndex(0)
    assert [s.table.item(i, 1).text() for i in range(5)] == ["2", "12А", "012а", "Раздел 2", "Раздел 10"]


def test_replacing_unsaved_sort_can_be_cancelled(win, tmp_path, monkeypatch):
    original = make_pdf(tmp_path / "Исходник.pdf", ["1"])
    other = make_pdf(tmp_path / "Другой.pdf", ["2"])
    s = win.sort
    s.open(original)
    s.table.item(0, 1).setText("Раздел 1")
    monkeypatch.setattr(QMessageBox, "exec", lambda box: QMessageBox.Cancel)
    s.open(other)
    assert s.path == original and s.values_by_page() == ["Раздел 1"]
    monkeypatch.setattr(QMessageBox, "exec", lambda box: QMessageBox.Discard)
    s.open(other)
    assert s.path == other and not s.is_dirty()


def test_import_odd_file_count_and_edit_pairs(win, tmp_path, monkeypatch):
    b = win.batch
    names = [str(tmp_path / f"{name}.pdf") for name in ["Расписки", "Акты", "Контракт", "Приложение"]]
    monkeypatch.setattr(PairFilesDialog, "exec", lambda dialog: QDialog.Rejected)
    b.accept_files(names[:3])
    assert not b.items

    def arrange(dialog):
        assert not dialog.ok.isEnabled()
        dialog.append_files(names[3:])
        assert dialog.ok.isEnabled()
        dialog.files.setCurrentRow(3)
        dialog.up.click()
        assert dialog.pairs() == [(names[1], names[0]), (names[3], names[2])]
        return QDialog.Accepted

    monkeypatch.setattr(PairFilesDialog, "exec", arrange)
    b.accept_files(names[:3])
    assert [(it.acts_path, it.receipts_path) for it in b.items] == [(names[1], names[0]), (names[3], names[2])]
    b.undo_stack.undo()
    assert not b.items


def test_batch_review_selected_pair_and_resume(win, tmp_path, monkeypatch):
    acts = make_pdf(tmp_path / "Акты.pdf", ["1"])
    receipts = make_pdf(tmp_path / "Расписки.pdf", ["2"])
    first, second = tmp_path / "Первый.pdf", tmp_path / "Второй.pdf"
    b = win.batch
    win.show_screen(b)
    b.add_pair(acts, receipts, out=str(first))
    b.btn_run.click()
    assert wait_until(app, lambda: not b.running)
    assert b.last_results[0]["status"] == "ok"
    b._summary.close()
    original = first.read_bytes(), first.stat().st_mtime_ns

    b.add_pair(acts, receipts, out=str(second))
    b.btn_review.click()
    s = win.sign
    s.job.src_rect = NRect(.1, .1, .4, .2)
    s.job.dst_rect = NRect(.2, .2, .5, .3)
    s.job.src_overrides = {0: NRect(.1, .1, .3, .2)}
    s.job.ink_mode, s.job.sensitivity, s.job.uniform_color = "black", .75, True
    expected = batch.ReviewedSettings.from_job(s.job)
    win.show_screen(b)
    b.table.setCurrentCell(0, 1)
    assert not b.btn_take.isEnabled()
    b.table.setCurrentCell(1, 1)
    b.btn_take.click()
    assert b.items[0].reviewed_settings is None and b.items[1].reviewed_settings == expected
    b.undo_stack.undo()
    b.undo_stack.redo()
    restored = batch.build_job(b.items[1], ())
    assert batch.ReviewedSettings.from_job(restored) == expected
    assert isinstance(restored.src_overrides[0], NRect)
    assert b.remaining_rows() == [1]
    b.btn_retry.click()
    assert wait_until(app, lambda: not b.running)
    assert [r["status"] for r in b.last_results] == ["ok", "ok"]
    assert (first.read_bytes(), first.stat().st_mtime_ns) == original
    with pymupdf.open(second) as doc:
        assert len(doc) == 1
    b._summary.close()

    # Закрытие диалога замены должно оставить существующие файлы и итоги.
    monkeypatch.setattr(QMessageBox, "exec", lambda box: QMessageBox.Cancel)
    b.btn_run.click()
    assert not b.running and not b.remaining_rows()
    assert (first.read_bytes(), first.stat().st_mtime_ns) == original


def test_batch_compress_independent_pdfs(win, tmp_path, monkeypatch):
    import io
    from PIL import Image
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QTextEdit

    scan = tmp_path / "Скан.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        picture = io.BytesIO()
        Image.effect_noise((700, 700), 40).convert("RGB").save(picture, format="PNG")
        page.insert_image(page.rect, stream=picture.getvalue())
        doc.save(scan)
    text_only = make_pdf(tmp_path / "Текст.pdf", ["Раздел"])
    existing = tmp_path / "Скан_сжатый.pdf"
    existing.write_bytes(b"previous result")
    original = scan.read_bytes()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([text_only, str(scan)], ""))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("Среднее", True))
    win.show_screen(win.batch)
    win.batch.btn_compress.click()
    report = next(d for d in win.batch.findChildren(QDialog) if d.windowTitle() == "Результаты сжатия")
    details = report.findChild(QTextEdit).toPlainText()
    assert "нет сканов" in details and "Скан_сжатый_2.pdf" in details
    with pymupdf.open(tmp_path / "Скан_сжатый_2.pdf") as result:
        assert len(result) == 1 and len(result[0].get_images()) == 1
    assert scan.read_bytes() == original and existing.read_bytes() == b"previous result"
    report.close()
