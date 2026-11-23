"""Исправления по итогам проверки 1.4.0 на Windows: пакет во время работы, понятные ошибки записи,
имена файлов Windows, повреждённые PDF, окно хода в отдельном потоке (окно не «Не отвечает»)."""
import os
import sys
import tempfile
import threading
import time

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestWinQA"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
FONT = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "assets", "fonts", "Inter-Regular.ttf")


def _pdf(path, n=3, sign=True):
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 80), f"Акт кв. № {i + 1}", fontname="inter", fontfile=FONT, fontsize=14)
        if sign:
            p.draw_bezier((90, 690), (140, 640), (190, 740), (260, 680), color=(0.1, 0.2, 0.8), width=2)
    d.save(path)
    d.close()
    return path


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
    toasts = []
    orig = w.toast
    w.toast = lambda text, kind="info", **k: (toasts.append((kind, text)), orig(text, kind, **k))[1]
    w.toasts = toasts
    yield w
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(60000)
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


# ---------- «Пакет»: нельзя менять список во время работы (раньше — IndexError и окно «Ошибка») ----------
def test_batch_locked_while_running(win, tmp_path):
    from PySide6.QtCore import QThreadPool
    acts, rec = _pdf(str(tmp_path / "акты.pdf"), 30), _pdf(str(tmp_path / "расписки.pdf"), 30, sign=False)
    b = win.batch
    for k in range(6):
        b.add_pair(acts, rec, None, str(tmp_path / f"п{k}.pdf"))
    errors = []
    old = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a)
    try:
        b.run_all()
        assert b.running and not b.btn_run.isEnabled() and b.btn_cancel.isEnabled()
        b.table.setCurrentCell(0, 0)
        b.remove_pair()
        b.add_pair(acts, rec, None, str(tmp_path / "x.pdf"))
        win.show_screen(b)
        win.undo()
        assert len(b.items) == 6
        assert any("Идёт обработка" in t for _, t in win.toasts)
        for _ in range(600):
            app.processEvents()
            QThreadPool.globalInstance().waitForDone(50)
            if b.last_results:
                break
        app.processEvents()
    finally:
        sys.excepthook = old
    assert not errors
    assert not b.running and b.btn_run.isEnabled()
    assert all(s.startswith("готово") for s in b.status)
    b._summary.close()


def test_batch_done_tolerates_shorter_list(win):
    """Даже если список стал короче (старые данные), итог не падает."""
    b = win.batch
    b.status = []
    b.items = []
    res = dict(pair=3, out="o.pdf", status="ok", placed=1, blank=[], empty_ink=[], error="")
    assert b.describe(res).startswith("готово")


# ---------- понятные сообщения вместо «code=2: cannot open file … Permission denied» ----------
def test_human_error_translates_mupdf():
    from app.core.pdfutil import human_error
    t = human_error("code=2: cannot open file 'C:/x/~a.tmp': Permission denied")
    assert t.startswith("Нет доступа") and "техническая причина" in t
    assert human_error("code=2: cannot write: No space left on device").startswith("На диске не хватает места")
    assert human_error("code=4: source object number out of range").startswith("PDF повреждён")
    assert human_error("Обычное сообщение") == "Обычное сообщение"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="права через chmod — только Unix не от root")
def test_save_into_readonly_folder_is_clear(tmp_path):
    from app.core import pages
    from app.core.pdfutil import PdfError
    src = _pdf(str(tmp_path / "a.pdf"))
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o555)
    try:
        with pytest.raises(PdfError) as ei:
            pages.rebuild(src, [0, 1], {}, str(ro / "x.pdf"))
        assert "code=" not in str(ei.value) and "Не удалось записать" in str(ei.value)
        assert os.listdir(ro) == []
    finally:
        os.chmod(ro, 0o755)


def test_error_toast_is_human(win):
    from app.ui.widgets import error
    error(win.pages, "code=2: cannot open file 'x': Permission denied")
    assert win.toasts[-1][1].startswith("Нет доступа")


def test_long_output_name_works(tmp_path):
    """Временный файл теперь с коротким именем: длинное (но допустимое) имя результата сохраняется."""
    from app.core import pages
    src = _pdf(str(tmp_path / "a.pdf"))
    out = str(tmp_path / ("д" * 120 + ".pdf"))       # 124 символа: 248 байт UTF-8 — у старого «~имя.pid.tmp» >255
    pages.rebuild(src, [0], {}, out)
    assert os.path.exists(out)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


# ---------- имена, недопустимые в Windows ----------
@pytest.mark.parametrize("name,ok", [("итог.pdf", True), ("CON.pdf", False), ("aux.pdf", False), ("com1.pdf", False),
                                     ("nul", False), ("файл?.pdf", False), ("a:b.pdf", False), ("имя .", False),
                                     ("имя.pdf ", False), ("a" * 260 + ".pdf", False), ("console.pdf", True),
                                     ("имя с точкой..pdf", True)])
