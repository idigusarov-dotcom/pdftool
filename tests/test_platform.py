"""Версия для Mac и новое оформление: подписи ⌘, тема «как в системе», узкое окно, файлы от системы."""
import os
import subprocess
import sys
import tempfile

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestPlatform"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QLineEdit, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
FONT = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "assets", "fonts", "Inter-Regular.ttf")


def _pdf(path, n=2):
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page()
        p.insert_text((72, 100), f"Акт кв. № {i + 1}", fontfile=FONT, fontname="inter", fontsize=14)
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
    w.show()
    app.processEvents()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


def test_key_labels(monkeypatch):
    from app.ui import style
    monkeypatch.setattr(style, "IS_MAC", True)
    assert style.key("Ctrl + Z — отменить") == "⌘Z — отменить"
    assert style.key("Ctrl+Shift+Z") == "⇧⌘Z"
    assert style.key("Масштаб — Ctrl+колесо").endswith("⌘ + колесо")
    assert "щипок" in style.key("Ctrl + колесо мыши")
    assert style.key("(Ctrl — несколько)") == "(⌘ — несколько)"
    monkeypatch.setattr(style, "IS_MAC", False)
    assert style.key("Ctrl + Z") == "Ctrl + Z"


def test_data_dir_on_mac(monkeypatch, tmp_path):
    from app.core import logsetup
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))     # expanduser на Windows смотрит сюда
    d = logsetup.log_dir()
    want = os.path.join(str(tmp_path), "Library", "Application Support", "PdfTool")
    assert os.path.normcase(os.path.normpath(d)) == os.path.normcase(want) and os.path.isdir(d)


def test_resources_found():
    from app.core.resources import resource
    assert os.path.isfile(resource("core", "models", "digits_cnn.npz"))
    assert os.path.isfile(resource("ui", "assets", "icons", "app-logo.png"))


