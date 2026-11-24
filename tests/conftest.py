"""Общие данные для сквозных проверок: выдуманные сканы (qa/fake_scans.py) создаются один раз за прогон.
PDFTOOL_E2E_DATA=<папка> — хранить их между прогонами (быстрее локально)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qa"))


@pytest.fixture(scope="session", autouse=True)
def qt_settings_dir(tmp_path_factory):
    if sys.platform.startswith("linux"):
        from PySide6.QtCore import QSettings
        QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path_factory.mktemp("qt_settings")))


@pytest.fixture(scope="session")
def fake(tmp_path_factory):
    from fake_scans import Scenario, make_pair
    base = os.environ.get("PDFTOOL_E2E_DATA") or str(tmp_path_factory.mktemp("fake_scans"))
    cache = {}

    def get(name, sc: "Scenario"):
        if name not in cache:
            import json
            folder = os.path.join(base, name)
            mj = os.path.join(folder, "meta.json")
            key = repr(sc)
            meta = None
            if os.path.exists(mj):
                old = json.load(open(mj, encoding="utf-8"))
                if old.get("key") == key and all(os.path.exists(old[k]) for k in ("acts", "receipts")):
                    meta = old
            if meta is None:
                meta = make_pair(folder, sc)
                meta["key"] = key
                json.dump(meta, open(mj, "w", encoding="utf-8"), ensure_ascii=False)
            meta["scenario"] = sc
            cache[name] = meta
        return cache[name]
    get.Scenario = Scenario
    get.base = base
    return get


@pytest.fixture
def win(monkeypatch, tmp_path):
    """Главное окно программы как у пользователя (тема, уведомления), настройки и шаблоны — во временной папке.
    win.toasts_seen — все показанные уведомления (вид, текст)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import e2e_lib  # noqa: F401 — QApplication и заглушки окон-сообщений
    monkeypatch.setenv("PDFTOOL_TEMPLATES", str(tmp_path / "tpl"))
    monkeypatch.setenv("PDFTOOL_SETTINGS", "PdfToolTestE2E")
    from app.main import MainWindow, setup_app
    if not getattr(e2e_lib.app, "_pdftool_setup", False):
        setup_app(e2e_lib.app)
        e2e_lib.app._pdftool_setup = True
    from app.ui.infra import settings
    settings().clear()
    w = MainWindow()
    w.resize(1400, 900)
    w.toasts_seen = []
    orig = w.toast

    def toast(text, kind="info", **k):
        w.toasts_seen.append((kind, text))
        return orig(text, kind, **k)
    w.toast = toast
    w.show()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()
    # окно уничтожить по-настоящему (deleteLater вне цикла событий не срабатывает): десятки «закрытых»
    # окон замедляют смену темы и processEvents в следующих тестах
    import shiboken6
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    QThreadPool.globalInstance().waitForDone(30000)
    QApplication.processEvents()
    for t in list(QApplication.topLevelWidgets()):
        if t is not w and shiboken6.isValid(t) and (t.parent() is w or t.window() is w):
            shiboken6.delete(t)
    if shiboken6.isValid(w):
        shiboken6.delete(w)


@pytest.fixture(autouse=True)
def _destroy_closed_windows():
    """После каждого теста уничтожаем закрытые главные окна: иначе к концу прогона их десятки, и каждая
    смена темы / processEvents обходит их все (прогон шёл в 2–3 раза дольше)."""
    yield
    if "PySide6.QtWidgets" not in sys.modules:
        return
    import shiboken6
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        return
    QThreadPool.globalInstance().waitForDone(30000)
    for t in list(QApplication.topLevelWidgets()):
        if shiboken6.isValid(t) and type(t).__name__ == "MainWindow" and not t.isVisible():
            shiboken6.delete(t)
