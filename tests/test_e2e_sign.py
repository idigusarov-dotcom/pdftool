"""Сквозная проверка экрана «Перенос подписей» на выдуманных сканах: распознавание и автоподбор в разных
раскладах, ручная правка пар, рамки, шаблоны, зоны, «не ставить», предпросмотр, сохранение.
Проверяется СОДЕРЖИМОЕ результата: чья подпись стоит в каждой расписке и где."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QSpinBox  # noqa: E402

from app.core import signatures as sig  # noqa: E402
from app.core.pdfutil import NRect  # noqa: E402
from e2e_lib import app, ink_in, page_count, pump, recognize, shot, wait_until, which_act  # noqa: E402,F401


CASES = {
    "clean": dict(apartments=["14", "27", "35", "48", "52"], receipts=["35", "14", "52", "48", "27"]),
    "missing": dict(apartments=["14", "27", "35", "48"], receipts=["35", "14", "52", "48", "27"], seed=11),
    "dupl": dict(apartments=["14", "27", "27", "35"], receipts=["27", "14", "35"], seed=12),
    "illeg": dict(apartments=["14", "27", "35"], receipts=["27", "35", "14"], illegible_acts=(1,), seed=13),
    "more_acts": dict(apartments=["14", "27", "35", "48", "52", "61"], receipts=["52", "14"], seed=14),
    "more_rec": dict(apartments=["14", "27"], receipts=["27", "35", "14", "48"], seed=15),
    "multipage": dict(apartments=["14", "27", "35"], receipts=["35", "27", "14"], pages_per_act=2, seed=16),
    "black": dict(apartments=["14", "27", "35"], receipts=["27", "14", "35"], ink="black", seed=17),
}


def pair(fake, name):
    return fake(name, fake.Scenario(**CASES[name]))


def same(a, b, tol=2e-3):
    return a is not None and b is not None and all(abs(getattr(a, k) - getattr(b, k)) < tol for k in ("x0", "y0", "x1", "y1"))


def check_output(out, meta, mapping, kind="blue", src=sig.DEFAULT_SRC_RECT, dst=sig.DEFAULT_DST_RECT):
    """В каждой расписке — подпись именно из того акта, что в mapping; где None — пусто."""
    assert page_count(out) == len(meta["scenario"].receipts)
    refs = None
    if kind != "blue":                 # эталон чёрной подписи — без печатного текста бланка (он общий у всех актов)
        import pymupdf
        job = sig.TransferJob(meta["acts"], meta["receipts"], list(meta["sig_pages"]), ink_mode="black")
        with pymupdf.open(meta["acts"]) as d:
            refs = {a: sig.preview_cut(job, d, a, src, 110)[..., 3] > 60 for a in meta["sig_pages"]}
    for r, a in enumerate(mapping):
        ink = ink_in(out, r, dst, kind)
        base = ink_in(meta["receipts"], r, dst, kind)
        if a is None:
            assert ink - base < 30, f"расписка {r + 1}: подпись не должна стоять"
            continue
        assert ink - base > 80, f"расписка {r + 1}: нет подписи"
        got, score = which_act(out, r, dst, meta["acts"], meta["sig_pages"], src, kind, refs=refs,
                               base_pdf=meta["receipts"] if kind != "blue" else None)
        assert got == a, f"расписка {r + 1}: подпись из акта стр. {got + 1 if got is not None else '-'}, " \
                         f"нужна стр. {a + 1} (похожесть {score:.2f})"


@pytest.mark.parametrize("name", ["clean", "missing", "more_acts", "more_rec", "multipage", "black"])
def test_auto_match_and_save(win, fake, tmp_path, name):
    meta = pair(fake, name)
    s = win.sign
    win.show_screen(s)
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    if meta["scenario"].ink == "black":
        s.advanced.set_expanded(True)
        s.cb_mode.setCurrentIndex(s.cb_mode.findData("black"))
    msg = recognize(win, s)
    exp = meta["expected"]
    assert s.job.mapping == exp, (s.job.mapping, exp, [x.text for x in s.nums_rec], [x.text for x in s.nums_act])
    n_ok = sum(m is not None for m in exp)
    assert f"Сопоставлено: {n_ok} из {len(exp)}" in msg
    shot(win, f"sign_{name}")
    out = str(tmp_path / f"{name}_с_подписями.pdf")
    n = len(win.toasts_seen)
    s.save(out)
    done = [t for _, t in win.toasts_seen[n:] if "Готово" in t]
    assert done and f"Подписей поставлено: {n_ok}" in done[0], win.toasts_seen[n:]
    check_output(out, meta, exp, "blue" if meta["scenario"].ink == "blue" else "dark")


def test_duplicate_numbers_are_flagged(win, fake):
    """Два акта с одним номером: программа не должна молча выбирать один из них."""
    meta = pair(fake, "dupl")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    msg = recognize(win, s)
    r27 = meta["scenario"].receipts.index("27")
    assert s.conf[r27] is not None and s.conf[r27] < 0.5, "строка с повторяющимся номером должна быть жёлтой"
    assert "27" in msg and "повтор" in msg.lower(), msg
    # остальные строки сопоставлены верно
    for r, apt in enumerate(meta["scenario"].receipts):
        if apt != "27":
            assert s.job.mapping[r] == meta["scenario"].apartments.index(apt)


def test_illegible_number_manual_fix(win, fake, tmp_path):
    meta = pair(fake, "illeg")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    msg = recognize(win, s)
    r27 = meta["scenario"].receipts.index("27")
    assert s.job.mapping[r27] is None and "2 из 3" in msg
    # пользователь сам выбирает акт стр. 2 для расписки кв. 27
    fixed = list(s.job.mapping)
    fixed[r27] = 1
    s.set_mapping(fixed)
    assert s.job.mapping == [1, 2, 0]
    s.undo_stack.undo()
    assert s.job.mapping[r27] is None
    s.undo_stack.redo()
    out = str(tmp_path / "ручная.pdf")
    s.save(out)
    check_output(out, meta, [1, 2, 0])


def test_not_place_and_order_without_recognition(win, fake, tmp_path):
    """Без распознавания — пары «по порядку»; «не ставить» оставляет расписку пустой."""
    meta = pair(fake, "clean")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    assert s.job.mapping == [0, 1, 2, 3, 4]
    s.set_mapping([2, None, 4, 3, None])
    out = str(tmp_path / "часть.pdf")
    s.save(out)
    check_output(out, meta, [2, None, 4, 3, None])


def test_frames_global_and_own(win, fake, tmp_path):
    """Синяя рамка: общая (для всех расписок) и «только для этой расписки»; отмена правки рамки."""
    meta = pair(fake, "clean")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    s.set_mapping([2, 0, 4, 3, 1])
    low = NRect(0.45, 0.70, 0.85, 0.76)           # правее «Дата:»
    s.table.setCurrentCell(0, 2)
    s.view_rec.set_nrect(low)                     # как будто перетащили рамку мышью
    assert same(s.job.dst_rect, low)
    s.table.setCurrentCell(2, 2)
    s.chk_dst_own.setChecked(True)
    own = NRect(0.12, 0.50, 0.52, 0.56)
    s.view_rec.set_nrect(own)
    assert same(s.job.dst_overrides.get(2), own) and same(s.job.dst_rect, low)
    out = str(tmp_path / "рамки.pdf")
    s.save(out)
    for r in range(5):
        where = own if r == 2 else low
        assert ink_in(out, r, where) - ink_in(meta["receipts"], r, where) > 80, r
        assert ink_in(out, r, sig.DEFAULT_DST_RECT) - ink_in(meta["receipts"], r, sig.DEFAULT_DST_RECT) < 30, r
    # отмена: последняя правка (своя рамка) уходит
    s.undo_stack.undo()
    assert 2 not in s.job.dst_overrides
    # «Рамки по умолчанию»
    s.reset_rects()
    assert same(s.job.dst_rect, sig.DEFAULT_DST_RECT) and not s.job.dst_overrides


def test_template_roundtrip_and_zone(win, fake, tmp_path):
    """Шаблон запоминает рамки и чернила; выбор шаблона их возвращает. Зелёная зона номера влияет на распознавание."""
    meta = pair(fake, "clean")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    s.advanced.set_expanded(True)
    low = NRect(0.45, 0.70, 0.85, 0.76)
    s.table.setCurrentCell(0, 2)
    s.view_rec.set_nrect(low)
    s.sl_sens.setValue(65)
    t = s.tbar.save("Акт стояки (тест)")
    assert t is not None and os.path.exists(os.environ["PDFTOOL_TEMPLATES"])
    s.reset_rects()
    s.sl_sens.setValue(50)
    assert s.job.dst_rect == sig.DEFAULT_DST_RECT
    s.tbar.reload("Акт стояки (тест)")
    s.tbar._chosen()
    assert s.job.dst_rect == low and abs(s.job.sensitivity - 0.65) < 1e-6
    # зона номера в пустом месте — номера не читаются; правильная — читаются
    s.tbar.chk_auto.setChecked(False)
    s.tbar.reload("")
    s.r_zone.setChecked(True)
    s.table.setCurrentCell(0, 2)
    s.view_rec.set_nrect(NRect(0.6, 0.85, 0.8, 0.9), key="zone")
    assert s.zone_rec == NRect(0.6, 0.85, 0.8, 0.9)
    recognize(win, s)
    assert all(not x.text for x in s.nums_rec)
    from app.ui.tab_sign import DEFAULT_ZONE_REC
    s.view_rec.set_nrect(DEFAULT_ZONE_REC, key="zone")
    recognize(win, s)
    assert [x.text for x in s.nums_rec] == meta["scenario"].receipts


def test_preview_renders_requested_receipt(win, fake, monkeypatch):
    meta = pair(fake, "missing")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    s.set_mapping(meta["expected"])
    visited = []
    original = sig.preview_page

    def track(job, index, dpi):
        visited.append(index)
        return original(job, index, dpi)

    monkeypatch.setattr(sig, "preview_page", track)
    QTest.mouseClick(s.btn_preview, Qt.LeftButton)
    dlg = win.findChild(QDialog, "ReceiptPreview")
    assert dlg.isVisible() and dlg.isModal()
    assert wait_until(app, lambda: s._reviewed, timeout=5)
    assert visited == [0]
    source, = [label for label in dlg.findChildren(QLabel) if "акта" in label.text()]
    assert "страницы 3" in source.text()
    page = dlg.findChild(QSpinBox)
    page.setValue(3)
    assert source.text() == "Без подписи"
    following, = [button for button in dlg.findChildren(QPushButton) if button.text() == "Далее"]
    QTest.mouseClick(following, Qt.LeftButton)
    QTest.mouseClick(following, Qt.LeftButton)
    assert page.value() == page.maximum() == 5 and not following.isEnabled()
    assert visited == [0, 2, 3, 4]
    shot(dlg, "sign_preview")
    QTest.mouseClick(dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Close), Qt.LeftButton)
    assert not dlg.isVisible()


def test_stamp_not_copied_with_signature(win, fake, tmp_path):
    """В актах есть печать подрядчика — в расписку переносится только подпись жильца, печать — нет."""
    meta = pair(fake, "clean")
    s = win.sign
    s.open_acts(meta["acts"])
    s.open_receipts(meta["receipts"])
    s.set_mapping([2, 0, 4, 3, 1])
    out = str(tmp_path / "без_печати.pdf")
    s.save(out)
    outside = NRect(0.0, 0.0, 1.0, 0.58)
    for r in range(5):
        assert abs(ink_in(out, r, outside) - ink_in(meta["receipts"], r, outside)) < 20
