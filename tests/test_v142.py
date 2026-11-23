"""Регрессии 1.4.2 (по прогону на выдуманных сканах):
1) «Вернуть чернила» на сером/желтоватом скане не возвращает тон бумаги;
2) ластик у края фрагмента не растягивает фрагмент на странице «Куда»;
3) точки после инициалов («Е. П.») не теряются вместе с пылью, а пыль по-прежнему убирается."""
import os
import sys

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestV142"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.core import fragment as fr  # noqa: E402
from app.core import ink  # noqa: E402
from app.core import retouch as rt  # noqa: E402
from app.core.pdfutil import NRect  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
BLUE = (30, 50, 160)


# ---------- 1. «Вернуть чернила» и тон бумаги ----------
def toned_crop(paper=(232, 226, 208), seed=1):
    """Желтовато-серая бумага с шумом и лёгким градиентом, бледный синевато-серый штрих посередине."""
    rng = np.random.default_rng(seed)
    h, w = 120, 300
    a = np.broadcast_to(np.array(paper, np.float32), (h, w, 3)).copy()
    a *= (1 - 0.04 * np.linspace(0, 1, w))[None, :, None]
    a[55:62, 40:260] = (172, 178, 200)                          # бледный «хвост» подписи
    a += rng.normal(0, 4, a.shape)
    return np.clip(a, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("paper", [(232, 226, 208), (214, 214, 214), (245, 238, 215)])
def test_restore_keeps_toned_paper_transparent(paper):
    crop = toned_crop(paper)
    lay = rt.restore_layer(crop, "any", True)
    bg = np.ones(crop.shape[:2], bool)
    bg[50:67, 35:265] = False
    assert (lay[..., 3][bg] > 10).mean() < 0.005                  # бумага прозрачная (было ~100% с альфой ~90)
    assert np.median(lay[57:60, 60:240, 3]) > 90                  # бледный штрих возвращается
    # цвет возвращённого штриха — без примеси бумаги: синий канал заметно выше красного
    c = lay[57:60, 60:240, :3].reshape(-1, 3).mean(0)
    assert c[2] - c[0] > 25


def test_restore_on_white_paper_unchanged():
    """На белой бумаге — прежнее поведение (порог 245, шкала 70)."""
    crop = np.full((60, 120, 3), 255, np.uint8)
    crop[20:30, 10:110] = 200
    lay = rt.restore_layer(crop, "blue", True)
    exp = int(((245 - 200) / 70) ** 0.7 * 255)
    assert abs(int(lay[25, 60, 3]) - exp) <= 2
    assert lay[5, 5, 3] == 0


def test_restore_paper_level_not_fooled_by_dense_stamp():
    """Блок, почти весь занятый печатью, не принимается за «бумагу»: оттиск возвращается."""
    crop = np.full((150, 150, 3), 236, np.uint8)
    crop[:60, :60] = (110, 100, 190)
    lay = rt.restore_layer(crop, "any", True)
    assert lay[30, 30, 3] > 200 and lay[120, 120, 3] == 0


# ---------- 2. размер фрагмента после ластика ----------
def test_follow_rect_keeps_scale_and_ink_position():
    old_ink = NRect(0.10, 0.40, 0.50, 0.50)
    old_dst = NRect(0.30, 0.60, 0.70, 0.70)                     # масштаб 1:1
    new_ink = NRect(0.20, 0.40, 0.50, 0.50)                     # левый край стёрт ластиком
    r = fr.follow_rect(old_dst, old_ink, new_ink)
    assert r.x0 == pytest.approx(0.40) and r.x1 == pytest.approx(0.70)      # правый край — на месте
    assert (r.x1 - r.x0) / (new_ink.x1 - new_ink.x0) == pytest.approx(1.0)  # масштаб тот же
    other = fr.follow_rect(old_dst, old_ink, NRect(0.6, 0.1, 0.7, 0.15))    # другие чернила — в тот же угол
    assert (other.x0, other.y0) == pytest.approx((0.30, 0.60))
    assert other.x1 - other.x0 == pytest.approx(0.10)


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


def two_pdfs(tmp_path):
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    p.draw_line((0.12 * 595, 0.45 * 842), (0.58 * 595, 0.45 * 842), color=(0.12, 0.2, 0.75), width=3)
    p.draw_circle((0.45 * 595, 0.45 * 842), 30, color=(0.3, 0.25, 0.7), width=3)
    src = str(tmp_path / "откуда.pdf")
    d.save(src)
    d = pymupdf.open()
    d.new_page(width=595, height=842)
    dst = str(tmp_path / "куда.pdf")
    d.save(dst)
    return src, dst


def test_edge_erase_does_not_enlarge_placed_fragment(win, tmp_path, monkeypatch):
    from app.ui import retouch_dialog
    src, dst = two_pdfs(tmp_path)
    f = win.fragment
    f.open_src(src)
    f.open_dst(dst)
    f.cb_mode.setCurrentIndex(f.cb_mode.findData("blue"))
    rect = NRect(0.10, 0.40, 0.60, 0.50)
    f.view_src.set_nrect(rect)
    f.recut()
    f.view_dst.set_nrect(NRect(0.2, 0.6, 0.2 + 0.48 * 0.9, 0.7), f.view_dst.boxes["main"].pixmap)
    f.recut()
    d0, ink0 = f.view_dst.get_nrect(), fr.ink_rect(rect, f.cur_frac)
    # стираем левый кусок линии (до круга) — граница чернил сдвигается вправо
    erase = (rt.Stroke(rt.ERASE, 0.015, ((0.11, 0.45), (0.30, 0.45))),)
    monkeypatch.setattr(retouch_dialog.RetouchDialog, "exec", lambda self: retouch_dialog.RetouchDialog.Accepted)
    monkeypatch.setattr(retouch_dialog.RetouchDialog, "strokes", lambda self: erase)
    f.open_retouch()
    d1, ink1 = f.view_dst.get_nrect(), fr.ink_rect(rect, f.cur_frac)
    assert ink1.x0 > ink0.x0 + 0.1                                  # чернил стало меньше слева
    s0 = (d0.x1 - d0.x0) / (ink0.x1 - ink0.x0)
    s1 = (d1.x1 - d1.x0) / (ink1.x1 - ink1.x0)
    assert s1 == pytest.approx(s0, rel=0.01)                         # масштаб тот же (раньше рос на ~10–60%)
    assert d1.x1 == pytest.approx(d0.x1, abs=0.003)                  # правый край оставшихся чернил на месте
    assert d1.y0 == pytest.approx(d0.y0, abs=0.003)
    # смена чувствительности тоже не растягивает
    f.sl_sens.setValue(80)
    f.recut()
    d2, ink2 = f.view_dst.get_nrect(), fr.ink_rect(rect, f.cur_frac)
    assert (d2.x1 - d2.x0) / (ink2.x1 - ink2.x0) == pytest.approx(s0, rel=0.01)


# ---------- 3. точки после инициалов и пыль ----------
def initials_image(seed=3):
    """«Буквы» (штрихи толщиной ~4 px), после двух из них — точки на строке; вокруг — синяя пыль."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", (520, 140), (238, 236, 228))
    d = ImageDraw.Draw(im)
    base = 90
    letters = [(30, 90), (110, 170), (230, 290)]
    for x0, x1 in letters:                                        # «буква»: две стойки и перекладина
        d.line([(x0, base), (x0 + 8, base - 50)], fill=BLUE, width=4)
        d.line([(x0 + 8, base - 50), (x1, base - 50)], fill=BLUE, width=4)
        d.line([(x1, base - 50), (x1 - 6, base)], fill=BLUE, width=4)
    dots = [(181, base - 2), (301, base - 2)]                    # «Е.» «П.»
    for cx, cy in dots:
        d.ellipse((cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5), fill=BLUE)
    dust = []
    while len(dust) < 25:                                         # пыль/брызги вдали от надписи
        x, y = int(rng.integers(5, 515)), int(rng.integers(5, 135))
        if 330 < x or y > 115 or y < 25:
            if all(abs(x - a) > 30 or abs(y - b) > 30 for a, b in dots) and \
                    all(abs(x - a) > 20 or abs(y - b) > 20 for a, b in dust):     # одиночные пылинки
                dust.append((x, y))
                s = int(rng.integers(2, 4))
                d.rectangle((x, y, x + s, y + s), fill=BLUE)
    a = np.asarray(im).astype(np.float32) + rng.normal(0, 3, (140, 520, 3))
    return np.clip(a, 0, 255).astype(np.uint8), dots, dust


def test_initial_dots_kept_dust_removed():
    rgb, dots, dust = initials_image()
    out = ink.extract_ink(rgb, mode="blue")[..., 3]
    for cx, cy in dots:
        assert out[cy - 1:cy + 2, cx - 1:cx + 2].max() > 150, (cx, cy)       # точка осталась
    far = [(x, y) for x, y in dust if not (20 <= x <= 330 and 20 <= y <= 110)]
    assert len(far) >= 15
    left = sum(out[y:y + 4, x:x + 4].max() > 30 for x, y in far)
    assert left == 0                                                     # пыль вдали — убрана


def test_line_remnant_and_fringe_not_rescued():
    """Плоские обрывки (бахрома печатного текста, остатки линии) рядом с подписью точкой не считаются."""
    rgb, _, _ = initials_image()
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    d.rectangle((120, 100, 127, 101), fill=BLUE)                 # 8×2 «волосок» под буквой
    d.rectangle((140, 18, 142, 20), fill=BLUE)                   # точка НАД верхом буквы (бахрома сверху)
    out = ink.extract_ink(np.asarray(im), mode="blue")[..., 3]
    assert out[100:102, 120:128].max() < 30
    assert out[18:21, 140:143].max() < 30
