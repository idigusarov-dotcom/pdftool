"""Сквозная проверка экранов «Пакет» и «Вырезать и перенести» на выдуманных сканах.
Проверяется содержимое результата: где стоят чернила, чьи они, сколько файлов и страниц."""
import os

import numpy as np
import pymupdf
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QProgressDialog

from app.core import signatures as sig
from app.core.pdfutil import NRect
from e2e_lib import (app, blue_mask, crop, dark_mask, ink_in, page_count, pump, recognize, render, shot,
                     toasts_after, wait_until, which_act)

CLEAN = dict(apartments=["14", "27", "35", "48", "52"], receipts=["35", "14", "52", "48", "27"])
SMALL = dict(apartments=["14", "27", "35"], receipts=["27", "35", "14"], seed=21)


def blank_pdf(path, n=3):
    d = pymupdf.open()
    for _ in range(n):
        d.new_page(width=595, height=842)
    d.save(path)
    d.close()
    return path


# ---------------------------------------------------------------- Пакет
def run_batch_ui(w, timeout=300):
    b = w.batch
    b.run_all()
    assert wait_until(app, lambda: not b.running and getattr(b, "last_results", None) is not None, timeout)
    pump(app, 0.3)
    return b.last_results


def test_batch_three_pairs_review_and_zero_warning(win, fake, tmp_path):
    clean = fake("clean", fake.Scenario(**CLEAN))
    small = fake("batch_small", fake.Scenario(**SMALL))
    empty = blank_pdf(str(tmp_path / "пустые_акты.pdf"))
    b, s = win.batch, win.sign
    win.show_screen(b)
    outs = [str(tmp_path / f"пара{i}.pdf") for i in range(3)]
    b.add_pair(clean["acts"], clean["receipts"], out=outs[0])
    b.add_pair(small["acts"], small["receipts"], out=outs[1])
    b.add_pair(empty, small["receipts"], out=outs[2])
    assert len(b.items) == 3
    # пара 1: проверить во вкладке «Подписи», распознать, забрать обратно
    b.table.setCurrentCell(0, 0)
    b.review()
    assert win.current() is s and s.acts_path == clean["acts"]
    recognize(win, s)
    assert s.job.mapping == clean["expected"]
    win.show_screen(b)
    b.take_mapping(0)
    assert b.items[0].mapping == clean["expected"]
    res = run_batch_ui(win)
    shot(win, "batch_done")
    assert [r["status"] for r in res] == ["ok", "ok", "ok"]
    assert res[0]["placed"] == 5 and res[1]["placed"] == 3 and res[2]["placed"] == 0
    text = b.summary_text(res)
    assert "Обработано пар: 3 из 3" in text and "Без единой подписи: пары 3" in text
    assert b._summary_warn is not None and "3" in b._summary_warn.text()
    assert "подписей 0" in b.describe(res[2])
    # содержимое: пара 1 — по распознанным парам; пара 2 — по порядку (не проверяли) — подписи стоят
    for r, a in enumerate(clean["expected"]):
        got, _ = which_act(outs[0], r, sig.DEFAULT_DST_RECT, clean["acts"], clean["sig_pages"], sig.DEFAULT_SRC_RECT)
        assert got == a
    for r in range(3):
        got, _ = which_act(outs[1], r, sig.DEFAULT_DST_RECT, small["acts"], small["sig_pages"], sig.DEFAULT_SRC_RECT)
        assert got == r, "без проверки пара берётся по порядку"
    assert page_count(outs[2]) == 3
    b._summary.close()


def test_batch_cancel_midway(win, fake, tmp_path):
    clean = fake("clean", fake.Scenario(**CLEAN))
    b = win.batch
    win.show_screen(b)
    outs = [str(tmp_path / f"отмена{i}.pdf") for i in range(3)]
    for o in outs:
        b.add_pair(clean["acts"], clean["receipts"], out=o)
    seen = []
    orig = b.bar.setValue

    def on_value(v):
        seen.append(v)
        if v > 0 and b.btn_cancel.isEnabled():
            b.cancel()                 # «Отмена» на первых же страницах
        orig(v)
    b.bar.setValue = on_value
    res = run_batch_ui(win)
    st = [r["status"] for r in res]
    assert "cancelled" in st and st[-1] in ("skipped", "cancelled"), st
    for r, o in zip(res, outs):
        if r["status"] != "ok":
            assert not os.path.exists(o), "недоделанный файл не должен оставаться"
    assert "отменено" in b.describe(res[st.index("cancelled")])
    b._summary.close()
    assert b.btn_run.isEnabled() and not b.running


