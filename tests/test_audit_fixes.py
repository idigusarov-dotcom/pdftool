"""Исправления после проверки 1.4.0-beta1: уведомления не закрывают кнопки, повторы страниц, проверка файлов."""
import os
import sys
import tempfile

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestAudit"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
FONT = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "assets", "fonts", "Inter-Regular.ttf")


def _pdf(path, n=3, password=None):
    d = pymupdf.open()
    for i in range(n):
        d.new_page().insert_text((72, 100), f"Акт кв. № {i + 1}", fontfile=FONT, fontname="inter", fontsize=14)
    if password:
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        d.save(path)
    d.close()
    return path


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", tempfile.mkdtemp())
    from app.main import MainWindow, setup_app
    from app.ui.infra import settings
    settings().clear()
    for k in ("sign", "batch", "fragment", "sort", "image", "pages"):
        settings().setValue(f"tour/{k}", True)
    setup_app(app)
    w = MainWindow()
    w.resize(1280, 800)
    w.show()
    app.processEvents()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


def test_ranges_accept_all_dashes():
    from app.core.pages import parse_ranges
    for t in ("1-3", "1–3", "1—3", "1−3", "1 - 3"):
        assert parse_ranges(t, 5) == [[0, 1, 2]], t


def test_image_same_page_once(win, tmp_path):
    pdf = _pdf(str(tmp_path / "акт.pdf"), 4)
    png = str(tmp_path / "печать.png")
    Image.new("RGB", (60, 60), (20, 40, 200)).save(png)
    win.image.open_pdf(pdf)
    win.image.open_image(png)
    win.image.r_sel.setChecked(True)
    win.image.ed_pages.setText("1-3, 2")
    assert win.image.target_pages() == [0, 1, 2]
    out = str(tmp_path / "с печатью.pdf")
    win.image.save(out)
    with pymupdf.open(out) as d:
        assert [len(p.get_image_info()) for p in d] == [1, 1, 1, 0]


def test_merge_list_rejects_bad_files(win, tmp_path):
    good = _pdf(str(tmp_path / "акты.pdf"))
    locked = _pdf(str(tmp_path / "с паролем.pdf"), password="123")
    broken = str(tmp_path / "битый.pdf")
    open(broken, "wb").write(b"not a pdf")
    win.pages.add_files([good, locked, broken])
    assert win.pages.merge_list.count() == 1


def test_toasts_do_not_cover_save_button(win, tmp_path):
    win.show_screen(win.batch)
    app.processEvents()
    for i in range(5):
        win.toast(f"Уведомление {i}", "info")
    app.processEvents()
    assert len(win._toasts) == 3
    host = win.centralWidget()
    btn = win.batch.btn_run
    top = btn.mapTo(host, btn.rect().topLeft()).y()
    assert all(t.geometry().bottom() < top for t in win._toasts)
    win.show_screen(win.pages)            # другой экран — старые уведомления убираются
    app.processEvents()
    assert win._toasts == []


def test_sign_warns_same_file(win, tmp_path):
    pdf = _pdf(str(tmp_path / "акты.pdf"))
    got = []
    orig = win.toast
    win.toast = lambda text, kind="info", **k: (got.append((kind, text)), orig(text, kind, **k))[1]
    win.sign.open_acts(pdf)
    win.sign.open_receipts(pdf)
    assert any(k == "error" and "один и тот же файл" in t for k, t in got)
