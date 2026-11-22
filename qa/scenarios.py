"""Сценарная проверка окна программы (тот же код, что в PdfTool.exe 1.4.0), запускается на Windows-раннере.

    python qa/scenarios.py <папка данных> <папка отчёта> [--only имя,имя] [--shots-only]

Каждый сценарий: время, пик памяти, максимальная «заморозка» окна (сколько окно не обрабатывало события),
уведомления/окна сообщений, необработанные исключения. Итог — report.json (дописывается после каждого
сценария, чтобы при падении процесса было видно, на чём упало) и снимки экрана.
"""
from __future__ import annotations

import faulthandler
import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qa"))
DATA, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
ONLY = set()
if "--only" in sys.argv:
    ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(","))
SHOTS_ONLY = "--shots-only" in sys.argv
TAG = os.environ.get("QA_TAG", "")
os.makedirs(OUT, exist_ok=True)
SHOTS = os.path.join(OUT, "shots")
os.makedirs(SHOTS, exist_ok=True)
WORK = os.path.join(OUT, "work")
os.makedirs(WORK, exist_ok=True)
_fh = open(os.path.join(OUT, "faulthandler.txt"), "w")
faulthandler.enable(_fh)
os.environ["PDFTOOL_SETTINGS"] = "PdfToolQA" + TAG
os.environ["PDFTOOL_TEMPLATES"] = os.path.join(WORK, "шаблоны")

import psutil  # noqa: E402
import pymupdf  # noqa: E402
from PySide6.QtCore import QThreadPool, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import make_data  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from app.main import MainWindow, setup_app  # noqa: E402
from app.ui.infra import settings  # noqa: E402
from app.core.logsetup import setup_logging, log_dir  # noqa: E402

setup_logging()

# ---------- перехват окон сообщений и необработанных ошибок ----------
EVENTS: list = []
ANSWER = {"question": QMessageBox.Yes}


def _mb(kind):
    def f(*a, **k):
        EVENTS.append((kind, str(a[2]) if len(a) > 2 else str(a)))
        return ANSWER["question"] if kind == "question" else QMessageBox.Ok
    return staticmethod(f)


for _k in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _k, _mb(_k))
QMessageBox.about = staticmethod(lambda *a, **k: EVENTS.append(("about", "")))
UNHANDLED: list = []
from PySide6.QtWidgets import QFileDialog  # noqa: E402
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (EVENTS.append(("filedialog", "save")), ("", ""))[1])
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (EVENTS.append(("filedialog", "open")), ("", ""))[1])
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: (EVENTS.append(("filedialog", "open")), ([], ""))[1])
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: (EVENTS.append(("filedialog", "dir")), "")[1])


def _hook(et, ev, tb):
    UNHANDLED.append("".join(traceback.format_exception(et, ev, tb)))


sys.excepthook = _hook
threading.excepthook = lambda a: UNHANDLED.append("thread: " + "".join(
    traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))

# ---------- память и «заморозка» ----------
PROC = psutil.Process()
PEAK = {"rss": 0}


def _sampler():
    while True:
        try:
            PEAK["rss"] = max(PEAK["rss"], PROC.memory_info().rss)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.05)


threading.Thread(target=_sampler, daemon=True).start()
BEAT = {"last": time.monotonic(), "max": 0.0}


def _beat():
    now = time.monotonic()
    BEAT["max"] = max(BEAT["max"], now - BEAT["last"])
    BEAT["last"] = now


_hb = QTimer()
_hb.setInterval(50)
_hb.timeout.connect(_beat)
_hb.start()


def pump(sec=0.2):
    t = time.monotonic()
    while time.monotonic() - t < sec:
        app.processEvents()
        time.sleep(0.01)


def wait_until(cond, timeout=120.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def wait_idle(timeout=300.0):
    return wait_until(lambda: QThreadPool.globalInstance().activeThreadCount() == 0, timeout)


# ---------- окно ----------
W = {"w": None}
TOASTS: list = []


def new_window(clear=True, size=(1366, 768)):
    old = W["w"]
    if old is not None:
        for t in old.all_tabs():
            t.undo_stack.setClean()
        old.close()
        old.deleteLater()
        pump(0.2)
    if clear:
        settings().clear()
        for k in ("sign", "batch", "fragment", "sort", "image", "pages", "compress"):
            settings().setValue(f"tour/{k}", True)
    setup_app(app)
    w = MainWindow()
    orig = w.toast

    def toast(text, kind="info", **k):
        TOASTS.append((kind, text))
        return orig(text, kind, **k)
    w.toast = toast
    w.resize(*size)
    w.show()
    pump(0.3)
    W["w"] = w
    return w


def shot(name, w=None):
    w = w or W["w"]
    for mode in ("light", "dark"):
        W["w"].set_theme_mode(mode)
        pump(0.3)
        w.grab().save(os.path.join(SHOTS, f"{name}_{mode}.png"))
    W["w"].set_theme_mode("light")


# ---------- регистр сценариев ----------
SCEN: list = []


class Fail(Exception):
    pass


def scenario(name, title):
    def deco(fn):
        SCEN.append((name, title, fn))
        return fn
    return deco


class Ctx:
    def __init__(self, name):
        self.name = name
        self.checks: list = []
        self.notes: list = []
        self.dir = os.path.join(WORK, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)

    def check(self, cond, msg):
        self.checks.append((bool(cond), msg))
        return bool(cond)

    def note(self, msg):
        self.notes.append(str(msg))

    def p(self, *names):
        return os.path.join(self.dir, *names)

    def last_toast(self, n_before):
        return TOASTS[n_before:]


REPORT = {"tag": TAG, "python": sys.version, "platform": sys.platform, "scenarios": []}
try:
    import PySide6
    REPORT["qt"] = PySide6.__version__
    REPORT["pymupdf"] = pymupdf.VersionBind
except Exception:  # noqa: BLE001
    pass


def save_report():
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=1)


