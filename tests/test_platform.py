"""Версия для Mac и новое оформление: подписи ⌘, тема «как в системе», узкое окно, файлы от системы."""
import os
import sys
import tempfile

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTestPlatform"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QUrl  # noqa: E402
from PySide6.QtGui import QFileOpenEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

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
    d = logsetup.log_dir()
    assert d == os.path.join(str(tmp_path), "Library", "Application Support", "PdfTool") and os.path.isdir(d)


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
    d = win.preferences.__func__  # окно «Настройки» не модальное в тесте
    os.environ["PDFTOOL_NO_MODAL"] = "1"
    try:
        dlg = d(win)
        dlg.radios["dark"].setChecked(True)
        assert style.is_dark() and main.theme_mode() == "dark"
        dlg.close()
    finally:
        os.environ.pop("PDFTOOL_NO_MODAL")
        win.set_theme_mode("light")


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
    from app.main import PdfApp
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
    # событие FileOpen (так macOS передаёт файл, брошенный на значок в Dock): до окна — в очередь
    ev = QFileOpenEvent(QUrl.fromLocalFile(pdf))
    assert ev.type() == QEvent.FileOpen
    got = []
    fake = type("A", (), {})()
    fake.window, fake.pending = None, []
    PdfApp.event(fake, ev)
    assert fake.pending == [pdf]
    fake.window = type("W", (), {"open_files": lambda self, ps: got.extend(ps)})()
    PdfApp.event(fake, QFileOpenEvent(QUrl.fromLocalFile(pdf)))
    assert got == [pdf]


def test_stepbar_compacts(win):
    sb = win.sign.header.steps
    sb.resize(300, 40)
    sb._fit()
    texts = sb._texts()
    assert sum(t.isVisibleTo(sb) for t in texts) == 1       # только активный шаг с текстом
    sb.resize(2000, 40)
    sb._fit()
    assert all(t.isVisibleTo(sb) for t in texts)