def test_batch_rejects_same_output_and_input(win, fake, tmp_path):
    clean = fake("clean", fake.Scenario(**CLEAN))
    b = win.batch
    n = len(win.toasts_seen)
    b.add_pair(clean["acts"], clean["receipts"], out=clean["receipts"])      # результат поверх расписок
    b.run_all()
    pump(app, 0.2)
    msg = " ".join(toasts_after(win, n))
    assert "Нельзя начать" in msg, msg
    assert os.path.getsize(clean["receipts"]) > 0 and page_count(clean["receipts"]) == 5


# ---------------------------------------------------------------- Вырезать и перенести
SIG_BOX = NRect(0.12, 0.76, 0.72, 0.85)          # имя + подпись в акте (синяя ручка)
STAMP_BOX = NRect(0.45, 0.52, 0.68, 0.72)        # печать подрядчика на акте 1
DST_A = NRect(0.10, 0.10, 0.50, 0.16)
DST_B = NRect(0.50, 0.80, 0.90, 0.86)


def frag_setup(win, fake, mode="blue", sens=50, keep=True):
    clean = fake("clean", fake.Scenario(**CLEAN))
    f = win.fragment
    win.show_screen(f)
    f.open_src(clean["acts"])
    f.open_dst(clean["receipts"])
    f.cb_mode.setCurrentIndex(f.cb_mode.findData(mode))
    f.sl_sens.setValue(sens)
    f.chk_keep.setChecked(keep)
    return clean, f


def cut_to(f, src, dst, src_page=1, dst_page=1):
    f.sp_src.setValue(src_page)
    f.sp_dst.setValue(dst_page)
    f.view_src.set_nrect(src, emit=False)
    f.recut()
    move_dst(f, dst)
    assert f.cur_rgba is not None


def move_dst(f, rect):
    """Перетащить/растянуть фрагмент на странице «Куда» (как мышью: рамка с картинкой, сигнал изменения)."""
    f.view_dst.set_nrect(rect, f.view_dst.boxes["main"].pixmap, emit=True)


def placed_ink(out, base, page, rect, kind):
    return ink_in(out, page, rect, kind, dpi=100) - ink_in(base, page, rect, kind, dpi=100)


@pytest.mark.parametrize("mode", ["blue", "black", "color", "any", "none"])
def test_fragment_every_mode(win, fake, tmp_path, mode):
    clean, f = frag_setup(win, fake, mode)
    box = STAMP_BOX if mode == "color" else SIG_BOX
    cut_to(f, box, DST_A)
    opaque = (f.cur_rgba[..., 3] == 255).mean()
    out = str(tmp_path / f"фрагмент_{mode}.pdf")
    f.save(out)
    assert page_count(out) == 5
    img = crop(render(out, 0, 100), DST_A)
    base = crop(render(clean["receipts"], 0, 100), DST_A)
    changed = np.abs(img.astype(int) - base.astype(int)).sum(-1) > 40
    assert changed.mean() > 0.01, f"{mode}: фрагмент не появился"
    # вне рамки «Куда» страница не изменилась
    full, fb = render(out, 0, 60), render(clean["receipts"], 0, 60)
    h, w = full.shape[:2]
    mask = np.ones((h, w), bool)
    mask[int(DST_A.y0 * h) - 2:int(DST_A.y1 * h) + 2, int(DST_A.x0 * w) - 2:int(DST_A.x1 * w) + 2] = False
    assert (np.abs(full.astype(int) - fb.astype(int)).sum(-1)[mask] > 40).mean() < 0.002
    if mode == "none":
        assert opaque > 0.99, "без удаления фона — прямоугольник с бумагой целиком"
    else:
        assert opaque < 0.5, f"{mode}: фон должен убираться"
    if mode == "color":
        assert blue_mask(img).sum() > 50, "штамп переносится цветным"
    if mode == "black":
        assert dark_mask(img).sum() > 50