def D(key):
    return DATA_MAP[key]


DATA_MAP: dict = {}


def tmp_leftovers(folder):
    return [f for f in os.listdir(folder) if f.endswith(".tmp")] if os.path.isdir(folder) else []


# ======================================================================================================
@scenario("start_screens", "Первый запуск без настроек, все экраны, обе темы, узкое окно и минимальный размер")
def s_start(c: Ctx):
    t0 = time.monotonic()
    w = new_window()
    c.note(f"окно создано за {time.monotonic() - t0:.2f} с")
    from app.main import SCREENS
    for i, (key, label, *_r) in enumerate(SCREENS):
        w.tabs.setCurrentIndex(i)
        pump(0.3)
        shot(f"screen_{i + 1}_{key}")
    for size in ((1024, 700), (900, 600)):
        w.resize(*size)
        pump(0.4)
        for i, (key, *_r) in enumerate(SCREENS):
            w.tabs.setCurrentIndex(i)
            pump(0.15)
            sa = w.tabs.widget(i)
            hb = sa.horizontalScrollBar().maximum()
            c.check(hb == 0, f"{size[0]}x{size[1]} «{key}»: прокрутки вбок нет (max={hb})")
        w.grab().save(os.path.join(SHOTS, f"narrow_{size[0]}x{size[1]}.png"))
    ms = w.minimumSizeHint()
    w.resize(1, 1)
    pump(0.4)
    c.note(f"минимальный размер окна: {w.width()}x{w.height()} (minimumSizeHint {ms.width()}x{ms.height()})")
    w.grab().save(os.path.join(SHOTS, "minimum_size.png"))
    w.resize(1366, 768)
    for mode in ("system", "dark", "light"):
        w.set_theme_mode(mode)
        pump(0.2)
    c.check(True, "переключение тем без ошибок")


@scenario("corrupt_settings", "Повреждённые настройки и шаблоны")
def s_corrupt(c: Ctx):
    s = settings()
    s.clear()
    garbage = {"ui/theme": 12345, "ui/dark": "может быть", "sort/zone1": "абв", "sort/zone2": [1, 2],
               "last_dir": "Z:\\нет такой папки\\x", "compress/level": "ультра", "tour/sign": "наверное",
               "sign/src_rect": ["a", "b", "c", "d"], "sign/dst_rect": 7, "fragment/mode": None,
               "geometry": b"\x00\xff garbage", "window/geometry": "мусор", "image/white": "да-нет"}
    for k, v in garbage.items():
        s.setValue(k, v)
    os.makedirs(os.environ["PDFTOOL_TEMPLATES"], exist_ok=True)
    tdir = os.environ["PDFTOOL_TEMPLATES"]
    with open(os.path.join(tdir, "битый.json"), "w", encoding="utf-8") as f:
        f.write("{ не json")
    with open(os.path.join(tdir, "пустой.json"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tdir, "чужой.json"), "w", encoding="utf-8") as f:
        f.write('{"name": 5, "zones_rec": "x"}')
    with open(os.path.join(tdir, "список.json"), "w", encoding="utf-8") as f:
        f.write('[1, 2, 3]')
    w = new_window(clear=False)
    from app.main import SCREENS
    for i in range(len(SCREENS)):
        w.tabs.setCurrentIndex(i)
        pump(0.1)
    c.check(w.isVisible(), "окно открылось с повреждёнными настройками")
    w.compress.open(D("scan20"))
    c.check(w.compress.level() in ("strong", "medium", "light"), f"степень сжатия по умолчанию: {w.compress.level()}")
    w.sort.open(D("sort_scan"))
    w.batch.refresh_templates()
    for f in glob.glob(os.path.join(tdir, "*.json")):
        os.remove(f)