def test_theme_mode_and_old_setting(win):
    from app import main
    from app.ui import style
    from app.ui.infra import settings
    s = settings()
    s.remove("ui/theme")
    s.setValue("ui/dark", True)                 # пользователь 1.3 включал тёмную тему кнопкой
    assert main.theme_mode() == "dark"
    s.remove("ui/dark")
    assert main.theme_mode() == "system"        # новый пользователь — как в системе
    win.set_theme_mode("dark")
    assert style.is_dark() and s.value("ui/theme") == "dark"
    win.set_theme_mode("light")
    assert not style.is_dark()
    win.set_theme_mode("system")
    assert style.is_dark() == style.system_dark()
    dlg = win.preferences()
    app.processEvents()
    assert dlg.isVisible() and dlg.isModal() and win.preferences() is dlg
    assert dlg.radios["system"].hasFocus()
    QTest.mouseClick(dlg.radios["dark"], Qt.LeftButton, pos=QPoint(8, dlg.radios["dark"].height() // 2))
    assert style.is_dark() and main.theme_mode() == "dark"
    QTest.mouseClick(dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Close), Qt.LeftButton)
    assert not dlg.isVisible()
    dlg = win.preferences()
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert not dlg.isVisible()


def test_edit_with_file_dialogs_and_history_buttons(win, tmp_path):
    pdf = _pdf(str(tmp_path / "Исходник.pdf"))
    output = str(tmp_path / "Исправлено.pdf")
    pages = win.pages
    QTest.mouseClick(win.nav["pages"], Qt.LeftButton)
    assert win.current() is pages
    dialogs = []

    def choose(path=None):
        dialog = app.activeModalWidget()
        if isinstance(dialog, QFileDialog):
            dialogs.append(dialog.acceptMode())
            if path:
                filename = dialog.findChild(QLineEdit, "fileNameEdit")
                filename.setText(path)
                QTest.keyClick(filename, Qt.Key_Return)
                QTimer.singleShot(3000, dialog, dialog.reject)
            else:
                QTest.keyClick(dialog, Qt.Key_Escape)

    attr = Qt.AA_DontUseNativeDialogs
    previous = app.testAttribute(attr)
    app.setAttribute(attr, True)
    try:
        pages.start_one.button.setFocus()
        QTimer.singleShot(100, choose)
        QTest.keyClick(pages.start_one.button, Qt.Key_Space)
        assert not pages.path
        QTest.mousePress(pages.start_one.button, Qt.LeftButton)
        QTest.mouseRelease(pages.start_one.button, Qt.LeftButton, pos=QPoint(-4, -4))
        QTimer.singleShot(100, lambda: choose(pdf))
        QTest.keyClick(pages.start_one.button, Qt.Key_Return)
        assert os.path.samefile(pages.path, pdf) and not win.btn_undo.isEnabled()
        first = pages.grid.visualItemRect(pages.grid.item(0)).center()
        QTest.mouseClick(pages.grid.viewport(), Qt.LeftButton, pos=first)
        QTest.mouseClick(pages.btn_rot, Qt.LeftButton)
        assert pages.state()[1][0] == 90 and win.btn_undo.isEnabled()
        QTest.mouseClick(win.btn_undo, Qt.LeftButton)
        assert pages.state()[1][0] == 0 and win.btn_redo.isEnabled()
        QTest.mouseClick(win.btn_redo, Qt.LeftButton)
        QTimer.singleShot(100, lambda: choose(output))
        QTest.mouseClick(pages.btn_save, Qt.LeftButton)
    finally:
        app.setAttribute(attr, previous)
    assert dialogs == [QFileDialog.AcceptOpen, QFileDialog.AcceptOpen, QFileDialog.AcceptSave]
    with pymupdf.open(output) as doc:
        assert len(doc) == 2 and doc[0].rotation == 90 and doc[1].rotation == 0


def test_thumbnails_follow_visible_grid(win, tmp_path):
    from app.ui.thumbs import visible_rows

    win.show_screen(win.pages)
    grid = win.pages.grid
    path = str(tmp_path / "pages.pdf")
    with pymupdf.open() as doc:
        for _ in range(300):
            doc.new_page()
        doc.save(path)
    win.pages.open(path)
    for width in (1024, 1280):
        win.resize(width, 720)
        app.processEvents()
        for index in (0, 143, 299):
            grid.scrollToItem(grid.item(index), grid.ScrollHint.PositionAtCenter)
            app.processEvents()
            queued = visible_rows(grid)
            visible = [i for i in range(grid.count())
                       if grid.viewport().rect().intersects(grid.visualItemRect(grid.item(i)))]
            assert index in visible
            assert all(i in queued for i in visible)
            assert len(queued) < 50


def test_preview_extract_and_save_current_pages(win, tmp_path, monkeypatch):
    from PySide6.QtCore import QModelIndex
    from PySide6.QtWidgets import QDialog, QSpinBox
    from app.ui import tab_pages
    from app.ui.widgets import PageView

    pg = win.pages
    win.show_screen(pg)
    assert not any(b.isEnabled() for b in (pg.btn_save, pg.btn_preview, pg.btn_extract, pg.btn_del, pg.btn_merge))
    pdf = _pdf(str(tmp_path / "Оригинал.pdf"), n=4)
    pg.open(pdf)
    assert pg.grid.model().moveRow(QModelIndex(), 3, QModelIndex(), 0)
    pg.grid.setCurrentRow(0)
    pg.grid.item(2).setSelected(True)
    assert pg.selection_count.text() == "Выбрано 2 из 4"
    QTest.mouseClick(pg.btn_rot, Qt.LeftButton)
    win.undo()
    win.redo()
    assert len(pg.grid.selectedItems()) == 2
    pg.grid.setFocus()
    QTest.keyClick(pg.grid, Qt.Key_Return)
    app.processEvents()
    dlg = app.activeModalWidget()
    assert isinstance(dlg, QDialog) and dlg.objectName() == "PagesPreview"
    view = dlg.findChild(PageView)
    assert view.page_item.pixmap().width() > view.page_item.pixmap().height()
    page = dlg.findChild(QSpinBox)
    QTest.keyClick(page, Qt.Key_PageDown)
    assert page.value() == 2
    assert view.page_item.pixmap().width() < view.page_item.pixmap().height()
    QTest.keyClick(page, Qt.Key_Escape)
    app.processEvents()

    picked = str(tmp_path / "Выделенные.pdf")
    whole = str(tmp_path / "Все страницы.pdf")
    merged = str(tmp_path / "Объединённые.pdf")
    paths = iter([picked, whole, merged])
    monkeypatch.setattr(tab_pages, "ask_save", lambda *args: next(paths))
    QTest.mouseClick(pg.btn_extract, Qt.LeftButton)
    assert pg.is_dirty()
    with pymupdf.open(picked) as result:
        assert [p.get_text().strip()[-1] for p in result] == ["4", "2"]
        assert [p.rotation for p in result] == [90, 90]
    win.activateWindow()
    assert QTest.qWaitForWindowActive(win)
    pg.grid.setFocus()
    QTest.keyClick(pg.grid, Qt.Key_S, Qt.ControlModifier)
    with pymupdf.open(whole) as result:
        assert [p.get_text().strip()[-1] for p in result] == ["4", "1", "2", "3"]
    pg.add_files([pdf, picked])
    pg.merge_list.setFocus()
    QTest.keyClick(pg.merge_list, Qt.Key_S, Qt.ControlModifier)
    with pymupdf.open(merged) as result:
        assert len(result) == 6
    with pymupdf.open(pdf) as original:
        assert all(page.rotation == 0 for page in original)
    pg.modes.setCurrentIndex(0)
    pg.grid.setFocus()
    QTest.keyClick(pg.grid, Qt.Key_Backspace)
    assert pg.grid.count() == 2 and not pg.btn_extract.isEnabled()
    win.undo()
    assert pg.state()[0] == [3, 0, 1, 2]


@pytest.mark.parametrize("width", [1024, 1179, 1180, 1280, 1366])
def test_no_horizontal_scroll(win, width, tmp_path):
    acts = _pdf(str(tmp_path / "Акты с очень длинным названием файла для проверки ширины окна кв 1-2.pdf"))
    rec = _pdf(str(tmp_path / "Расписки с очень длинным названием файла для проверки ширины окна кв 1-2.pdf"))
    win.sign.open_acts(acts)
    win.sign.open_receipts(rec)
    win.fragment.open_src(acts)
    win.fragment.open_dst(rec)
    win.pages.open(acts)
    win.pages.add_files([acts, rec])
    win.sort.open(acts)
    win.image.open_pdf(acts)
    win.batch.add_pair(acts, rec)
    win.sign.advanced.set_expanded(True)
    win.sort.advanced.set_expanded(True)
    win.resize(width, 720)
    app.processEvents()
    from app.main import COMPACT_BELOW
    assert win._compact == (width < COMPACT_BELOW)
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
        assert win.tabs.widget(i).horizontalScrollBar().maximum() == 0, type(win.tabs.currentWidget()).__name__
    for button in (win.fragment.btn_retouch, win.fragment.btn_place):
        assert button.width() >= button.minimumSizeHint().width()


def test_compact_sidebar_keeps_navigation(win):
    win.resize(1000, 700)
    app.processEvents()
    assert win._compact and win.nav["pages"].text() == ""
    win.nav["pages"].click()
    assert win.current() is win.pages
    win.resize(1300, 700)
    app.processEvents()
    assert not win._compact and win.nav["pages"].text() == "Страницы"


def test_open_files_from_system(win, tmp_path):
    pdf = _pdf(str(tmp_path / "Папка с кириллицей" / "акты.pdf") if os.makedirs(tmp_path / "Папка с кириллицей")
               is None else "")
    win.show_screen(win.helppage)
    win.open_files([pdf])
    assert win.current() is win.pages and win.pages.doc is not None
    png = str(tmp_path / "печать.png")
    from PIL import Image
    Image.new("RGB", (40, 40), (20, 40, 200)).save(png)
    win.open_files([png])
    assert win.current() is win.image
    win.show_screen(win.sign)
    win.open_files([png, pdf])
    assert win.current() is win.image and win.image.path == pdf and win.image.img_path == png


def test_mac_open_menu(win, monkeypatch, tmp_path):
    from app.main import QFileDialog
    from PySide6.QtGui import QAction, QKeySequence
    if sys.platform != "darwin":
        win._build_menubar()
    action, = [a for a in win.findChildren(QAction) if a.shortcut() == QKeySequence(QKeySequence.Open)]
    pdf = _pdf(str(tmp_path / "образец.pdf"))
    win.show_screen(win.helppage)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([pdf], ""))
    action.trigger()
    assert win.current() is win.pages and win.pages.path == pdf