def test_windows_name_problem(name, ok):
    from app.core.pdfutil import windows_name_problem
    assert (windows_name_problem(name) == "") is ok


def test_reserved_name_refused_on_windows(tmp_path, monkeypatch):
    from app.core import pdfutil
    monkeypatch.setattr(pdfutil.sys, "platform", "win32")
    with pytest.raises(pdfutil.PdfError, match="зарезервировано"):
        pdfutil.check_output_path(str(tmp_path / "CON.pdf"))


# ---------- повреждённый PDF, который открывается, но страницы не читаются ----------
def test_merge_damaged_pdf_clear_message(tmp_path):
    from app.core import pages
    from app.core.pdfutil import PdfError
    good = _pdf(str(tmp_path / "хороший.pdf"))
    bad = str(tmp_path / "битый.pdf")
    with open(bad, "wb") as f:
        f.write(open(good, "rb").read()[:700])
    out = str(tmp_path / "o.pdf")
    try:
        pages.merge([good, bad], out)
    except PdfError as e:
        assert "битый.pdf" in str(e) and "code=" not in str(e)
    assert not os.path.exists(out) or pymupdf.open(out).page_count >= 3


# ---------- окно хода: работа в отдельном потоке, окно отвечает, «Отмена», закрытие заблокировано ----------
def test_progress_run_in_thread_keeps_window_alive(win):
    from PySide6.QtCore import QTimer
    from app.ui.widgets import progress_window
    beats, threads = [], []
    t = QTimer()
    t.setInterval(20)
    t.timeout.connect(lambda: beats.append(time.monotonic()))
    t.start()

    def work(n, progress=None):
        threads.append(threading.current_thread().name)
        for i in range(n):
            progress(i, n)
            time.sleep(0.05)       # «тяжёлая» работа, без processEvents
        return "готово"
    with progress_window(win.pages, "Проверка…") as prog:
        assert prog.run(work, 20) == "готово"
    t.stop()
    assert threads == ["pdftool-save"]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert beats and max(gaps) < 0.5          # окно всё время обрабатывало события


def test_progress_run_cancel_and_errors(win):
    from PySide6.QtCore import QTimer
    from app.core.pdfutil import Cancelled, PdfError
    from app.ui.widgets import progress_window

    def slow(progress=None):
        for i in range(200):
            progress(i, 200)
            time.sleep(0.02)
    with progress_window(win.pages, "Проверка…") as prog:
        QTimer.singleShot(300, prog.dlg.cancel)
        with pytest.raises(Cancelled):
            prog.run(slow)

    def bad(progress=None):
        raise PdfError("сломалось")
    with progress_window(win.pages, "Проверка…") as prog:
        with pytest.raises(PdfError, match="сломалось"):
            prog.run(bad)
    assert QApplication.overrideCursor() is None


def test_close_blocked_during_save(win):
    from PySide6.QtCore import QTimer
    from app.ui.widgets import progress_window
    closed = []

    def work(progress=None):
        for i in range(30):
            progress(i, 30)
            time.sleep(0.03)
    with progress_window(win.pages, "Проверка…") as prog:
        QTimer.singleShot(200, lambda: closed.append(win.close()))
        prog.run(work)
    assert closed == [False] and win.isVisible()
    assert any("Идёт сохранение" in t for _, t in win.toasts)


