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
    win.show_screen(win.sort)
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


# ---------- v1.3: интерфейс и пошаговые подсказки ----------
def _screens(win):
    return [win.sign, win.batch, win.fragment, win.sort, win.image, win.pages, win.text, win.helppage]


@pytest.mark.parametrize("size", [(1366, 768), (900, 560)])
def test_tours_reference_real_widgets_and_run(win, size):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QWidget
    from app.ui.tour import TourOverlay
    win.resize(*size)       # маленькое окно: подсказка должна сама прокрутить экран к элементу
    for scr in _screens(win):
        win.show_screen(scr)
        app.processEvents()
        steps = scr.tour_steps()
        assert len(steps) >= (1 if scr is win.helppage else 6), scr
        for st in steps:
            assert st.title and st.text
            if st.target is not None:
                assert isinstance(st.target, QWidget)
                assert scr.isAncestorOf(st.target), (scr, st.title)   # элемент на этом же экране
        tour = win.start_tour()
        assert isinstance(tour, TourOverlay)
        for i in range(len(tour.steps)):
            tour.go(i)
            app.processEvents()
            st = tour.steps[i]
            if st.target is not None:
                assert st.target.isVisible(), (scr, st.title)          # «Дополнительно» раскрывается само
                assert not tour.target_rect().isNull()
            assert QRect(0, 0, tour.width(), tour.height()).contains(tour.bubble.geometry()), (scr, st.title)
            assert tour.count.text() == f"Шаг {i + 1} из {len(tour.steps)}"
        tour.next()                       # «Готово» на последнем шаге закрывает
        app.processEvents()
        assert win.tour is None
    # «Дополнительно» после подсказки снова свёрнуто
    assert not win.sign.advanced.is_expanded()


def test_tour_keys_and_header_button(win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win.show_screen(win.sort)
    app.processEvents()
    win.btn_help.click()
    t = win.tour
    assert t is not None and t.i == 0
    QTest.keyClick(t, Qt.Key_Right)
    assert t.i == 1
    QTest.keyClick(t, Qt.Key_Left)
    assert t.i == 0
    QTest.keyClick(t, Qt.Key_Tab)
    assert t.b_close.hasFocus()
    QTest.keyClick(t.b_close, Qt.Key_Backtab)
    assert t.b_next.hasFocus()
    QTest.keyClick(t.b_next, Qt.Key_Tab)
    assert t.b_close.hasFocus()
    QTest.keyClick(t.b_close, Qt.Key_Escape)
    app.processEvents()
    assert win.tour is None and win.sort.header.help.hasFocus()
    win.sort.header.help.click()          # «Как пользоваться» на экране
    assert win.tour is not None
    win.tour.close_tour()


def test_first_visit_offer_once(win):
    from app.ui.infra import settings
    win._toasts.clear()
    win.offer_tour("image")               # ушли с экрана до появления предложения
    assert not settings().value("tour/image", False, type=bool)
    win.show_screen(win.pages)
    n = len(win._toasts)
    win.offer_tour("pages")
    assert len(win._toasts) == n + 1 and "Показать" in [b.text() for b in win._toasts[-1].findChildren(
        __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)]
    win.offer_tour("pages")               # второй раз не предлагаем
    assert len(win._toasts) == n + 1


def _sample_pdfs(tmp_path):
    """Сгенерированные PDF (реальные файлы в репозиторий не кладём). Путь с пробелом и кириллицей —
    как на Windows-рабочем столе."""
    import pymupdf
    d = tmp_path / "Мои документы"
    d.mkdir()
    acts, rec = str(d / "акты 1.pdf"), str(d / "расписки 1.pdf")
    for path, blue in ((acts, True), (rec, False)):
        doc = pymupdf.open()
        for k in range(3):
            pg = doc.new_page(width=595, height=842)
            pg.insert_text((72, 80), f"Page {k + 1}", fontsize=14)
            pg.draw_line((72, 700), (300, 700), color=(0, 0, 0))
            if blue:
                pg.draw_bezier((90, 690), (140, 640), (190, 740), (260, 680), color=(0.1, 0.2, 0.8), width=2)
        doc.save(path)
    return acts, rec


def test_steps_toast_dropzone_theme(win, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from app.ui import style
    from app.ui.widgets import info
    s = win.sign
    win.show_screen(s)
    assert s.header.steps.state(0) == "active"
    acts, rec = _sample_pdfs(tmp_path)
    # перетаскивание файлов на зоны
    for dz, path in ((s.dz_acts, acts), (s.dz_rec, rec)):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        dz.dropEvent(ev)
    assert s.acts is not None and s.rec is not None
    win.refresh_steps()
    assert s.header.steps.state(0) == "done" and s.header.steps.state(1) == "active"
    s.save(str(tmp_path / "x.pdf"))
    win.refresh_steps()
    assert [s.header.steps.state(i) for i in range(3)] == ["done"] * 3
    assert any("Готово" in t.label.text() for t in win._toasts)        # уведомление вместо окна
    n = len(win._toasts)
    info(s, "проверка")
    assert len(win._toasts) == n + 1
    win._toasts[-1].close_now()
    # перетаскивание на окно уходит текущему экрану
    win.show_screen(win.pages)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(rec), QUrl.fromLocalFile(acts)])
    win.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
    assert win.pages.merge_list.count() == 2
    # тема
    dark0 = style.is_dark()
    win.toggle_theme()
    assert style.is_dark() != dark0
    win.toggle_theme()
    assert style.is_dark() == dark0