def test_finder_events_delivered_in_batches():
    # Отдельный процесс нужен для создания именно PdfApp, в pytest уже есть QApplication.
    subprocess.run([sys.executable, "-c", """
import os
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFileOpenEvent
from app.main import PdfApp

app = PdfApp([])
batches = []
class Window:
    def open_files(self, paths):
        batches.append(paths)

def send(path):
    app.sendEvent(app, QFileOpenEvent(QUrl.fromLocalFile(path)))

paths = [os.path.abspath(name) for name in ("Акты.pdf", "Расписки.pdf", "Печать.png")]
send(paths[0])
app.set_window(Window())
send(paths[1])
app.processEvents()
assert batches == [paths[:2]], batches
send(paths[0])
send(paths[2])
app.processEvents()
assert batches == [paths[:2], [paths[0], paths[2]]], batches
"""], cwd=os.path.dirname(os.path.dirname(__file__)), check=True, timeout=30)


def test_stepbar_compacts(win):
    sb = win.sign.header.steps
    sb.resize(300, 40)
    sb._fit()
    texts = sb._texts()
    assert sum(t.isVisibleTo(sb) for t in texts) == 1       # только активный шаг с текстом
    sb.resize(2000, 40)
    sb._fit()
    assert all(t.isVisibleTo(sb) for t in texts)