def test_pages_ops_use_progress_and_cancel(win, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QProgressDialog
    src = _pdf(str(tmp_path / "a.pdf"), 40)
    p = win.pages
    p.open(src)
    out_dir = tmp_path / "parts"
    out_dir.mkdir()
    p.split_every(str(out_dir))
    assert len(os.listdir(out_dir)) == 40
    out = str(tmp_path / "rot.pdf")
    p.grid.selectAll()
    p.rotate(90)
    p.save(out)
    assert pymupdf.open(out)[0].rotation == 90
    p.merge_list.clear()
    p.add_files([src, src])
    p.do_merge(str(tmp_path / "m.pdf"))
    assert pymupdf.open(str(tmp_path / "m.pdf")).page_count == 80
    # отмена «каждая отдельно» на середине
    import app.core.pages as pg
    orig = pg.split

    def slow_split(*a, progress=None, **k):
        def pr(i, n):
            time.sleep(0.03)
            progress(i, n)
        return orig(*a, progress=pr, **k)
    pg.split = slow_split
    try:
        def cancel():
            for d in p.findChildren(QProgressDialog):
                if d.isVisible():
                    d.cancel()
                    return
            QTimer.singleShot(100, cancel)
        QTimer.singleShot(600, cancel)
        d2 = tmp_path / "parts2"
        d2.mkdir()
        p.split_every(str(d2))
    finally:
        pg.split = orig
    assert 0 < len(os.listdir(d2)) < 40
    assert any("Остановлено" in t for _, t in win.toasts)


def test_locked_input_says_locked(tmp_path, monkeypatch):
    """Файл, занятый другой программой без общего доступа (на Windows так держит файл, например,
    программа сканера), раньше назывался «повреждён или это не PDF»."""
    import builtins
    from app.core.pdfutil import PdfError, open_pdf
    src = _pdf(str(tmp_path / "занят.pdf"))
    real_open = builtins.open

    def fake_open(p, *a, **k):
        if str(p) == src:
            raise PermissionError(13, "Permission denied")
        return real_open(p, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(PdfError, match="открыт в другой программе"):
        open_pdf(src)


def test_window_fits_small_screen(win):
    """На экране меньше окна (ноутбук 1366×768 с панелью задач, масштаб 150%) окно разворачивается,
    кнопки внизу не уходят под панель задач."""
    from app.main import fit_to_screen
    s = win.screen().availableGeometry()
    win.showNormal()
    win.resize(s.width() + 300, s.height() + 200)
    app.processEvents()
    assert fit_to_screen(win) == "maximized"
    app.processEvents()
    win.showNormal()
    win.resize(max(300, s.width() // 2), max(300, s.height() // 2))
    app.processEvents()
    assert fit_to_screen(win) == "centered"
    assert s.contains(win.frameGeometry().topLeft())


# ---------- вторая проверка 1.4.0 (Windows 10 / Server 2022, обрезанный PDF) ----------
def _truncated(tmp_path, name="битый.pdf"):
    good = _pdf(str(tmp_path / f"целый_{name}"))
    bad = str(tmp_path / name)
    with open(bad, "wb") as f:
        f.write(open(good, "rb").read()[:700])
    return good, bad


def test_truncated_pdf_refused_on_open(tmp_path):
    """Обрезанный (недокачанный) PDF MuPDF «чинит» и открывает, но страницы ссылаются на отсутствующие
    объекты. Раньше он попадал в «Объединить» и «Пакет» и падал только при сохранении."""
    from app.core.pdfutil import PdfError, open_pdf
    good, bad = _truncated(tmp_path)
    open_pdf(good).close()
    with pytest.raises(PdfError, match="повреждён") as e:
        open_pdf(bad)
    assert "битый.pdf" in str(e.value) and "code=" not in str(e.value)


def test_page_without_contents_is_not_broken(tmp_path):
    from app.core.pdfutil import open_pdf
    d = pymupdf.open()
    d.new_page()                     # пустая страница без /Contents — это нормальный PDF
    p = str(tmp_path / "пустая страница.pdf")
    d.save(p)
    open_pdf(p).close()


def test_batch_refuses_truncated_pair_before_start(tmp_path):
    from app.core import batch
    good, bad = _truncated(tmp_path)
    it = batch.BatchItem(acts_path=good, receipts_path=bad, out_path=str(tmp_path / "итог.pdf"))
    errs = batch.check_items([it])
    assert errs and "битый.pdf" in errs[0]


def test_reserved_name_when_abspath_maps_to_device(tmp_path, monkeypatch):
    """Windows 10 / Server 2022: abspath("C:\\папка\\CON.pdf") == "\\\\.\\CON". Сообщение должно быть
    про зарезервированное имя, а не «Папки «\\\\.\\CON» нет»."""
    from app.core import batch, pdfutil
    monkeypatch.setattr(pdfutil.sys, "platform", "win32")
    monkeypatch.setattr(batch.sys, "platform", "win32")
    real = os.path.abspath
    monkeypatch.setattr(os.path, "abspath",
                        lambda p: "\\\\.\\CON" if os.path.basename(str(p)).upper().startswith("CON") else real(p))
    with pytest.raises(pdfutil.PdfError, match="зарезервировано"):
        pdfutil.check_output_path(str(tmp_path / "CON.pdf"))
    good = _pdf(str(tmp_path / "акты.pdf"))
    it = batch.BatchItem(acts_path=good, receipts_path=good, out_path=str(tmp_path / "CON.pdf"))
    errs = batch.check_items([it])
    assert any("зарезервировано" in e for e in errs) and not any("нет папки" in e for e in errs)


def test_icon_buttons_have_accessible_names_in_compact_window(win):
    """Узкое окно (ноутбук 1366×768 при 150%): в боковой панели только значки. У кнопок должно остаться имя
    для экранного диктора Windows и UI Automation — иначе их «не видно» без текста."""
    from PySide6.QtWidgets import QAbstractButton
    win.set_compact(True)
    for key, b in win.nav.items():
        assert b.text() == "" and b.accessibleName(), key
    for b in (win.btn_undo, win.btn_redo, win.btn_theme, win.btn_more, win.btn_help):
        assert b.accessibleName()
    win.set_compact(False)
    assert win.nav["compress"].text() == "Сжатие"
    unnamed = [b for b in win.findChildren(QAbstractButton)
               if b.isVisibleTo(win) and not b.text() and not b.accessibleName() and b.toolTip()]
    assert not unnamed, [b.toolTip() for b in unnamed][:5]