@scenario("pages_merge", "Объединение: порядок перетаскиванием, пароль, битый, пустой, не-PDF")
def s_merge(c: Ctx):
    w = new_window()
    p = w.pages
    w.show_screen(p)
    n0 = len(TOASTS)
    p.add_files([D("a3"), D("password"), D("b3"), D("broken"), D("empty"), D("notpdf"), D("c3")])
    c.check(p.merge_list.count() == 3, f"в список попали только нормальные файлы ({p.merge_list.count()})")
    errs = [t for t in TOASTS[n0:] if t[0] == "error"]
    c.check(len(errs) == 4, f"по каждому плохому файлу понятное сообщение ({len(errs)}): " +
            " | ".join(t[1][:70] for t in errs))
    # «перетаскивание»: последний файл наверх
    m = p.merge_list.model()
    m.moveRow(m.index(0, 0).parent(), 2, m.index(0, 0).parent(), 0)
    pump(0.1)
    order = [os.path.basename(p.merge_list.item(i).data(256)) for i in range(p.merge_list.count())]
    c.note("порядок после перетаскивания: " + ", ".join(order))
    out = c.p("объединённый.pdf")
    p.do_merge(out)
    with pymupdf.open(out) as d:
        first = [d[i].get_text().strip().split("\n")[0] for i in (0, 3, 6)]
    c.check(len(first) == 3 and first[0].startswith("В-1") and first[1].startswith("А-1"),
            f"порядок страниц в результате как в списке: {first}")
    shot("pages_merge")
    # один файл — объединять нечего
    p.merge_list.clear()
    p.add_files([D("a3")])
    n0 = len(TOASTS)
    p.do_merge(c.p("x.pdf"))
    c.check(TOASTS[n0:] and TOASTS[n0][0] == "error", "один файл: сообщение «добавьте хотя бы два»")
    # огромный: 320 страниц + 20
    p.merge_list.clear()
    p.add_files([D("big"), D("scan20")])
    BEAT["max"] = 0
    t = time.monotonic()
    p.do_merge(c.p("большой объединённый.pdf"))
    c.note(f"объединение 340 стр. (~75 МБ): {time.monotonic() - t:.1f} с, окно не отвечало {BEAT['max']:.1f} с")


@scenario("pages_edit", "Страницы: поворот, удаление, порядок, диапазоны (крайние случаи), каждая отдельно")
def s_pages(c: Ctx):
    w = new_window()
    p = w.pages
    w.show_screen(p)
    p.open(D("pages10"))
    wait_idle(30)
    pump(0.3)
    g = p.grid
    c.check(g.count() == 10, f"10 миниатюр ({g.count()})")
    g.item(0).setSelected(True)
    g.item(1).setSelected(True)
    p.rotate(90)
    g.clearSelection()
    g.item(9).setSelected(True)
    p.delete()
    g.clearSelection()
    it = g.takeItem(2)          # «перетащить» 3-ю страницу в конец
    g.addItem(it)
    p.commit("порядок")
    out = c.p("изменён.pdf")
    p.save(out)
    with pymupdf.open(out) as d:
        c.check(len(d) == 9, f"страниц 9 ({len(d)})")
        c.check(d[0].rotation == 90 and d[1].rotation == 90 and d[2].rotation == 0, "поворот на 1-2 страницах")
        c.check(d[8].get_text().strip().startswith("Страница 3"), "3-я страница в конце")
    p.undo_stack.undo()
    p.undo_stack.undo()
    p.undo_stack.redo()
    shot("pages_edit")
    # диапазоны
    cases = {"": False, "1-3, 4-6": True, "1–3; 5": True, "1—3, 7−9": True, "0": False, "11": False,
             "3-1": None, "1-3,,5": None, "abc": False, "1-999": False, " 2 ": True, "-": False, "1-": None,
             "5,5,5": None, "1-10": True, "１-３": None}
    for txt, expect in cases.items():
        p.ed_ranges.setText(txt)
        d2 = c.p("диапазоны", f"r{abs(hash(txt)) % 10000}")
        os.makedirs(d2, exist_ok=True)
        n0 = len(TOASTS)
        try:
            p.split_ranges(d2)
        except Exception as e:  # noqa: BLE001
            c.check(False, f"диапазон «{txt}»: исключение {e!r}")
            continue
        made = len(os.listdir(d2))
        kinds = [k for k, _ in TOASTS[n0:]]
        msg = TOASTS[n0][1][:80] if TOASTS[n0:] else "—"
        if expect is True:
            c.check(made > 0 and "error" not in kinds, f"диапазон «{txt}»: создано {made} файлов")
        elif expect is False:
            c.check(made == 0 and "error" in kinds, f"диапазон «{txt}»: отказ с понятным текстом: {msg}")
        else:
            c.note(f"диапазон «{txt}»: файлов {made}, сообщение: {msg}")
    # каждая страница отдельно, 320 страниц
    p.open(D("big"))
    wait_idle(60)
    d3 = c.p("каждая")
    os.makedirs(d3)
    BEAT["max"] = 0
    t = time.monotonic()
    p.split_every(d3)
    c.check(len(os.listdir(d3)) == 320, f"320 файлов ({len(os.listdir(d3))})")
    c.note(f"«каждая отдельно» на 320 стр.: {time.monotonic() - t:.1f} с, окно не отвечало {BEAT['max']:.1f} с")
    # поворот всех и сохранение большого
    p.grid.selectAll()
    p.rotate(180)
    BEAT["max"] = 0
    t = time.monotonic()
    p.save(c.p("большой повёрнут.pdf"))
    c.note(f"сохранение 320 стр. с поворотом: {time.monotonic() - t:.1f} с, окно не отвечало {BEAT['max']:.1f} с")
    # удалить всё
    p.grid.selectAll()
    p.delete()
    n0 = len(TOASTS)
    p.save(c.p("пусто.pdf"))
    c.check(TOASTS[n0:] and TOASTS[n0][0] == "error", "удалены все страницы: понятный отказ")


