"""Сквозная проверка экранов «Сортировка», «Картинка», «Страницы», «Сжатие», общих функций окна и крайних
случаев (пароль, битый файл, большой файл, длинные русские пути, папка только для чтения, тот же файл на
входе и выходе, закрытие во время работы). Проверяется содержимое результата."""
import os
import stat
import sys

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QMimeData, QModelIndex, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QProgressDialog

from app.core.pdfutil import NRect
from e2e_lib import app, blue_mask, crop, ink_in, page_count, pump, render, shot, toasts_after, wait_until

CLEAN = dict(apartments=["14", "27", "35", "48", "52"], receipts=["35", "14", "52", "48", "27"])


def clean(fake):
    return fake("clean", fake.Scenario(**CLEAN))


def same_page(a_pdf, a, b_pdf, b, dpi=50, tol=0.6):
    """Та же страница (копия без пересжатия даёт почти нулевую разницу)."""
    x, y = render(a_pdf, a, dpi), render(b_pdf, b, dpi)
    return x.shape == y.shape and np.abs(x.astype(int) - y.astype(int)).mean() < tol


def which_page(out, k, src):
    hits = [i for i in range(page_count(src)) if same_page(out, k, src, i)]
    assert len(hits) == 1, (k, hits)
    return hits[0]


def errors_after(w, n):
    return [t for kind, t in w.toasts_seen[n:] if kind == "error"]


def wait_recognized(w, n, word, timeout=180):
    assert wait_until(app, lambda: toasts_after(w, n, word), timeout)
    pump(app, 0.2)


# ---------------------------------------------------------------- Сортировка
def test_sort_by_recognized_numbers_manual_fix_and_save(win, fake, tmp_path):
    c = clean(fake)
    s = win.sort
    win.show_screen(s)
    s.open(c["receipts"])
    n = len(win.toasts_seen)
    s.recognize()
    wait_recognized(win, n, "Распознано")
    vals = s.values_by_page()
    assert vals == CLEAN["receipts"], vals
    s.sort_table()
    shot(win, "sort_sorted")
    out = str(tmp_path / "по_квартирам.pdf")
    s.save(out)
    order = [which_page(out, k, c["receipts"]) for k in range(5)]
    assert [CLEAN["receipts"][p] for p in order] == ["14", "27", "35", "48", "52"]
    # ручная правка: у страницы с «52» пишем «10» — она должна встать первой
    p52 = vals.index("52")
    v2 = list(vals)
    v2[p52] = "10"
    s.set_values(v2)
    s.sort_table()
    out2 = str(tmp_path / "по_квартирам_2.pdf")
    s.save(out2)
    assert which_page(out2, 0, c["receipts"]) == p52
    # повтор номера подсвечивается
    v2[vals.index("14")] = "27"
    s.set_values(v2)
    assert "27" in s.stat.text() and "повторы: 27" in s.stat.text()
    csv = str(tmp_path / "список.csv")
    original = open(c["receipts"], "rb").read()
    s.save_csv(c["receipts"])
    assert open(c["receipts"], "rb").read() == original
    s.save_csv(csv)
    text = open(csv, encoding="utf-8-sig").read()
    assert "27" in text and "10" in text
    s.undo_stack.undo()
    assert s.values_by_page()[vals.index("14")] == "14"


