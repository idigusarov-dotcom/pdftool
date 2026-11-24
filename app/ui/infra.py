"""Инфраструктура интерфейса: отмена (Ctrl+Z), настройки, фоновые задачи."""
from __future__ import annotations

import copy
import os
import traceback
import weakref

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..core.logsetup import log
from ..core.pdfutil import NRect


def settings() -> QSettings:
    # PDFTOOL_SETTINGS — отдельное хранилище для тестов/скриншотов
    return QSettings("AkvaTehStroy", os.environ.get("PDFTOOL_SETTINGS", "PdfTool"))


def save_rect(key: str, r: NRect | None):
    if r is not None:
        settings().setValue(key, [r.x0, r.y0, r.x1, r.y1])


def load_rect(key: str, default: NRect | None) -> NRect | None:
    v = settings().value(key)
    try:
        return NRect(*[float(x) for x in v]) if v else default
    except (TypeError, ValueError):
        return default


def last_dir() -> str:
    return str(settings().value("last_dir", os.path.expanduser("~")))


def remember_dir(path: str):
    if path:
        settings().setValue("last_dir", path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path)))


class _Snap(QUndoCommand):
    def __init__(self, owner, before, after, text):
        super().__init__(text)
        self.owner = weakref.proxy(owner)
        self.before, self.after = before, after
        self._first = True

    def redo(self):
        if self._first:  # состояние уже применено пользователем
            self._first = False
            return
        self.owner.apply_state(copy.deepcopy(self.after))

    def undo(self):
        self.owner.apply_state(copy.deepcopy(self.before))


class UndoMixin:
    """Вкладка реализует get_state()/set_state(); после правки вызывает self.commit("что сделано")."""

    def init_undo(self):
        self.undo_stack = QUndoStack(self)
        self._last_state = None
        self._restoring = False

    def reset_undo(self):
        self.undo_stack.clear()
        self._last_state = copy.deepcopy(self.get_state())

    def commit(self, text: str = "изменение"):
        if self._restoring or self._last_state is None:
            return
        now = self.get_state()
        if now == self._last_state:
            return
        self.undo_stack.push(_Snap(self, self._last_state, copy.deepcopy(now), text))
        self._last_state = copy.deepcopy(now)

    def apply_state(self, state):
        self._restoring = True
        try:
            self.set_state(state)
        finally:
            self._restoring = False
        self._last_state = copy.deepcopy(state)

    def mark_saved(self):
        self.undo_stack.setClean()

    def is_dirty(self) -> bool:
        return not self.undo_stack.isClean()


class _Sig(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Фоновая задача: fn(progress) -> результат. Сигналы приходят в поток интерфейса."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.sig = _Sig()
        self.cancelled = False

    def run(self):
        try:
            res = self.fn(lambda i, n: (self.sig.progress.emit(i, n), not self.cancelled)[1])
            self.sig.done.emit(res)
        except Exception as e:  # noqa: BLE001
            log.error("Ошибка фоновой задачи:\n%s", traceback.format_exc())
            self.sig.failed.emit(str(e))


def run_task(fn, on_done, on_fail=None, on_progress=None) -> Task:
    t = Task(fn)
    t.sig.done.connect(on_done)
    if on_fail:
        t.sig.failed.connect(on_fail)
    if on_progress:
        t.sig.progress.connect(on_progress)
    QThreadPool.globalInstance().start(t)
    return t