@scenario("image_insert", "Картинка: PNG с прозрачностью, фото, все страницы большого файла, окно хода и «Отмена»")
def s_image(c: Ctx):
    w = new_window()
    im = w.image
    w.show_screen(im)
    im.open_pdf(D("pages10"))
    im.open_image(D("stamp"))
    im.r_all.setChecked(True)
    out = c.p("с печатью.pdf")
    im.save(out)
    with pymupdf.open(out) as d:
        c.check(all(len(pg.get_images()) == 1 for pg in d), "печать на всех 10 страницах")
    shot("image")
    n0 = len(TOASTS)
    im.open_image(D("photo"))
    im.open_image(D("notpdf"))
    c.check(any(k == "error" for k, _ in TOASTS[n0:]), "не-картинка: понятное сообщение")
    im.open_pdf(D("big"))
    im.open_image(D("stamp"))
    im.r_all.setChecked(True)
    BEAT["max"] = 0
    t = time.monotonic()
    big_out = c.p("большой с печатью.pdf")
    im.save(big_out)
    c.note(f"печать на 320 стр.: {time.monotonic() - t:.1f} с, окно не отвечало {BEAT['max']:.1f} с")
    c.check(os.path.exists(big_out), "результат на 320 стр. сохранён")
    # отмена из окна хода
    from PySide6.QtWidgets import QProgressDialog

    def cancel_soon():
        for wd in QApplication.topLevelWidgets() + w.findChildren(QProgressDialog):
            if isinstance(wd, QProgressDialog) and wd.isVisible():
                wd.grab().save(os.path.join(SHOTS, "progress_image.png"))
                wd.cancel()
                return
        QTimer.singleShot(200, cancel_soon)
    QTimer.singleShot(700, cancel_soon)
    out2 = c.p("отменённый.pdf")
    n0 = len(TOASTS)
    im.save(out2)
    if any("Отменено" in t for _, t in TOASTS[n0:]):
        c.check(not os.path.exists(out2), "после «Отмены» файл не создан")
    else:
        c.note("вставка закончилась раньше, чем появилось окно хода — «Отмену» здесь проверить нельзя")
    c.check(not tmp_leftovers(c.dir), f"временных файлов не осталось {tmp_leftovers(c.dir)}")


@scenario("sign", "Подписи: распознавание, сохранение, ошибки записи")
def s_sign(c: Ctx):
    w = new_window()
    s = w.sign
    w.show_screen(s)
    s.open_acts(D("acts"))
    s.open_receipts(D("receipts"))
    wait_idle(30)
    s.recognize()
    wait_idle(120)
    pump(0.5)
    c.note(f"сопоставление после распознавания: {s.job.mapping if s.job else None}")
    shot("sign")
    out = c.p("расписки с подписями.pdf")
    s.save(out)
    c.check(os.path.exists(out), "сохранено")
    n0 = len(TOASTS)
    s.save(D("acts"))
    c.check(TOASTS[n0:] and TOASTS[n0][0] == "error" and "поверх" in TOASTS[n0][1],
            "запись поверх исходного — отказ")
    n0 = len(TOASTS)
    s.save(D("receipts").upper() if sys.platform == "win32" else D("receipts"))
    c.check(TOASTS[n0:] and TOASTS[n0][0] == "error", "поверх исходного другим регистром букв — отказ")


