"""Ручная доработка фрагмента («Ластик» / «Вернуть чернила»): поправка к маске поверх автоматики."""
import io
import os
import sys

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestRetouch"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.core import fragment as fr  # noqa: E402
from app.core import retouch as rt  # noqa: E402
from app.core.ink import DEFAULT_INK_RGB  # noqa: E402
from app.core.pdfutil import NRect, render_page  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

# Фрагмент: x 0.10..0.60, y 0.40..0.50 страницы A4. Синяя линия (подпись) и чёрная линия (в режиме «синие» не
# берётся). Середину синей стираем, кусок чёрной возвращаем.
RECT = NRect(0.10, 0.40, 0.60, 0.50)
BLUE_Y, BLACK_Y = 0.43, 0.47
ERASE_X, RESTORE_X = (0.30, 0.36), (0.44, 0.52)


@pytest.fixture
def src(tmp_path):
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    W, H = p.rect.width, p.rect.height
    p.draw_line((0.12 * W, BLUE_Y * H), (0.58 * W, BLUE_Y * H), color=(0.12, 0.2, 0.75), width=3)
    p.draw_line((0.12 * W, BLACK_Y * H), (0.58 * W, BLACK_Y * H), color=(0.05, 0.05, 0.05), width=3)
    path = str(tmp_path / "источник.pdf")
    d.save(path)
    d.close()
    return path


@pytest.fixture
def dst(tmp_path):
    d = pymupdf.open()
    d.new_page(width=595, height=842)
    path = str(tmp_path / "цель.pdf")
    d.save(path)
    d.close()
    return path


def stroke(op, x0, x1, y, r=0.012):
    return rt.Stroke(op, r, ((x0, y), ((x0 + x1) / 2, y), (x1, y)))


EDITS = (stroke(rt.ERASE, *ERASE_X, BLUE_Y), stroke(rt.RESTORE, *RESTORE_X, BLACK_Y))


def col(x, dpi_w):          # доля страницы -> столбец пикселя внутри фрагмента
    return int(round(x * dpi_w)) - int(round(RECT.x0 * dpi_w))


def row(y, dpi_h):
    return int(round(y * dpi_h)) - int(round(RECT.y0 * dpi_h))


# ---------- маска-поправка ----------
def test_rasterize_order_later_stroke_wins():
    box, page = (0, 0, 100, 100), (100, 100)
    a = rt.Stroke(rt.ERASE, 0.1, ((0.5, 0.5),))
    b = rt.Stroke(rt.RESTORE, 0.05, ((0.5, 0.5),))
    lab = rt.rasterize((a, b), box, page)
    assert lab[50, 50] == 2 and lab[50, 42] == 1 and lab[0, 0] == 0
    lab2 = rt.rasterize((b, a), box, page)
    assert lab2[50, 50] == 1


def test_rasterize_is_resolution_independent():
    s = rt.Stroke(rt.ERASE, 0.02, ((0.3, 0.3), (0.6, 0.3)))
    lo = rt.rasterize((s,), (0, 0, 200, 200), (200, 200)) > 0
    hi = rt.rasterize((s,), (0, 0, 400, 400), (400, 400)) > 0
    hi_small = np.asarray(Image.fromarray(hi.astype(np.uint8) * 255).resize((200, 200))) > 127
    assert abs(int(lo.sum()) - int(hi_small.sum())) < 0.1 * lo.sum()


def test_erase_and_restore(src):
    with pymupdf.open(src) as d:
        auto, crop, box, page_px = fr.cut_parts(d, 0, RECT, "blue", 0.5, True, dpi=150)
        W, H = page_px
        out = rt.apply(auto, crop, EDITS, box, page_px, "blue", True)
        by, ky = row(BLUE_Y, H), row(BLACK_Y, H)
        ex = col(sum(ERASE_X) / 2, W)
        rx = col(sum(RESTORE_X) / 2, W)
        assert auto[by, ex, 3] > 200 and out[by, ex, 3] == 0                   # стёрто — прозрачно
        assert auto[ky, rx, 3] == 0 and out[ky, rx, 3] > 200                   # чёрная линия вернулась
        assert out[ky, rx, :3].max() < 60                                     # её настоящий (чёрный) цвет
        assert out[by, col(0.15, W), 3] == auto[by, col(0.15, W), 3]           # вне мазков — как было
        assert out[ky, col(0.2, W), 3] == 0                                   # чёрное вне кисти не взялось
        # ничего «из воздуха»: кисть по бумаге не рисует
        paper = rt.apply(auto, crop, (stroke(rt.RESTORE, 0.2, 0.5, 0.405),), box, page_px, "blue", True)
        assert (paper[..., 3] > 0).sum() == (auto[..., 3] > 0).sum()