def test_fragment_keep_colors_off_and_sensitivity(win, fake, tmp_path):
    clean, f = frag_setup(win, fake, "color", keep=True)
    cut_to(f, STAMP_BOX, DST_A)
    keep = f.cur_rgba.copy()
    f.chk_keep.setChecked(False)
    f.recut()
    one = f.cur_rgba
    a1, a2 = keep[..., 3] > 128, one[..., 3] > 128
    assert a1.sum() > 100 and a2.sum() > 100
    # без «настоящего цвета» — один цвет ручки
    px = one[a2][:, :3].astype(int)
    assert px.std(0).max() < 12
    f.cb_mode.setCurrentIndex(f.cb_mode.findData("blue"))
    f.chk_keep.setChecked(True)
    f.view_src.set_nrect(SIG_BOX, emit=False)
    f.sl_sens.setValue(5)
    f.recut()
    lo = (f.cur_rgba[..., 3] > 60).sum()
    f.sl_sens.setValue(95)
    f.recut()
    hi = (f.cur_rgba[..., 3] > 60).sum()
    assert hi >= lo, (lo, hi)


def test_fragment_several_pages_move_resize_undo_redo(win, fake, tmp_path):
    clean, f = frag_setup(win, fake, "blue")
    cut_to(f, SIG_BOX, DST_A, src_page=1, dst_page=1)
    f.place_current()
    cut_to(f, SIG_BOX, DST_B, src_page=3, dst_page=4)
    # двигаем и растягиваем поставленный (текущий) фрагмент
    moved = NRect(0.30, 0.30, 0.80, 0.37)
    move_dst(f, moved)
    f.place_current()
    assert len(f.placed) == 2
    assert f.placed[1].dst_page == 3 and f.placed[1].dst_rect == moved
    shot(win, "fragment_two_placed")
    f.undo_stack.undo()                      # отмена «фрагмент поставлен»
    assert len(f.placed) == 1
    f.undo_stack.redo()
    assert len(f.placed) == 2
    out = str(tmp_path / "два_фрагмента.pdf")
    f.save(out)
    assert placed_ink(out, clean["receipts"], 0, DST_A, "blue") > 100
    assert placed_ink(out, clean["receipts"], 3, moved, "blue") > 100
    assert abs(placed_ink(out, clean["receipts"], 3, DST_B, "blue")) < 20, "старое место после перемещения пустое"
    for p in (1, 2, 4):
        assert abs(placed_ink(out, clean["receipts"], p, NRect(0, 0, 1, 1), "blue")) < 20
    # чей фрагмент: со стр. 1 → расписка 1, со стр. 3 → расписка 4
    got, _ = which_act(out, 0, DST_A, clean["acts"], [0, 2], SIG_BOX)
    assert got == 0
    got, _ = which_act(out, 3, moved, clean["acts"], [0, 2], SIG_BOX)
    assert got == 2


def test_fragment_retouch_persists_through_sensitivity_place_save(win, fake, tmp_path, monkeypatch):
    from app.core import retouch as rt
    from app.ui import retouch_dialog
    clean, f = frag_setup(win, fake, "blue")
    cut_to(f, SIG_BOX, DST_A)
    before = (f.cur_rgba[..., 3] > 60).sum()
    # «Ластик» по левой половине фрагмента (имя), через окно доработки
    y = (SIG_BOX.y0 + SIG_BOX.y1) / 2
    erase = rt.Stroke(rt.ERASE, 0.035, tuple((x, y) for x in np.linspace(SIG_BOX.x0, 0.43, 12)))

    def fake_exec(dlg):
        dlg._push(dlg.strokes() + (erase,))
        return retouch_dialog.RetouchDialog.Accepted
    monkeypatch.setattr(retouch_dialog.RetouchDialog, "exec", fake_exec)
    f.open_retouch()
    assert len(f.cur_retouch) == 1
    after = (f.cur_rgba[..., 3] > 60).sum()
    assert after < before * 0.8
    f.sl_sens.setValue(80)
    f.recut()
    assert len(f.cur_retouch) == 1, "правки не сбрасываются при смене чувствительности"
    f.place_current()
    assert f.placed[0].retouch
    out = str(tmp_path / "с_доработкой.pdf")
    f.save(out)
    img = crop(render(out, 0, 100), DST_A)
    base = crop(render(clean["receipts"], 0, 100), DST_A)
    add = blue_mask(img) & ~blue_mask(base)
    h, w = add.shape
    assert add.sum() > 50
    # стёртая левая часть пустая (фрагмент целиком — подпись справа)
    from PIL import Image
    Image.fromarray(img).save(str(tmp_path / "dbg.png"))
    if os.environ.get("PDFTOOL_E2E_SHOTS"):
        Image.fromarray(img).save(os.path.join(os.environ["PDFTOOL_E2E_SHOTS"], "retouch_saved.png"))
    left = add[:, :int(0.25 * w)].sum()
    assert left < 0.03 * add.sum(), ("стёртое ластиком не должно попасть в файл", left, add.sum())