@scenario("batch", "Пакет: 40 пар, плохая пара, 0 подписей, «Отмена», удаление пары во время работы")
def s_batch(c: Ctx):
    w = new_window()
    b = w.batch
    w.show_screen(b)
    for k in range(40):
        b.add_pair(D("acts"), D("receipts"), None, c.p(f"пара {k + 1:02d}.pdf"))
    b.add_pair(D("acts_nosign"), D("receipts"), None, c.p("пара без подписей.pdf"))
    t = time.monotonic()
    b.run_all()
    ok = wait_until(lambda: bool(b.last_results), 300)
    c.check(ok, "пакет завершился")
    pump(0.3)
    st = [r["status"] for r in b.last_results]
    c.note(f"41 пара за {time.monotonic() - t:.1f} с, статусы: { {x: st.count(x) for x in set(st)} }")
    c.check(b.status[-1].startswith("проверьте"), f"пара без подписей: «{b.status[-1]}»")
    c.check(getattr(b, "_summary_warn", None) is not None, "в итоговом окне жёлтое предупреждение")
    if getattr(b, "_summary", None):
        w.grab().save(os.path.join(SHOTS, "batch_after.png"))
        b._summary.grab().save(os.path.join(SHOTS, "batch_summary.png"))
        b._summary.close()
    # отмена посередине
    b.last_results = []
    b.run_all()
    QTimer.singleShot(400, b.cancel)
    wait_until(lambda: bool(b.last_results), 300)
    st = [r["status"] for r in b.last_results]
    c.check("cancelled" in st or "skipped" in st, f"«Отмена» остановила пакет ({st.count('ok')} готово)")
    if getattr(b, "_summary", None):
        b._summary.close()
    # двойной щелчок «Обработать все»
    b.last_results = []
    b.btn_run.click()
    b.btn_run.click()
    c.check(not b.btn_run.isEnabled(), "кнопка «Обработать все» заблокирована во время работы")
    wait_until(lambda: bool(b.last_results), 300)
    wait_idle(60)
    if getattr(b, "_summary", None):
        b._summary.close()
    # удаление пар во время работы
    b.last_results = []
    nerr = len(UNHANDLED)
    b.run_all()
    pump(0.2)
    for _ in range(10):
        b.table.setCurrentCell(0, 0)
        b.remove_pair()
    wait_until(lambda: bool(b.last_results), 300)
    wait_idle(60)
    pump(0.5)
    c.check(len(UNHANDLED) == nerr, "удаление пар во время работы — без необработанной ошибки"
            + ("" if len(UNHANDLED) == nerr else ": " + UNHANDLED[-1].strip().splitlines()[-1]))
    if getattr(b, "_summary", None):
        b._summary.close()
    # плохая пара
    b.items.clear()
    b.status.clear()
    b.fill()
    b.add_pair(D("acts"), D("broken"), None, c.p("плохая.pdf"))
    n0 = len(TOASTS)
    b.run_all()
    pump(0.3)
    c.check(TOASTS[n0:] and TOASTS[n0][0] == "error", "битый файл в паре: отказ до запуска с объяснением")


@scenario("sort", "Сортировка: номера из текста, распознавание цифр, «Отмена», сохранение и CSV")
def s_sort(c: Ctx):
    w = new_window()
    so = w.sort
    w.show_screen(so)
    so.open(D("sort_scan"))
    wait_idle(30)
    so.recognize()
    wait_idle(120)
    pump(0.5)
    vals = so.values_by_page()
    exp = DATA_MAP["sort_nums"]
    good = sum(a == b for a, b in zip(vals, exp))
    c.note(f"распознано {good} из {len(exp)} (напечатанные цифры, не рукописные): {vals}")
    shot("sort")
    out = c.p("отсортировано.pdf")
    so.save(out)
    c.check(os.path.exists(out), "отсортированный PDF сохранён")
    so.save_csv(c.p("номера.csv"))
    c.check(os.path.exists(c.p("номера.csv")), "CSV сохранён")
    # текстовый слой
    so.open(D("pages10"))
    so.guess()
    c.note(f"по тексту: {so.values_by_page()[:3]}…")
    so.open(D("acts"))
    so.guess()
    c.check(so.values_by_page() == ["1", "2", "3", "4", "5"], f"номера из текста: {so.values_by_page()}")
    # отмена распознавания на большом
    so.open(D("big"))
    wait_idle(60)
    from PySide6.QtWidgets import QProgressDialog
    so.recognize()
    pump(1.0)
    for d in so.findChildren(QProgressDialog):
        if d.isVisible():
            d.grab().save(os.path.join(SHOTS, "progress_sort.png"))
            d.cancel()
    ok = wait_idle(60)
    pump(0.5)
    c.check(ok, "распознавание остановилось после «Отмены»")


@scenario("fragment", "Фрагменты: вырезать и перенести")
def s_fragment(c: Ctx):
    from app.core.pdfutil import NRect
    w = new_window()
    f = w.fragment
    w.show_screen(f)
    f.open_src(D("acts"))
    f.open_dst(D("receipts"))
    f.view_src.set_nrect(NRect(0.1, 0.74, 0.5, 0.9))
    f.recut()
    f.place_current()
    out = c.p("фрагмент.pdf")
    f.save(out)
    c.check(os.path.exists(out), "сохранено")
    shot("fragment")