def test_restore_uses_pipeline_recolor_when_true_color_off(src):
    with pymupdf.open(src) as d:
        auto, crop, box, page_px = fr.cut_parts(d, 0, RECT, "blue", 0.5, False, dpi=150)
        out = rt.apply(auto, crop, EDITS, box, page_px, "blue", False)
        W, H = page_px
        px = out[row(BLACK_Y, H), col(sum(RESTORE_X) / 2, W)]
        assert px[3] > 200 and tuple(px[:3]) == DEFAULT_INK_RGB


def test_edits_survive_sensitivity_and_dpi(src):
    with pymupdf.open(src) as d:
        for sens in (0.1, 0.9):
            for dpi in (150, 300):
                rgba = fr.cut(d, 0, RECT, "blue", sens, True, dpi=dpi, retouch=EDITS)
                W = int(round(d[0].rect.width * dpi / 72))
                H = int(round(d[0].rect.height * dpi / 72))
                assert rgba[row(BLUE_Y, H), col(sum(ERASE_X) / 2, W), 3] == 0
                assert rgba[row(BLACK_Y, H), col(sum(RESTORE_X) / 2, W), 3] > 200


def test_mode_none_erase_makes_transparent(src):
    with pymupdf.open(src) as d:
        rgba = fr.cut(d, 0, RECT, "none", 0.5, True, dpi=150, retouch=EDITS[:1])
        assert rgba[..., 3].min() == 0 and rgba[0, 0, 3] == 255


# ---------- перенос и сохранение ----------
def test_placement_png_and_saved_pdf_keep_edits(src, dst, tmp_path):
    dst_rect = NRect(0.1, 0.1, 0.6, 0.2)
    plain = fr.Placement(src, 0, RECT, 0, dst_rect, "blue", 0.5, True)
    edited = fr.Placement(src, 0, RECT, 0, dst_rect, "blue", 0.5, True, retouch=EDITS)
    a0 = np.asarray(Image.open(io.BytesIO(plain.png())))[..., 3]
    a1 = np.asarray(Image.open(io.BytesIO(edited.png())))[..., 3]
    assert a1.shape[0] > a0.shape[0]          # возвращённая чёрная линия расширила фрагмент вниз
    out = str(tmp_path / "итог.pdf")
    fr.apply(dst, [edited], out).close()
    with pymupdf.open(out) as d:
        imgs = d[0].get_images(full=True)
        assert imgs and imgs[0][1]                                # есть маска прозрачности
        smask = d.extract_image(imgs[0][1])
        alpha = np.asarray(Image.open(io.BytesIO(smask["image"])).convert("L"))
        assert alpha.shape == a1.shape and np.array_equal(alpha, a1)
        # на странице: стёртая середина синей линии — белая бумага, возвращённая чёрная — тёмная
        img = render_page(d, 0, 150)
    h, w = img.shape[:2]
    fx0, fy0, fx1, fy1 = _frac(src)

    def to_dst(x, y):
        u = ((x - RECT.x0) / (RECT.x1 - RECT.x0) - fx0) / (fx1 - fx0)
        v = ((y - RECT.y0) / (RECT.y1 - RECT.y0) - fy0) / (fy1 - fy0)
        return int((dst_rect.y0 + v * (dst_rect.y1 - dst_rect.y0)) * h), \
            int((dst_rect.x0 + u * (dst_rect.x1 - dst_rect.x0)) * w)
    y, x = to_dst(sum(ERASE_X) / 2, BLUE_Y)
    assert img[y, x].min() > 240
    y, x = to_dst(0.2, BLUE_Y)
    assert img[y - 2:y + 3, x].min() < 150
    y, x = to_dst(sum(RESTORE_X) / 2, BLACK_Y)
    assert img[y - 2:y + 3, x].max(axis=-1).min() < 100


def _frac(src):
    with pymupdf.open(src) as d:
        _, frac = fr.trim(fr.cut(d, 0, RECT, "blue", 0.5, True, retouch=EDITS))
    return frac


# ---------- окно «Доработать вручную» ----------
def _dialog(src, strokes=()):
    from app.ui.retouch_dialog import RetouchDialog
    d = pymupdf.open(src)
    return RetouchDialog(None, d, 0, RECT, "blue", 0.5, True, strokes, dpi=150)


def _drag(dlg, x0, x1, y):
    W, H = dlg.page_px
    bx, by = dlg.box[:2]
    pts = [QPointF(x * W - bx, y * H - by) for x in np.linspace(x0, x1, 8)]
    dlg._start(pts[0])
    for p in pts[1:]:
        dlg._move(p)
    dlg._end()