def test_buttons_have_tooltips_and_no_emoji(win):
    from PySide6.QtWidgets import QAbstractButton
    bad = []
    for scr in _screens(win):
        for b in scr.findChildren(QAbstractButton):
            if b.metaObject().className() in ("QRadioButton", "QCheckBox") and not b.toolTip():
                continue
            if not b.toolTip() and b.text() not in ("Отмена",):
                if b.parent() and b.parent().metaObject().className() in ("QTableWidget",):
                    continue
                if not b.text() and not b.icon().isNull():
                    bad.append((type(scr).__name__, "icon-only без подсказки"))
                elif b.text() and b.isVisibleTo(scr):
                    bad.append((type(scr).__name__, b.text()))
            assert not any(ord(ch) > 0x2700 for ch in b.text()), b.text()   # эмодзи заменены иконками
    assert not bad, bad


SYN = os.path.join(os.path.dirname(__file__), "..", "qa", "samples", "source_synthetic.pdf")


@pytest.mark.skipif(not os.path.exists(JOB2B), reason="нет job2")
def test_fragment_screen_flow(win, tmp_path):
    """«Вырезать и перенести»: подпись из акта job2 + печать из синтетического образца в расписку job2."""
    import pymupdf
    from app.core.pdfutil import NRect
    f = win.fragment
    win.show_screen(f)
    assert f.step_index() == 0
    f.open_src(JOB2B)
    f.open_dst(JOB2A)
    f.cb_mode.setCurrentIndex(f.cb_mode.findData("blue"))
    f.view_src.set_nrect(NRect(0.0665, 0.729, 0.671, 0.792))
    f.recut()
    assert f.cur_rgba is not None and (f.cur_rgba[..., 3] > 100).sum() > 500
    d = f.view_dst.get_nrect()
    assert d is not None and 0 <= d.x0 < d.x1 <= 1
    f.place_current()
    assert len(f.placed) == 1 and f.step_index() == 2
    f.open_src(SYN)
    f.cb_mode.setCurrentIndex(f.cb_mode.findData("color"))
    f.view_src.set_nrect(NRect(0.445, 0.582, 0.675, 0.738))
    f.recut()
    f.view_dst.set_nrect(NRect(0.55, 0.70, 0.75, 0.84), f.view_dst.boxes["main"].pixmap)
    f.commit("место")
    f.undo_stack.undo()
    f.undo_stack.redo()
    out = str(tmp_path / "frag.pdf")
    f.save(out)
    assert f.step_index() == 3
    before = pymupdf.open(JOB2A)
    after = pymupdf.open(out)
    assert len(after) == len(before)
    assert len(after[0].get_images()) > len(before[0].get_images())
    # исходники не тронуты
    with pytest.raises(Exception):
        f.save(JOB2A)
        raise RuntimeError("save over input must be refused")


def test_fragment_needs_files(win):
    f = win.fragment
    n = len(win._toasts)
    f.save("/tmp/never.pdf")          # нечего сохранять — понятное уведомление, без падения
    assert len(win._toasts) == n + 1


@pytest.mark.parametrize("scope", ["cur", "all", "sel"])
@pytest.mark.parametrize("white", [False, True])
def test_image_screen_position_rotated(win, tmp_path, scope, white):
    """Картинка стоит ровно там, куда её перетащили, на всех выбранных страницах, в т.ч. повёрнутых."""
    import numpy as np
    import pymupdf
    from app.core.pdfutil import NRect
    src = str(tmp_path / "rot.pdf")
    with pymupdf.open() as doc:
        for _ in range(5):
            doc.new_page()
        doc[1].set_rotation(90)
        doc[3].set_rotation(270)
        doc.save(src)
    png = str(tmp_path / "i.png")
    from PIL import Image
    Image.new("RGB", (300, 60), (20, 40, 200)).save(png)
    t = win.image
    t.open_pdf(src)
    t.open_image(png)
    t.chk_white.setChecked(white)
    t.spin.setValue(2)
    nr = NRect(0.55, 0.15, 0.85, 0.15 + 0.06)
    t.view.set_nrect(nr, t.view.boxes["main"].pixmap)
    {"cur": t.r_cur, "all": t.r_all, "sel": t.r_sel}[scope].setChecked(True)
    t.ed_pages.setText("2-4")
    out = str(tmp_path / "o.pdf")
    t.save(out)
    exp = set(t.target_pages())

    def rend(p, i):
        px = p[i].get_pixmap(dpi=40)
        return np.frombuffer(px.samples, np.uint8).reshape(px.height, px.width, px.n)[..., :3].astype(int)
    a, b = pymupdf.open(src), pymupdf.open(out)
    for i in range(len(a)):
        diff = np.abs(rend(a, i) - rend(b, i)).max(2) > 40
        if i not in exp:
            assert not diff.any()
            continue
        ys, xs = np.where(diff)
        H, W = diff.shape
        assert len(xs) > 20
        rh = nr.y1 - nr.y0
        assert xs.min() >= nr.x0 * W - 3 and xs.max() <= nr.x1 * W + 3
        assert ys.min() >= nr.y0 * H - 3 and ys.max() <= (nr.y0 + rh * 2) * H + 3