@scenario("compress", "Сжатие: три степени на 320 стр. и 20 стр., «Отмена», файл без сканов")
def s_compress(c: Ctx):
    w = new_window()
    cz = w.compress
    w.show_screen(cz)
    for key in ("scan20", "big"):
        cz.open(D(key))
        for lv in ("strong", "medium", "light"):
            cz.radios[lv].setChecked(True)
            out = c.p(f"{key}_{lv}.pdf")
            BEAT["max"] = 0
            PEAK["rss"] = 0
            t = time.monotonic()
            cz.save(out)
            dt = time.monotonic() - t
            sz = os.path.getsize(out) if os.path.exists(out) else 0
            c.note(f"{key} {lv}: {os.path.getsize(D(key)) / 1e6:.1f} → {sz / 1e6:.1f} МБ за {dt:.1f} с, "
                   f"память до {PEAK['rss'] / 1e6:.0f} МБ, окно не отвечало {BEAT['max']:.1f} с")
            if not sz:
                c.check("Меньше не стало" in cz.lbl_sizes.text(), f"{key} {lv}: не стало меньше — сказано прямо")
            else:
                c.check(sz < os.path.getsize(D(key)), f"{key} {lv}: файл меньше")
    shot("compress_done")
    from PySide6.QtWidgets import QProgressDialog

    def cancel_soon():
        for d in cz.findChildren(QProgressDialog):
            if d.isVisible():
                d.cancel()
                return
        QTimer.singleShot(200, cancel_soon)
    QTimer.singleShot(1500, cancel_soon)
    out = c.p("отменённое.pdf")
    cz.radios["medium"].setChecked(True)
    cz.save(out)
    c.check(not os.path.exists(out), "«Отмена»: файл не создан")
    c.check(not tmp_leftovers(c.dir), "временных файлов нет")
    n0 = len(TOASTS)
    cz.open(D("pages10"))
    cz.save(c.p("вектор.pdf"))
    c.check(any("нечего" in t for _, t in TOASTS[n0:]), "PDF без сканов: «сжимать нечего»")


