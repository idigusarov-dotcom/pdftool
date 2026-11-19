"""Новое в 1.4.0-beta2: предупреждение «проверьте рамки» в «Пакете», окно хода долгих сохранений, сжатие PDF."""
import os
import sys
import tempfile

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestNew"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)


def _scan_pdf(path, n=3, dpi=300, alpha_page=False):
    """PDF «как со сканера»: на каждой странице A4 — фото-подобная картинка с шумом и текстом."""
    rng = np.random.default_rng(1)
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    d = pymupdf.open()
    for i in range(n):
        arr = (235 + rng.integers(-20, 20, (h // 4, w // 4, 3))).clip(0, 255).astype("uint8")
        arr[100:140, 60:500] = (20, 20, 120)                     # «строка текста»
        im = Image.fromarray(arr).resize((w, h))
        import io
        b = io.BytesIO()
        im.save(b, "JPEG", quality=95)
        p = d.new_page(width=595, height=842)
        p.insert_image(p.rect, stream=b.getvalue())
    if alpha_page:
        p = d.new_page(width=595, height=842)
        rgba = Image.new("RGBA", (600, 600), (200, 0, 0, 128))
        b = io.BytesIO()
        rgba.save(b, "PNG")
        p.insert_image(pymupdf.Rect(50, 50, 300, 300), stream=b.getvalue())
    d.save(path)
    d.close()
    return path


# ---------- сжатие: ядро ----------
@pytest.mark.parametrize("level", ["strong", "medium", "light"])
def test_compress_shrinks_and_keeps_source(tmp_path, level):
    from app.core import compress as cz
    src = _scan_pdf(str(tmp_path / "скан.pdf"))
    before = open(src, "rb").read()
    out = str(tmp_path / "сжатый.pdf")
    calls = []
    rep = cz.compress(src, out, level, progress=lambda i, n: calls.append((i, n)))
    assert open(src, "rb").read() == before                     # исходник не тронут
    assert rep["before"] == len(before) and rep["after"] == os.path.getsize(out)
    assert rep["after"] < rep["before"] * 0.8
    assert calls and calls[-1][0] == calls[-1][1]
    with pymupdf.open(out) as d:
        assert d.page_count == 3
        pix = d[0].get_pixmap(dpi=50)                          # открывается и рисуется
        assert pix.width > 0
        txt_px = d[0].get_pixmap(dpi=72, clip=pymupdf.Rect(100, 105, 300, 125))
        assert max(txt_px.samples[0::3]) < 120                  # синяя «строка» на месте


def test_compress_levels_ordered(tmp_path):
    from app.core import compress as cz
    src = _scan_pdf(str(tmp_path / "скан.pdf"), n=2)
    sizes = {lv: cz.compress(src, str(tmp_path / f"{lv}.pdf"), lv)["after"] for lv in cz.LEVELS}
    assert sizes["strong"] < sizes["medium"] < sizes["light"]


def test_compress_keeps_transparent_images(tmp_path):
    from app.core import compress as cz
    src = _scan_pdf(str(tmp_path / "скан.pdf"), n=1, alpha_page=True)
    out = str(tmp_path / "out.pdf")
    cz.compress(src, out, "strong")
    with pymupdf.open(out) as d:
        imgs = d[1].get_images(full=True)
        assert imgs and imgs[0][1] != 0                          # маска прозрачности осталась


def test_compress_vector_pdf_says_nothing_to_do(tmp_path):
    from app.core import compress as cz
    from app.core.pdfutil import PdfError
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "text")
    src = str(tmp_path / "v.pdf")
    d.save(src)
    with pytest.raises(PdfError, match="сжимать нечего"):
        cz.compress(src, str(tmp_path / "o.pdf"))
    assert not os.path.exists(tmp_path / "o.pdf")


def test_compress_cancel_writes_nothing(tmp_path):
    from app.core import compress as cz
    from app.core.pdfutil import Cancelled
    src = _scan_pdf(str(tmp_path / "скан.pdf"), n=2)
    out = str(tmp_path / "o.pdf")

    def prog(i, n):
        if i >= 1:
            raise Cancelled()
    with pytest.raises(Cancelled):
        cz.compress(src, out, progress=prog)
    assert not os.path.exists(out)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_compress_refuses_overwriting_source(tmp_path):
    from app.core import compress as cz
    from app.core.pdfutil import PdfError
    src = _scan_pdf(str(tmp_path / "скан.pdf"), n=1)
    before = open(src, "rb").read()
    with pytest.raises(PdfError):
        cz.compress(src, src)
    assert open(src, "rb").read() == before


def test_human_size():
    from app.core.compress import human_size
    assert human_size(700 * 1024) == "700 КБ"
    assert human_size(int(8.8 * 1024 * 1024)) == "8,8 МБ"


# ---------- окно программы ----------
@pytest.fixture
def win(monkeypatch):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", tempfile.mkdtemp())
    from app.main import MainWindow, setup_app
    from app.ui.infra import settings
    settings().clear()
    for k in ("sign", "batch", "fragment", "sort", "image", "pages", "compress"):
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


def test_compress_screen(win, tmp_path):
    src = _scan_pdf(str(tmp_path / "скан.pdf"), n=2)
    t = win.compress
    win.show_screen(t)
    assert t.step_index() == 0
    t.accept_files([src])
    assert t.path == src and "страниц: 2" in t.lbl_file.text()
    t.radios["strong"].setChecked(True)
    assert t.level() == "strong"
    t.undo_stack.undo()
    assert t.level() == "medium"                                # Ctrl+Z возвращает степень
    out = str(tmp_path / "скан_сжатый.pdf")
    t.save(out)
    assert os.path.exists(out) and t.result.isVisible()
    assert "→" in t.lbl_sizes.text() and "%" in t.lbl_sizes.text()
    assert t.step_index() == 3


def test_compress_screen_not_smaller_removes_file(win, tmp_path):
    t = win.compress
    win.show_screen(t)
    t.show_result({"before": 1000, "after": 990, "images": 1, "changed": 0}, str(tmp_path / "x.pdf"))
    assert "Меньше не стало" in t.lbl_sizes.text()


def test_batch_zero_signatures_is_warning(win):
    b = win.batch
    res = dict(pair=0, out="o.pdf", status="ok", placed=0, blank=[], empty_ink=[0, 1], error="")
    s = b.describe(res)
    assert s.startswith("проверьте рамки") and not s.startswith("готово")
    txt = b.summary_text([res, dict(res, pair=1, placed=2, empty_ink=[])])
    assert "Без единой подписи: пары 1." in txt
    b.status = [s]
    b.show_summary([res])
    assert b._summary_warn is not None and "Проверьте рамки" in b._summary_warn.text()
    b._summary.close()
    ok = dict(res, placed=3, empty_ink=[])
    assert b.describe(ok).startswith("готово")


def test_progress_window_cancel(win, tmp_path):
    """Окно хода: показывает «i из n», при «Отмене» бросает Cancelled — файл не создаётся."""
    from app.core import imageinsert
    from app.core.pdfutil import Cancelled, NRect
    from app.ui.widgets import progress_window
    d = pymupdf.open()
    for _ in range(5):
        d.new_page()
    src = str(tmp_path / "a.pdf")
    d.save(src)
    out = str(tmp_path / "b.pdf")
    img = Image.new("RGB", (40, 40), (0, 0, 200))
    seen = []
    with progress_window(win.image, "Ставлю картинку…") as prog:
        def p(i, n):
            seen.append(i)
            prog(i, n)
            if i == 2:
                prog.dlg.cancel()
        with pytest.raises(Cancelled):
            imageinsert.insert_image(src, img, NRect(0.1, 0.1, 0.2, 0.2), list(range(5)), out, progress=p)
        assert "из 5" in prog.dlg.labelText()
    assert seen == [0, 1, 2, 3] and not os.path.exists(out)
    assert QApplication.overrideCursor() is None


def test_image_save_reports_progress(win, tmp_path, monkeypatch):
    from app.core import imageinsert
    d = pymupdf.open()
    for _ in range(4):
        d.new_page()
    src = str(tmp_path / "a.pdf")
    d.save(src)
    png = str(tmp_path / "p.png")
    Image.new("RGB", (60, 60), (20, 40, 200)).save(png)
    calls = []
    orig = imageinsert.insert_image

    def spy(*a, progress=None, **k):
        def p(i, n):
            calls.append((i, n))
            progress(i, n)
        return orig(*a, progress=p, **k)
    monkeypatch.setattr(imageinsert, "insert_image", spy)
    win.image.open_pdf(src)
    win.image.open_image(png)
    win.image.r_all.setChecked(True)
    win.image.save(str(tmp_path / "out.pdf"))
    assert calls[-1] == (4, 4) and os.path.exists(tmp_path / "out.pdf")