def test_dialog_erase_restore_undo_redo_reset(src):
    dlg = _dialog(src)
    W, H = dlg.page_px
    by, ex = row(BLUE_Y, H), col(sum(ERASE_X) / 2, W)
    ky, rx = row(BLACK_Y, H), col(sum(RESTORE_X) / 2, W)
    _drag(dlg, *ERASE_X, BLUE_Y)
    dlg.btn_restore.setChecked(True)
    _drag(dlg, *RESTORE_X, BLACK_Y)
    assert [s.op for s in dlg.strokes()] == [rt.ERASE, rt.RESTORE]
    img = dlg.composed()
    assert img[by, ex, 3] == 0 and img[ky, rx, 3] > 200
    dlg.undo()
    assert len(dlg.strokes()) == 1 and dlg.composed()[ky, rx, 3] == 0
    dlg.redo()
    assert len(dlg.strokes()) == 2
    dlg.reset()
    assert dlg.strokes() == () and dlg.composed()[by, ex, 3] > 200
    dlg.undo()                                             # «Сбросить» тоже отменяется
    assert len(dlg.strokes()) == 2
    dlg.undo()
    dlg.undo()
    dlg.undo()                                             # дальше начала — без ошибки
    assert dlg.strokes() == () and not dlg.btn_undo.isEnabled()
    dlg.bigger()
    assert dlg.view.radius > 14
    dlg.smaller()
    dlg.smaller()
    assert dlg.view.radius < 14


def test_dialog_keeps_existing_edits(src):
    dlg = _dialog(src, EDITS)
    assert dlg.strokes() == EDITS
    W, H = dlg.page_px
    assert dlg.composed()[row(BLUE_Y, H), col(sum(ERASE_X) / 2, W), 3] == 0


# ---------- экран «Вырезать и перенести» ----------
@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", str(tmp_path / "tpl"))
    from app.main import MainWindow
    from app.ui.infra import settings
    settings().clear()
    w = MainWindow()
    w.show()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


def _accept_with(monkeypatch, strokes):
    from app.ui import retouch_dialog
    monkeypatch.setattr(retouch_dialog.RetouchDialog, "exec", lambda self: retouch_dialog.RetouchDialog.Accepted)
    monkeypatch.setattr(retouch_dialog.RetouchDialog, "strokes", lambda self: strokes)


def test_fragment_tab_retouch_place_save_undo(win, src, dst, tmp_path, monkeypatch):
    f = win.fragment
    win.show_screen(f)
    f.open_src(src)
    f.open_dst(dst)
    f.cb_mode.setCurrentIndex(f.cb_mode.findData("blue"))
    f.view_src.set_nrect(RECT)
    f.recut()
    assert f.btn_retouch.isEnabled()
    before = f.cur_rgba.copy()
    _accept_with(monkeypatch, EDITS)
    f.open_retouch()
    assert f.cur_retouch == EDITS and f.lbl_retouch.isVisibleTo(f)
    assert not np.array_equal(before, f.cur_rgba)
    # смена чувствительности: правки остаются и применяются заново
    f.sl_sens.setValue(80)
    f.recut()
    assert f.cur_retouch == EDITS
    # отмена на экране возвращает к состоянию без правок
    while f.cur_retouch and f.undo_stack.canUndo():
        f.undo_stack.undo()
    assert f.cur_retouch == ()
    while f.undo_stack.canRedo():
        f.undo_stack.redo()
    assert f.cur_retouch == EDITS
    f.place_current()
    assert f.placed[-1].retouch == EDITS and f.cur_retouch == ()
    assert "вручную" in f.lst.item(0).text()
    out = str(tmp_path / "сохранено.pdf")
    f.save(out)
    ref = str(tmp_path / "эталон.pdf")
    fr.apply(dst, [fr.Placement(src, 0, RECT, 0, f.placed[0].dst_rect, "blue", 0.8, True, retouch=EDITS)],
             ref).close()
    with pymupdf.open(out) as a, pymupdf.open(ref) as b:
        assert np.array_equal(render_page(a, 0, 72), render_page(b, 0, 72))


def test_fragment_tab_page_change_drops_edits(win, src, dst, monkeypatch):
    f = win.fragment
    f.open_src(src)
    f.open_dst(dst)
    f.view_src.set_nrect(RECT)
    f.recut()
    _accept_with(monkeypatch, EDITS)
    f.open_retouch()
    assert f.cur_retouch
    f.open_src(src)                      # другой (тот же) файл — правки к старому не относятся
    assert f.cur_retouch == ()


def test_dialog_ui_builds_in_both_themes_small_window(src):
    from app.ui import style
    for dark in (False, True):
        style.apply_theme(app, dark)
        dlg = _dialog(src)
        dlg.resize(560, 400)
        dlg.show()
        app.processEvents()
        assert dlg.btn_ok.isVisible() and dlg.btn_reset.isVisible()
        assert dlg.view.width() > 200
        dlg.close()
    style.apply_theme(app, False)