@scenario("double_click", "Двойной щелчок по «Сохранить» во время долгого сжатия (окно хода блокирует второй)")
def s_double(c: Ctx):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from app.core import compress as czm
    from app.ui import tab_compress
    w = new_window()
    cz = w.compress
    w.show_screen(cz)
    cz.open(D("big"))
    cz.radios["strong"].setChecked(True)
    calls = []
    orig = czm.compress

    def spy(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    out = c.p("двойной.pdf")
    old_ask = tab_compress.ask_save
    czm.compress = spy
    tab_compress.ask_save = lambda *a, **k: out
    try:
        QTimer.singleShot(1200, lambda: QTest.mouseClick(cz.btn_save, Qt.LeftButton))
        QTimer.singleShot(1300, lambda: QTest.mouseClick(cz.btn_save, Qt.LeftButton))
        QTest.mouseClick(cz.btn_save, Qt.LeftButton)
        pump(1.0)
    finally:
        czm.compress = orig
        tab_compress.ask_save = old_ask
    c.check(len(calls) == 1, f"сжатие запущено один раз (запусков {len(calls)})")


@scenario("close_during", "Закрытие окна во время работы (сжатие, распознавание, пакет)")
def s_close(c: Ctx):
    nerr = len(UNHANDLED)
    # сжатие: закрыть окно, пока идёт
    w = new_window()
    w.show_screen(w.compress)
    w.compress.open(D("big"))
    QTimer.singleShot(1500, w.close)
    out = c.p("сжатие при закрытии.pdf")
    w.compress.save(out)
    pump(0.5)
    c.note(f"сжатие при закрытии окна: окно видно={w.isVisible()}, файл создан={os.path.exists(out)}")
    # распознавание: закрыть окно
    w = new_window()
    w.show_screen(w.sort)
    w.sort.open(D("big"))
    wait_idle(60)
    w.sort.recognize()
    pump(0.8)
    w.close()
    pump(0.3)
    ok = wait_idle(120)
    pump(0.5)
    c.check(ok, "фоновое распознавание завершилось/остановилось после закрытия")
    # пакет: закрыть окно
    w = new_window()
    for k in range(15):
        w.batch.add_pair(D("acts"), D("receipts"), None, c.p(f"п{k}.pdf"))
    w.batch.run_all()
    pump(0.3)
    ANSWER["question"] = QMessageBox.Yes
    w.close()
    ok = wait_idle(120)
    pump(0.5)
    c.check(ok, "фоновый пакет завершился после закрытия")
    c.check(len(UNHANDLED) == nerr, "без необработанных ошибок" +
            ("" if len(UNHANDLED) == nerr else ": " + UNHANDLED[-1].strip().splitlines()[-1]))
    c.check(not tmp_leftovers(c.dir), f"временных файлов нет {tmp_leftovers(c.dir)}")


@scenario("paths", "Пути Windows: кириллица и пробелы, длинные (>260), зарезервированные имена, UNC, только чтение, "
                   "занятый файл, переполненный диск")
def s_paths(c: Ctx):
    w = new_window()
    p = w.pages
    cz = w.compress

    def try_save(label, out, fn, expect_ok):
        n0 = len(TOASTS)
        nerr = len(UNHANDLED)
        try:
            fn(out)
        except Exception as e:  # noqa: BLE001
            c.check(False, f"{label}: исключение наружу {e!r}")
            return
        msgs = TOASTS[n0:]
        exists = bool(out) and os.path.exists(out)
        txt = msgs[-1][1].replace("\n", " ")[:160] if msgs else "—"
        if expect_ok:
            c.check(exists, f"{label}: сохранено ({txt})")
        else:
            bad = [m for m in msgs if m[0] == "error"]
            readable = bool(bad) and not any(s in bad[-1][1] for s in ("code=", "Errno", "fz_", "Traceback"))
            c.check(not exists and readable, f"{label}: понятный отказ — «{txt}»")
        c.check(len(UNHANDLED) == nerr, f"{label}: без необработанной ошибки")

    p.open(D("pages10"))
    # 1. кириллица и пробелы
    d1 = c.p("Мои документы", "Акты  и расписки (копия)")
    os.makedirs(d1)
    try_save("кириллица и пробелы", os.path.join(d1, "итог № 1 .pdf"), p.save, True)
    # 2. длинный путь
    long_dir = c.dir
    while len(long_dir) < 250:
        long_dir = os.path.join(long_dir, "очень длинное имя папки для проверки")
    try:
        os.makedirs(long_dir, exist_ok=True)
        made = True
    except OSError as e:
        made = False
        c.note(f"длинную папку создать нельзя: {e}")
    if made:
        lp = os.path.join(long_dir, "результат.pdf")
        c.note(f"длинный путь: {len(lp)} символов")
        try_save("сохранение в длинный путь (>260)", lp, p.save, True)
        if os.path.exists(lp):
            n0 = len(TOASTS)
            p.open(lp)
            c.check(p.path == lp, "открытие файла по длинному пути " + (TOASTS[n0][1][:120] if TOASTS[n0:] else ""))
            cz.open(lp)
    # 3. зарезервированные имена и недопустимые символы
    for name in ("CON.pdf", "aux.pdf", "NUL.pdf", "COM1.pdf", "файл?.pdf", "имя с точкой в конце..pdf",
                 "a" * 250 + ".pdf"):
        try_save(f"имя «{name[:30]}»", os.path.join(c.dir, name), p.save, None)
    # 4. только чтение
    ro = c.p("только чтение")
    os.makedirs(ro)
    if sys.platform == "win32":
        import subprocess
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", ro, "/deny", f"{user}:(W,AD,WD)"], capture_output=True)
    else:
        os.chmod(ro, 0o555)
    try_save("папка только для чтения (страницы)", os.path.join(ro, "x.pdf"), p.save, False)
    cz.open(D("scan20"))
    try_save("папка только для чтения (сжатие)", os.path.join(ro, "x.pdf"), cz.save, False)
    w.pages.ed_ranges.setText("1-2")
    try_save("папка только для чтения (разделение)", ro, lambda d: p.split_ranges(d), False)
    # 5. занятый файл (как открытый в Acrobat: запрет записи и удаления)
    locked = c.p("открыт в Acrobat.pdf")
    shutil.copy(D("a3"), locked)
    h = None
    if sys.platform == "win32":
        import win32file
        h = win32file.CreateFile(locked, win32file.GENERIC_READ, win32file.FILE_SHARE_READ, None,
                                 win32file.OPEN_EXISTING, 0, None)
    try_save("файл-результат занят другой программой", locked, p.save, None)
    if sys.platform == "win32":
        c.check(open(locked, "rb").read() == open(D("a3"), "rb").read(), "занятый файл не испорчен")
        # занятый исходник (открыт без общего доступа) — открыть
        n0 = len(TOASTS)
        h2 = win32file.CreateFile(D("b3"), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None)
        p.open(D("b3"))
        msg = TOASTS[n0][1][:150] if TOASTS[n0:] else "—"
        c.note(f"открыть файл, занятый без общего доступа: {msg}")
        win32file.CloseHandle(h2)
        win32file.CloseHandle(h)
    # 6. UNC (как сетевая папка / OneDrive через сеть)
    unc = os.environ.get("QA_UNC")
    if unc:
        ud = os.path.join(unc, "проверка сети")
        os.makedirs(ud, exist_ok=True)
        shutil.copy(D("a3"), os.path.join(ud, "на сервере.pdf"))
        p.open(os.path.join(ud, "на сервере.pdf"))
        c.check(p.path.endswith("на сервере.pdf"), "открытие с сетевого пути \\\\server\\share")
        try_save("сохранение на сетевой путь", os.path.join(ud, "сохранён по сети.pdf"), p.save, True)
    # 7. переполненный диск
    small = os.environ.get("QA_SMALL_DRIVE")
    if small:
        cz.open(D("big"))
        cz.radios["light"].setChecked(True)
        try_save("переполненный диск (сжатие 53 МБ на диск 8 МБ)", os.path.join(small, "сжатый.pdf"), cz.save, False)
        c.check(not tmp_leftovers(small + os.sep), f"на диске не осталось временных файлов {os.listdir(small + os.sep)}")
        p.open(D("big"))
        try_save("переполненный диск (страницы)", os.path.join(small, "страницы.pdf"), p.save, False)
    # 8. OneDrive-подобная папка (папка с «облачным» именем внутри профиля)
    od = os.path.join(os.path.expanduser("~"), "OneDrive - АкваТехСтрой", "Документы")
    os.makedirs(od, exist_ok=True)
    p.open(D("pages10"))
    try_save("папка вида OneDrive", os.path.join(od, "итог.pdf"), p.save, True)


@scenario("undo_everywhere", "Ctrl+Z/Ctrl+Y на всех экранах после действий")
def s_undo(c: Ctx):
    w = new_window()
    w.pages.open(D("pages10"))
    w.pages.grid.selectAll()
    w.pages.rotate(90)
    w.sort.open(D("acts"))
    w.sort.guess()
    w.compress.open(D("scan20"))
    w.compress.radios["strong"].setChecked(True)
    for t in w.all_tabs():
        w.show_screen(t)
        for _ in range(5):
            w.undo()
        for _ in range(5):
            w.redo()
    c.check(True, "без ошибок")


# ======================================================================================================
def main():
    global DATA_MAP
    t = time.monotonic()
    if not os.path.exists(os.path.join(DATA, "большой скан 320 страниц.pdf")):
        DATA_MAP = make_data.make_all(DATA)
    else:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            DATA_MAP = make_data.make_all(os.path.join(tempfile.mkdtemp(), "d")) if False else _known(DATA)
    REPORT["data_sec"] = round(time.monotonic() - t, 1)
    for name, title, fn in SCEN:
        if ONLY and name not in ONLY:
            continue
        if SHOTS_ONLY and name != "start_screens":
            continue
        with open(os.path.join(OUT, "current.txt"), "w", encoding="utf-8") as f:
            f.write(name)
        print(f"=== {name}: {title}", flush=True)
        c = Ctx(name)
        n_unh, n_ev, n_t = len(UNHANDLED), len(EVENTS), len(TOASTS)
        PEAK["rss"] = 0
        BEAT["max"] = 0
        BEAT["last"] = time.monotonic()
        t0 = time.monotonic()
        status, tb = "ok", ""
        try:
            fn(c)
        except Exception:  # noqa: BLE001
            status, tb = "exception", traceback.format_exc()
        pump(0.3)
        failed = [m for ok, m in c.checks if not ok]
        unh = UNHANDLED[n_unh:]
        if status == "ok" and (failed or unh):
            status = "fail"
        rec = {"name": name, "title": title, "status": status, "sec": round(time.monotonic() - t0, 1),
               "peak_rss_mb": round(PEAK["rss"] / 1e6), "checks": c.checks, "notes": c.notes,
               "traceback": tb, "unhandled": unh, "dialogs": EVENTS[n_ev:], "toasts": TOASTS[n_t:]}
        REPORT["scenarios"].append(rec)
        save_report()
        print(f"    {status} за {rec['sec']} с; проверок {len(c.checks)}, не прошло {len(failed)}; "
              f"память {rec['peak_rss_mb']} МБ", flush=True)
        for m in failed:
            print("    ✗", m, flush=True)
        for n in c.notes:
            print("    ·", n, flush=True)
        if tb:
            print(tb, flush=True)
        for u in unh:
            print("    НЕОБРАБОТАНО:", u, flush=True)
    try:
        shutil.copy(os.path.join(log_dir(), "log.txt"), os.path.join(OUT, "app_log.txt"))
    except OSError:
        pass
    with open(os.path.join(OUT, "current.txt"), "w", encoding="utf-8") as f:
        f.write("done")
    bad = [s["name"] for s in REPORT["scenarios"] if s["status"] != "ok"]
    print("ИТОГ: сценариев", len(REPORT["scenarios"]), "с замечаниями:", bad, flush=True)
    return 0


def _known(d):
    j = lambda n: os.path.join(d, n)  # noqa: E731
    return {"acts": j("акты кв 1-5.pdf"), "receipts": j("расписки кв 1-5.pdf"),
            "acts_nosign": j("акты без подписей.pdf"), "pages10": j("десять страниц.pdf"), "a3": j("файл А.pdf"),
            "b3": j("файл Б.pdf"), "c3": j("файл В.pdf"), "password": j("с паролем.pdf"), "broken": j("битый.pdf"),
            "empty": j("пустой.pdf"), "notpdf": j("на самом деле текст.pdf"), "sort_scan": j("сканы для сортировки.pdf"),
            "sort_nums": ["17", "3", "45", "8", "112", "21", "5", "60"], "scan20": j("скан 20 страниц.pdf"),
            "big": j("большой скан 320 страниц.pdf"), "stamp": j("печать.png"), "photo": j("фото.jpg")}


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