def test_separate_page_workspaces_and_text_navigation(win, tmp_path):
    pages = win.pages
    win.show_screen(pages)
    win.resize(900, 560)
    app.processEvents()
    assert pages.start_one.button.isVisible() and not pages.grid.isVisible()
    assert win.tabs.widget(win.tabs.currentIndex()).verticalScrollBar().maximum() == 0
    path = _pdf(str(tmp_path / "book.pdf"))
    pages.open(path)
    pages.grid.setCurrentRow(0)
    pages.rotate(90)
    pages.add_files([path, path])
    assert pages.merge_list.isVisible() and not pages.grid.isVisible()
    pages.do_merge(str(tmp_path / "merged.pdf"))
    assert pages.is_dirty()  # Поворот ещё не сохранён.
    pages.modes.setCurrentIndex(0)
    assert pages.grid.count() == 2 and pages.grid.currentRow() == 0
    pages.save(str(tmp_path / "rotated.pdf"))
    assert not pages.is_dirty()
    QTest.mouseClick(win.nav["text"], Qt.LeftButton)
    assert win.current() is win.text and win.text.doc[0].rotation == 90


def test_cancel_replacing_edited_pdf(win, tmp_path, monkeypatch):
    from app.ui import tab_pages
    pg = win.pages
    original = _pdf(str(tmp_path / "first.pdf"))
    another = _pdf(str(tmp_path / "second.pdf"))
    pg.open(original)
    pg.grid.setCurrentRow(0)
    pg.rotate(90)
    monkeypatch.setattr(QMessageBox, "exec", lambda _: QMessageBox.Save)
    monkeypatch.setattr(tab_pages, "ask_save", lambda *_: "")
    pg.open(another)
    assert pg.path == original and pg.state()[1][0] == 90
    monkeypatch.setattr(QMessageBox, "exec", lambda _: QMessageBox.Discard)
    pg.open(another)
    assert pg.path == another and not pg.is_dirty()