# ---------------------------------------------------------------- Картинка
def logo(path):
    im = Image.new("RGB", (300, 150), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((20, 10, 280, 140), fill=(30, 60, 200))
    im.save(path)
    return path


def test_tall_image_fits_and_undo_restores_name(win, tmp_path):
    pdf = tmp_path / "альбом.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=540, height=300)
        doc.save(pdf)
    tall = tmp_path / "маяк.png"
    Image.new("RGB", (200, 620), "red").save(tall)
    t = win.image
    win.show_screen(t)
    t.open_pdf(str(pdf))
    t.open_image(str(tall))
    rect = t.view.get_nrect()
    assert 0 <= rect.x0 < rect.x1 <= 1 and 0 <= rect.y0 < rect.y1 <= 1
    assert (rect.x1 - rect.x0) * 540 / ((rect.y1 - rect.y0) * 300) == pytest.approx(200 / 620)
    other = logo(str(tmp_path / "парусник.png"))
    t.open_image(other)
    win.undo()
    assert t.img_path == str(tall) and tall.name in t.dz_img.t.toolTip()
    win.undo()
    assert t.view.box is None and t.dz_img.property("loaded") == "false"
    win.redo()
    win.redo()
    assert t.img_path == other and os.path.basename(other) in t.dz_img.t.toolTip()


def test_image_on_selected_pages_white_removed(win, fake, tmp_path):
    c = clean(fake)
    t = win.image
    win.show_screen(t)
    t.open_pdf(c["receipts"])
    t.open_image(logo(str(tmp_path / "логотип.png")))
    t.chk_white.setChecked(True)
    rect = NRect(0.05, 0.02, 0.35, 0.02 + 0.30 * 0.5 * 595 / 842)
    t.view.set_nrect(rect, t.view.boxes["main"].pixmap, emit=True)
    t.r_sel.setChecked(True)
    t.ed_pages.setText("1, 3-4")
    assert t.target_pages() == [0, 2, 3]
    out = str(tmp_path / "с_картинкой.pdf")
    t.save(out)
    for p in range(5):
        img = crop(render(out, p, 100), rect)
        has = blue_mask(img).mean() > 0.3
        assert has == (p in (0, 2, 3)), p
    # белый фон убран: угол картинки — прозрачный, страница под ним как была
    corner = NRect(rect.x0, rect.y0, rect.x0 + 0.02, rect.y0 + 0.01)
    a, b = crop(render(out, 0, 100), corner), crop(render(c["receipts"], 0, 100), corner)
    assert np.abs(a.astype(int) - b.astype(int)).mean() < 4


# ---------------------------------------------------------------- Страницы
def test_pages_delete_rotate_reorder_undo_save(win, fake, tmp_path):
    c = clean(fake)
    pg = win.pages
    win.show_screen(pg)
    pg.open(c["receipts"])
    assert pg.grid.count() == 5
    pg.grid.item(1).setSelected(True)
    pg.delete()                                   # без стр. 2
    assert pg.grid.count() == 4
    pg.grid.clearSelection()
    pg.grid.item(0).setSelected(True)
    pg.rotate(90)                                 # стр. 1 повернуть
    pg.grid.clearSelection()
    assert pg.grid.model().moveRow(QModelIndex(), 3, QModelIndex(), 0)   # последнюю — в начало
    pump(app, 0.1)
    order, rot = pg.state()
    assert order == [4, 0, 2, 3] and rot[0] == 90
    win.undo()                                    # Ctrl+Z — отменить перенос
    assert pg.state()[0] == [0, 2, 3, 4]
    win.redo()
    assert pg.state()[0] == [4, 0, 2, 3]
    out = str(tmp_path / "изменён.pdf")
    pg.save(out)
    with pymupdf.open(out) as d:
        assert len(d) == 4
        assert d[1].rotation == 90
    assert same_page(out, 0, c["receipts"], 4)
    assert same_page(out, 2, c["receipts"], 2) and same_page(out, 3, c["receipts"], 3)


def test_pages_merge_split_extract(win, fake, tmp_path):
    c = clean(fake)
    pg = win.pages
    win.show_screen(pg)
    pg.add_files([c["acts"], c["receipts"]])
    out = str(tmp_path / "объединённый.pdf")
    pg.do_merge(out)
    assert page_count(out) == 10
    assert same_page(out, 0, c["acts"], 0) and same_page(out, 5, c["receipts"], 0)
    pg.open(out)
    pg.ed_ranges.setText("1-2, 7")
    d1 = tmp_path / "части"
    d1.mkdir()
    pg.split_ranges(str(d1))
    parts = sorted(os.listdir(d1))
    assert len(parts) == 2, parts
    counts = sorted(page_count(str(d1 / p)) for p in parts)
    assert counts == [1, 2]
    one = [str(d1 / p) for p in parts if page_count(str(d1 / p)) == 1][0]
    assert same_page(one, 0, c["receipts"], 1), "извлечена стр. 7 = расписка 2"
    d2 = tmp_path / "по_одной"
    d2.mkdir()
    pg.split_every(str(d2))
    assert len(os.listdir(d2)) == 10
    n = len(win.toasts_seen)
    pg.ed_ranges.setText("3-99")
    pg.split_ranges(str(d1))
    assert errors_after(win, n), "неверный диапазон — понятная ошибка"


# ---------------------------------------------------------------- Сжатие
def test_compress_three_levels(win, fake, tmp_path):
    c = clean(fake)
    cz = win.compress
    win.show_screen(cz)
    cz.open(c["acts"])
    before = os.path.getsize(c["acts"])
    sizes = {}
    for lv in ("light", "medium", "strong"):
        cz.radios[lv].setChecked(True)
        out = str(tmp_path / f"сжатый_{lv}.pdf")
        n = len(win.toasts_seen)
        cz.save(out)
        if not os.path.exists(out):
            # сканы и так сжаты — для «Лёгкого» меньше может не стать; тогда файл не создаётся и это сказано
            assert lv == "light" and toasts_after(win, n, "Меньше не стало"), lv
            continue
        sizes[lv] = os.path.getsize(out)
        assert page_count(out) == 5
        # читается: страница та же, а подпись из сжатого акта программа берёт почти целиком
        from app.core import signatures as sig
        with pymupdf.open(out) as d, pymupdf.open(c["acts"]) as o:
            for a in range(5):
                got = (sig.cut_signature(d, a, sig.DEFAULT_SRC_RECT, 110)[..., 3] > 60).sum()
                ref = (sig.cut_signature(o, a, sig.DEFAULT_SRC_RECT, 110)[..., 3] > 60).sum()
                assert got > 0.75 * ref, (lv, a, got, ref)
        assert same_page(out, 0, c["acts"], 0, dpi=20, tol=4)
    assert sizes["strong"] < sizes["medium"] < before, (sizes, before)
    if "light" in sizes:
        assert sizes["medium"] < sizes["light"] < before


def big_pdf(src, path, times=30):
    d = pymupdf.open()
    with pymupdf.open(src) as s:
        for _ in range(times):
            d.insert_pdf(s)
    d.save(path)
    d.close()
    return path


def cancel_progress_soon(ms=400):
    def go():
        for wdg in app.topLevelWidgets():
            if isinstance(wdg, QProgressDialog) and wdg.isVisible() or isinstance(wdg, QProgressDialog):
                wdg.cancel()
    QTimer.singleShot(ms, go)


def test_compress_cancel_leaves_no_file(win, fake, tmp_path):
    c = clean(fake)
    big = big_pdf(c["acts"], str(tmp_path / "большой.pdf"), 30)
    cz = win.compress
    win.show_screen(cz)
    cz.open(big)
    out = str(tmp_path / "отмена.pdf")
    n = len(win.toasts_seen)
    cancel_progress_soon(300)
    cz.save(out)
    assert not os.path.exists(out)
    assert toasts_after(win, n, "Отменено")


# ---------------------------------------------------------------- Общее
def test_theme_settings_persist_and_tour(win, tmp_path):
    from app.ui import style
    from app.main import MainWindow
    was = style.is_dark()
    win.toggle_theme()
    assert style.is_dark() != was
    shot(win, "theme_toggled")
    w2 = MainWindow()                                  # «перезапуск»: тема запомнилась
    from app.ui.infra import settings
    assert settings().value("ui/theme") == ("dark" if not was else "light")
    w2.close()
    w2.deleteLater()
    win.set_theme_mode("light")
    assert not style.is_dark()
    for scr in win.all_tabs():
        win.show_screen(scr)
        t = win.start_tour()
        n = 0
        while win.tour is not None and n < 30:
            t.next()
            pump(app, 0.02)
            n += 1
        assert win.tour is None, f"подсказка на {type(scr).__name__} не закончилась"
        assert n == len([s for s in scr.tour_steps() if s.target is None or s.target.window() is win])


def test_drag_and_drop_and_open_files_route_to_screen(win, fake, tmp_path):
    c = clean(fake)
    win.show_screen(win.compress)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(c["acts"])])
    ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    win.dropEvent(ev)
    # на Windows Qt отдаёт путь со слэшами «/», а pytest — с «\»
    assert os.path.normcase(os.path.normpath(win.compress.path)) == os.path.normcase(os.path.normpath(c["acts"]))
    png = logo(str(tmp_path / "лого.png"))
    win.open_files([png])                         # одна картинка — на экран «Картинка»
    assert win.current() is win.image
    assert os.path.normcase(os.path.normpath(win.image.img_path)) == os.path.normcase(os.path.normpath(png))
    win.show_screen(win.helppage)
    win.open_files([c["receipts"]])               # со справки — на «Страницы»
    assert win.current() is win.pages and win.pages.grid.count() == 5


