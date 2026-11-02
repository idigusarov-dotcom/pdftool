"""Проверки интерфейса без экрана: отмена, ленивые миниатюры, скорость открытия."""
import os

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTest"
import tempfile  # noqa: E402

_TPL = tempfile.mkdtemp(prefix="pdftool_ui_tpl_")
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

DATA = os.environ.get("PDFTOOL_DATA", "/workspace")
JOB4 = f"{DATA}/job4/input.pdf"
JOB2A, JOB2B = f"{DATA}/job2/fileA_3.0MB.pdf", f"{DATA}/job2/fileB_3.5MB.pdf"
app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", _TPL)
    from app.main import MainWindow
    from app.ui.infra import settings
    settings().clear()
    w = MainWindow()
    w.show()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


@pytest.mark.skipif(not os.path.exists(JOB4), reason="нет job4")
def test_open_89_pages_fast(win):
    t0 = time.perf_counter()
    win.tabs.setCurrentIndex(1)
    win.sort.open(JOB4)
    app.processEvents()
    dt = time.perf_counter() - t0
    print(f"\nоткрытие 89 стр. (сортировка): {dt:.2f} c")
    assert win.sort.table.rowCount() == 89
    assert dt < 5
    t0 = time.perf_counter()
    win.pages.open(JOB4)
    app.processEvents()
    print(f"открытие 89 стр. (страницы): {time.perf_counter() - t0:.2f} c")
    t1 = time.perf_counter()
    while len(win.sort._icons) < 5 and time.perf_counter() - t1 < 20:
        app.processEvents()
        time.sleep(0.02)
    assert len(win.sort._icons) >= 5          # видимые миниатюры дорисовались в фоне
    assert len(win.sort._icons) < 89          # а не все сразу


@pytest.mark.skipif(not os.path.exists(JOB2A), reason="нет job2")
def test_undo_sort_and_pages(win):
    win.sort.open(JOB2A)
    win.sort.table.item(0, 1).setText("12")
    win.sort.table.item(1, 1).setText("12")
    assert win.sort.is_dirty()
    assert win.sort.values_by_page()[1] == "12"
    win.sort.undo_stack.undo()
    assert win.sort.values_by_page()[1] is None
    win.sort.undo_stack.redo()
    assert win.sort.values_by_page()[1] == "12"

    win.pages.open(JOB2B)
    win.pages.grid.item(0).setSelected(True)
    win.pages.delete()
    assert win.pages.grid.count() == 7
    win.pages.undo_stack.undo()
    assert win.pages.grid.count() == 8


@pytest.mark.skipif(not os.path.exists(JOB2A), reason="нет job2")
def test_sign_mapping_undo_and_ink_preview(win):
    win.sign.open_acts(JOB2B)
    win.sign.open_receipts(JOB2A)
    win.sign.set_mapping([3, 4, 7, 6, None, 0, 2])
    assert win.sign.job.mapping[4] is None
    win.sign.undo_stack.undo()
    assert win.sign.job.mapping[6] == 6          # вернулось к «по порядку»
    win.sign.cb_mode.setCurrentIndex(win.sign.cb_mode.findData("black"))
    win.sign.update_ink_preview()
    assert win.sign.job.ink_mode == "black"
    assert win.sign.lbl_ink.pixmap() is not None


def test_zoom(win):
    v = win.image.view
    from PySide6.QtGui import QPixmap
    v.set_page(QPixmap(200, 300))
    s0 = v.transform().m11()
    v.zoom_in()
    assert v.transform().m11() > s0
    v.fit()


@pytest.mark.skipif(not os.path.exists(JOB2A), reason="нет job2")
def test_template_bar_and_batch_ui(win, tmp_path):
    from PySide6.QtCore import QThreadPool
    from app.core import templates as tp
    from app.core.pdfutil import NRect
    s = win.sign
    s.open_acts(JOB2B)
    s.open_receipts(JOB2A)
    s.zone_act = NRect(0.255, 0.335, 0.37, 0.38)
    s.sl_sens.setValue(70)
    t = s.tbar.save("Акт тест")
    assert t.fp_act and t.fp_rec and abs(t.sensitivity - 0.7) < 1e-6
    assert win.sort.tbar.cb.findText("Акт тест") > 0          # другие вкладки видят новый шаблон
    s.sl_sens.setValue(20)
    s.zone_act = NRect(0, 0, 0.1, 0.1)
    s.apply_template(tp.get("Акт тест"))
    assert s.sl_sens.value() == 70 and s.zone_act == NRect(0.255, 0.335, 0.37, 0.38)
    # пакет: две пары; вторая — с исправленным во вкладке сопоставлением
    b = win.batch
    b.add_pair(JOB2B, JOB2A, "Акт тест", str(tmp_path / "p1.pdf"))
    b.add_pair(JOB2B, JOB2A, None, str(tmp_path / "p2.pdf"))
    b.table.setCurrentCell(1, 0)
    b.review()
    assert win.tabs.currentWidget() is s
    s.set_mapping([3, 4, 7, 6, None, 0, 2])
    b.take_mapping()
    assert b.items[1].mapping == [3, 4, 7, 6, None, 0, 2]
    b.undo_stack.undo()
    assert b.items[1].mapping is None
    b.undo_stack.redo()
    b.run_all()
    for _ in range(600):
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(50)
        if b.last_results:
            break
    app.processEvents()
    assert [r["status"] for r in b.last_results] == ["ok", "ok"]
    assert b.last_results[1]["placed"] == 6
    assert "Обработано пар: 2 из 2" in b.summary_text(b.last_results)
    assert os.path.exists(tmp_path / "p1.pdf") and os.path.exists(tmp_path / "p2.pdf")
    b._summary.close()