def test_last_folder_is_remembered(win, tmp_path):
    from app.ui.infra import last_dir, remember_dir
    f = tmp_path / "папка с актами" / "файл.pdf"
    f.parent.mkdir()
    remember_dir(str(f))  # диалог сохранения возвращает путь до создания файла
    assert last_dir() == str(f.parent)
    f.write_bytes(b"%PDF-1.4\n")
    remember_dir(str(f))
    assert last_dir() == str(f.parent)


# ---------------------------------------------------------------- Крайние случаи
def password_pdf(src, path):
    with pymupdf.open(src) as d:
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="секрет")
    return path


@pytest.mark.parametrize("kind", ["password", "broken", "empty", "not_pdf"])
def test_bad_files_give_clear_message_everywhere(win, fake, tmp_path, kind):
    c = clean(fake)
    p = str(tmp_path / f"{kind}.pdf")
    if kind == "password":
        password_pdf(c["receipts"], p)
    elif kind == "broken":
        data = open(c["receipts"], "rb").read()
        open(p, "wb").write(data[: len(data) // 3])
    elif kind == "empty":
        open(p, "wb").close()
    else:
        open(p, "wb").write(b"\x89PNG not a pdf at all" * 50)
    opens = [win.sign.open_acts, win.sign.open_receipts, win.fragment.open_src, win.fragment.open_dst,
             win.sort.open, win.image.open_pdf, win.pages.open, win.compress.open,
             lambda x: win.pages.add_files([x])]
    for fn in opens:
        n = len(win.toasts_seen)
        fn(p)
        errs = errors_after(win, n)
        if kind == "broken" and not errs:
            continue          # PyMuPDF иногда чинит обрезанный файл — тогда он просто открывается
        assert errs, (kind, fn)
        if kind == "password":
            assert "парол" in errs[0].lower(), errs[0]


def test_huge_pdf_pages_and_sort(win, fake, tmp_path):
    import time
    c = clean(fake)
    big = big_pdf(c["receipts"], str(tmp_path / "большой.pdf"), 60)      # 300 страниц сканов
    pg = win.pages
    win.show_screen(pg)
    t0 = time.time()
    pg.open(big)
    assert pg.grid.count() == 300
    pg.grid.item(0).setSelected(True)
    pg.delete()
    out = str(tmp_path / "большой_без_первой.pdf")
    pg.save(out)
    assert page_count(out) == 299
    win.sort.open(big)
    assert win.sort.table.rowCount() == 300
    assert time.time() - t0 < 120


def test_long_cyrillic_paths(win, fake, tmp_path):
    c = clean(fake)
    deep = tmp_path / ("Акты и расписки — дом №5, корпус «Б» " * 3).strip() / "Ноябрь 2026 (копия)"
    deep.mkdir(parents=True)
    src = deep / ("расписки жильцов подъезд 2 этажи 1–9 " * 2 + ".pdf")
    src.write_bytes(open(c["receipts"], "rb").read())
    assert len(str(src)) > 200
    pg = win.pages
    pg.open(str(src))
    out = deep / "результат — изменён.pdf"
    pg.save(str(out))
    assert page_count(str(out)) == 5
    cz = win.compress
    cz.open(str(src))
    cz.save(str(deep / "сжатый.pdf"))


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="права папки POSIX")
def test_read_only_output_folder(win, fake, tmp_path):
    c = clean(fake)
    ro = tmp_path / "только_чтение"
    ro.mkdir()
    out = str(ro / "результат.pdf")
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        for screen, act in [(win.pages, lambda: (win.pages.open(c["receipts"]), win.pages.save(out))),
                            (win.compress, lambda: (win.compress.open(c["acts"]), win.compress.save(out))),
                            (win.sort, lambda: (win.sort.open(c["receipts"]), win.sort.save(out))),
                            (win.sign, lambda: (win.sign.open_acts(c["acts"]), win.sign.open_receipts(c["receipts"]),
                                                win.sign.save(out)))]:
            win.show_screen(screen)
            n = len(win.toasts_seen)
            act()
            errs = errors_after(win, n)
            assert errs, type(screen).__name__
            assert "Traceback" not in errs[0]
            assert not os.path.exists(out)
    finally:
        os.chmod(ro, stat.S_IRWXU)


def test_same_file_as_input_and_output(win, fake, tmp_path):
    c = clean(fake)
    p = str(tmp_path / "один_и_тот_же.pdf")
    open(p, "wb").write(open(c["receipts"], "rb").read())
    size = os.path.getsize(p)
    for screen, act in [(win.pages, lambda: (win.pages.open(p), win.pages.save(p))),
                        (win.compress, lambda: (win.compress.open(p), win.compress.save(p))),
                        (win.sort, lambda: (win.sort.open(p), win.sort.save(p))),
                        (win.image, lambda: (win.image.open_pdf(p), win.image.open_image(logo(str(tmp_path / "l.png"))),
                                             win.image.save(p)))]:
        win.show_screen(screen)
        n = len(win.toasts_seen)
        act()
        pump(app, 0.1)
        # либо понятный отказ, либо файл перезаписан корректно — но не испорчен
        assert page_count(p) == 5, type(screen).__name__
        assert not any("Traceback" in t for t in errors_after(win, n))
    assert os.path.getsize(p) > size * 0.2


def test_close_during_batch_and_with_unsaved(win, fake, tmp_path, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox
    c = clean(fake)
    b = win.batch
    outs = [str(tmp_path / f"п{i}.pdf") for i in range(3)]
    for o in outs:
        b.add_pair(c["acts"], c["receipts"], out=o)
    b.run_all()
    assert wait_until(app, lambda: b.bar.value() > 0, 60)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (asked.append(a[1]), QMessageBox.Yes)[1]))
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert asked and "пакет" in asked[0].lower()
    assert ev.isAccepted()
    assert not b.running or wait_until(app, lambda: not b.running, 60)
    done = [o for o in outs if os.path.exists(o)]
    for o in done:
        assert page_count(o) == 5, "готовые файлы целые"
    # несохранённые изменения: спросить, «Нет» — не закрывать
    pg = win.pages
    pg.open(c["receipts"])
    pg.grid.item(0).setSelected(True)
    pg.delete()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert not ev.isAccepted()
