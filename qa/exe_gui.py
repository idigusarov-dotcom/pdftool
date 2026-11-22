"""Проверка настоящего PdfTool.exe (релиз 1.4.0) через UI Automation (pywinauto), как это делал бы человек.

    python qa/exe_gui.py <PdfTool.exe> <папка данных> <папка отчёта>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import winreg

import psutil
import win32con
import win32gui
from PIL import ImageGrab
from pywinauto import Desktop
from pywinauto.keyboard import send_keys

EXE, DATA, OUT = (os.path.abspath(a) for a in sys.argv[1:4])
SHOTS = os.path.join(OUT, "exe_shots")
WORK = os.path.join(OUT, "exe_work")
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(WORK, exist_ok=True)
REG = r"Software\AkvaTehStroy\PdfTool"
LOG = os.path.join(os.environ["APPDATA"], "PdfTool", "log.txt")
REPORT = {"exe": EXE, "exe_size": os.path.getsize(EXE), "flows": []}
TITLE = ".*PDF-помощник.*версия.*"


def reg_clear():
    def rm(root, path):
        try:
            k = winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS)
        except FileNotFoundError:
            return
        while True:
            try:
                sub = winreg.EnumKey(k, 0)
            except OSError:
                break
            rm(root, path + "\\" + sub)
        winreg.CloseKey(k)
        winreg.DeleteKey(root, path)
    rm(winreg.HKEY_CURRENT_USER, REG)


def reg_set(name, value, kind=winreg.REG_SZ):
    parts = name.split("/")
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG + ("\\" + "\\".join(parts[:-1]) if len(parts) > 1 else ""))
    winreg.SetValueEx(k, parts[-1], 0, kind, value)
    winreg.CloseKey(k)


def no_tours():
    for k in ("sign", "batch", "fragment", "sort", "image", "pages", "compress"):
        reg_set(f"tour/{k}", "true")


class App:
    def __init__(self, args=(), env=None, wait=True):
        e = dict(os.environ)
        e.update(env or {})
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen([EXE, *args], env=e)
        self.ps = psutil.Process(self.proc.pid)
        self.peak = 0
        self._stop = False
        threading.Thread(target=self._sample, daemon=True).start()
        self.win = None
        if wait:
            self.wait_window()

    def pids(self):
        try:
            return [self.ps.pid] + [c.pid for c in self.ps.children(recursive=True)]
        except psutil.Error:
            return []

    def rss(self):
        s = 0
        for pid in self.pids():
            try:
                s += psutil.Process(pid).memory_info().rss
            except psutil.Error:
                pass
        return s

    def _sample(self):
        while not self._stop:
            # у exe «в одном файле» два процесса: распаковщик и сама программа — считаем программу (больший)
            try:
                m = max((psutil.Process(p).memory_info().rss for p in self.pids()), default=0)
                self.peak = max(self.peak, m)
            except psutil.Error:
                pass
            time.sleep(0.1)

    def wait_window(self, timeout=90):
        t = time.monotonic()
        while time.monotonic() - t < timeout:
            for w in Desktop(backend="uia").windows(title_re=TITLE):
                if w.process_id() in self.pids():
                    self.win = w
                    self.startup = time.monotonic() - self.t0
                    time.sleep(1.0)
                    return w
            if self.proc.poll() is not None:
                raise RuntimeError(f"процесс завершился до появления окна, код {self.proc.returncode}")
            time.sleep(0.3)
        raise TimeoutError("окно не появилось")

    def alive(self):
        return self.proc.poll() is None

    def close(self, timeout=60):
        if self.win is not None and self.alive():
            try:
                win32gui.PostMessage(self.win.handle, win32con.WM_CLOSE, 0, 0)
            except Exception:  # noqa: BLE001
                pass
        try:
            rc = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            rc = "timeout"
            self.kill()
        self._stop = True
        return rc

    def kill(self):
        for pid in reversed(self.pids()):
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass


def shot(app_or_win, name):
    try:
        w = app_or_win.win if isinstance(app_or_win, App) else app_or_win
        w.set_focus()
        time.sleep(0.4)
        r = w.rectangle()
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(
            os.path.join(SHOTS, name + ".png"))
    except Exception:  # noqa: BLE001
        ImageGrab.grab(all_screens=True).save(os.path.join(SHOTS, name + "_screen.png"))


def btn(win, title_re, timeout=10):
    b = win.child_window(title_re=title_re, control_type="Button")
    b.wait("exists enabled", timeout=timeout)
    return b


def click(win, title_re, ctype="Button", timeout=10, idx=0):
    el = win.child_window(title_re=title_re, control_type=ctype, found_index=idx)
    el.wait("exists", timeout=timeout)
    el.click_input()
    return el


def texts(win):
    out = []
    for e in win.descendants(control_type="Text"):
        try:
            out.append(e.window_text())
        except Exception:  # noqa: BLE001
            pass
    return out


def wait_text(win, pred, timeout=120):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        for s in texts(win):
            if pred(s):
                return s
        time.sleep(0.5)
    return None


def file_dialog(path, timeout=20):
    """Стандартное окно Windows «Открыть» / «Сохранить как»: ввести путь в поле имени и Enter."""
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        for d in Desktop(backend="win32").windows(class_name="#32770"):
            try:
                if not d.is_visible():
                    continue
                ed = None
                for cand in d.descendants(class_name="Edit"):
                    if cand.is_visible():
                        ed = cand
                        break
                if ed is None:
                    continue
                d.set_focus()
                ed.set_edit_text(path)
                time.sleep(0.3)
                ed.type_keys("{ENTER}")
                time.sleep(0.8)
                # подтверждение «заменить?» — да
                for d2 in Desktop(backend="win32").windows(class_name="#32770"):
                    if d2.is_visible() and d2.handle != d.handle and "Подтвер" in d2.window_text() + "Confirm":
                        send_keys("%y")
                return True
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.3)
    return False


def error_windows(app: App):
    """Окна «Ошибка» (необработанное исключение) и окна WER «программа перестала работать»."""
    found = []
    for w in Desktop(backend="win32").windows():
        try:
            t = w.window_text()
            if w.process_id() in app.pids() and t == "Ошибка":
                found.append(t)
            if "PdfTool" in t and ("перестала" in t or "stopped" in t.lower() or "WerFault" in t):
                found.append(t)
        except Exception:  # noqa: BLE001
            pass
    return found


def log_errors(since_size):
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        f.seek(since_size)
        tail = f.read()
    return [ln for ln in tail.splitlines() if " ERROR " in ln or "Необработанная" in ln]


FLOWS = []


def flow(title):
    def deco(fn):
        FLOWS.append((title, fn))
        return fn
    return deco


# ---------------------------------------------------------------------------------------------------
@flow("Первый запуск без настроек: время запуска, память, все экраны, обе темы")
def f_start(r):
    reg_clear()
    a = App()
    r["startup_sec"] = round(a.startup, 1)
    shot(a, "01_first_launch_tour_offer")
    no_tours()
    names = ["Подписи", "Пакет", "Фрагменты", "Сортировка", "Картинка", "Страницы", "Сжатие", "Справка"]
    for i, n in enumerate(names):
        try:
            click(a.win, f"^{n}$")
            time.sleep(0.8)
            shot(a, f"02_screen_{i + 1}")
        except Exception as e:  # noqa: BLE001
            r["errors"].append(f"экран «{n}»: {e}")
    # тема: кнопка «луна/солнце»
    try:
        a.win.child_window(title_re=".*[Тт]ёмная.*светлая.*", control_type="Button").click_input()
        time.sleep(1)
        shot(a, "03_theme_toggled")
        a.win.child_window(title_re=".*[Тт]ёмная.*светлая.*", control_type="Button").click_input()
    except Exception as e:  # noqa: BLE001
        r["notes"].append(f"кнопка темы по имени не найдена ({e}); темы проверены через настройку ниже")
    r["rss_mb"] = round(a.rss() / 1e6)
    r["errors"] += error_windows(a)
    r["exit"] = a.close()
    r["peak_mb"] = round(a.peak / 1e6)


@flow("Тёмная тема из настроек и масштаб 125% / 150% (QT_SCALE_FACTOR)")
def f_dpi(r):
    reg_clear()
    no_tours()
    reg_set("ui/theme", "dark")
    a = App()
    click(a.win, "^Сжатие$")
    time.sleep(0.5)
    shot(a, "04_dark")
    r["exit_dark"] = a.close()
    reg_set("ui/theme", "light")
    for f in ("1.25", "1.5"):
        a = App(env={"QT_SCALE_FACTOR": f})
        time.sleep(1)
        rect = a.win.rectangle()
        r["notes"].append(f"масштаб {f}: окно {rect.width()}x{rect.height()}")
        for n in ("Подписи", "Страницы", "Сжатие"):
            click(a.win, f"^{n}$")
            time.sleep(0.6)
            shot(a, f"05_scale_{f}_{n}")
        r["errors"] += error_windows(a)
        r[f"exit_{f}"] = a.close()


@flow("Окно уменьшено до минимума и развёрнуто на весь экран")
def f_resize(r):
    a = App()
    h = a.win.handle
    win32gui.MoveWindow(h, 0, 0, 300, 200, True)
    time.sleep(1)
    rect = a.win.rectangle()
    r["notes"].append(f"минимальный размер окна, который даёт программа: {rect.width()}x{rect.height()}")
    shot(a, "06_min_size")
    win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
    time.sleep(1)
    shot(a, "06_maximized")
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    r["alive"] = a.alive()
    r["exit"] = a.close()


@flow("Сжатие через окна Windows: выбрать файл, «Среднее», сохранить, окно хода; затем «Отмена»; "
      "двойной щелчок по кнопке")
def f_compress(r):
    no_tours()
    a = App()
    logsz = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    click(a.win, "^Сжатие$")
    time.sleep(0.5)
    click(a.win, "^PDF для сжатия.*", "Text")
    big = os.path.join(DATA, "большой скан 320 страниц.pdf")
    r["checks"].append(("окно «Открыть» появилось и принято", file_dialog(big)))
    got = wait_text(a.win, lambda s: s.startswith("Сейчас:"), 60)
    r["checks"].append((f"файл открыт: {got}", bool(got)))
    click(a.win, "^Сильное.*", "RadioButton")
    out = os.path.join(WORK, "сжатый через окно.pdf")
    t = time.monotonic()
    # двойной щелчок по кнопке: второй щелчок не должен запустить второе сжатие
    b = btn(a.win, "^Сжать и сохранить.*")
    b.click_input(double=True)
    r["checks"].append(("окно «Сохранить как» появилось", file_dialog(out)))
    time.sleep(2)
    prog = [w for w in Desktop(backend="uia").windows(title="Подождите") if w.process_id() in a.pids()]
    if prog:
        shot(prog[0], "07_progress_compress")
    # пока идёт: щёлкнуть ещё раз по кнопке и по пункту меню — окно хода должно это заблокировать
    try:
        btn(a.win, "^Сжать и сохранить.*", timeout=2).click_input()
        click(a.win, "^Страницы$", timeout=2)
    except Exception as e:  # noqa: BLE001
        r["notes"].append(f"щелчки во время работы: {e}")
    res = wait_text(a.win, lambda s: "→" in s or "Меньше не стало" in s, 300)
    dt = time.monotonic() - t
    r["checks"].append((f"итог «было → стало»: {res} за {dt:.0f} с", bool(res)))
    r["checks"].append(("файл создан", os.path.exists(out)))
    extra = [d for d in Desktop(backend="win32").windows(class_name="#32770") if d.is_visible()
             and d.process_id() in a.pids()]
    r["checks"].append((f"второе окно «Сохранить как» не открылось ({len(extra)})", not extra))
    for d in extra:
        d.close()
    shot(a, "08_compress_done")
    # «Отмена»
    out2 = os.path.join(WORK, "отменённое сжатие.pdf")
    btn(a.win, "^Сжать и сохранить.*").click_input()
    file_dialog(out2)
    t = time.monotonic()
    cancelled = False
    while time.monotonic() - t < 30 and not cancelled:
        for w in Desktop(backend="uia").windows(title="Подождите"):
            if w.process_id() in a.pids():
                time.sleep(1)
                try:
                    w.child_window(title="Отмена", control_type="Button").click_input()
                    cancelled = True
                except Exception:  # noqa: BLE001
                    pass
                break
        time.sleep(0.3)
    time.sleep(3)
    r["checks"].append(("кнопка «Отмена» нажата", cancelled))
    r["checks"].append(("после «Отмены» файла нет", not os.path.exists(out2)))
    r["checks"].append(("временных файлов нет", not [f for f in os.listdir(WORK) if f.endswith(".tmp")]))
    r["errors"] += error_windows(a) + log_errors(logsz)
    r["exit"] = a.close()
    r["peak_mb"] = round(a.peak / 1e6)


@flow("Картинка: печать на все 320 страниц через окна Windows")
def f_image(r):
    a = App()
    logsz = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    click(a.win, "^Картинка$")
    time.sleep(0.5)
    click(a.win, "^PDF$", "Text")
    file_dialog(os.path.join(DATA, "большой скан 320 страниц.pdf"))
    time.sleep(2)
    click(a.win, "^Картинка$", "Text")
    file_dialog(os.path.join(DATA, "печать.png"))
    time.sleep(1)
    click(a.win, "^все страницы$", "RadioButton")
    out = os.path.join(WORK, "с печатью через окно.pdf")
    btn(a.win, ".*Сохранить новый PDF.*").click_input()
    file_dialog(out)
    t = time.monotonic()
    while time.monotonic() - t < 180 and not os.path.exists(out):
        time.sleep(0.5)
    r["checks"].append((f"файл с печатью создан за {time.monotonic() - t:.0f} с", os.path.exists(out)))
    shot(a, "09_image_done")
    r["errors"] += error_windows(a) + log_errors(logsz)
    r["exit"] = a.close()
    r["peak_mb"] = round(a.peak / 1e6)


@flow("Страницы: объединить 3 файла (выбор нескольких в окне Windows) и сохранить")
def f_merge(r):
    a = App()
    click(a.win, "^Страницы$")
    time.sleep(0.5)
    click(a.win, "^Файлы для объединения$", "Text")
    file_dialog(DATA + "\\")
    time.sleep(1)
    file_dialog('"файл А.pdf" "файл Б.pdf" "файл В.pdf"')
    time.sleep(2)
    out = os.path.join(WORK, "объединённый через окно.pdf")
    btn(a.win, "^Объединить и сохранить.*").click_input()
    file_dialog(out)
    time.sleep(3)
    r["checks"].append(("объединённый файл создан", os.path.exists(out)))
    shot(a, "10_merge_done")
    r["errors"] += error_windows(a)
    r["exit"] = a.close()


@flow("Закрытие окна крестиком во время сжатия 320 страниц")
def f_close_during(r):
    a = App()
    click(a.win, "^Сжатие$")
    click(a.win, "^PDF для сжатия.*", "Text")
    file_dialog(os.path.join(DATA, "большой скан 320 страниц.pdf"))
    wait_text(a.win, lambda s: s.startswith("Сейчас:"), 60)
    click(a.win, "^Лёгкое.*", "RadioButton")
    out = os.path.join(WORK, "сжатие при закрытии.pdf")
    btn(a.win, "^Сжать и сохранить.*").click_input()
    file_dialog(out)
    time.sleep(3)
    win32gui.PostMessage(a.win.handle, win32con.WM_CLOSE, 0, 0)
    t = time.monotonic()
    try:
        rc = a.proc.wait(180)
    except subprocess.TimeoutExpired:
        rc = "не закрылась за 180 с"
    r["notes"].append(f"после WM_CLOSE: код выхода {rc} через {time.monotonic() - t:.0f} с, "
                      f"файл создан={os.path.exists(out)}")
    r["checks"].append(("программа завершилась без падения (код 0 или ещё работает)", rc in (0, "не закрылась за 180 с")))
    r["checks"].append(("временных файлов нет", not [f for f in os.listdir(WORK) if f.endswith(".tmp")]))
    a.kill()


@flow("Два экземпляра одновременно")
def f_two(r):
    a1 = App()
    a2 = App()
    click(a1.win, "^Страницы$")
    click(a2.win, "^Сжатие$")
    time.sleep(1)
    r["checks"].append(("оба окна живы", a1.alive() and a2.alive()))
    r["exit1"] = a1.close()
    r["exit2"] = a2.close()
    r["checks"].append(("оба закрылись с кодом 0", r["exit1"] == 0 and r["exit2"] == 0))


@flow("Повреждённые настройки в реестре")
def f_corrupt(r):
    reg_clear()
    reg_set("ui/theme", "12345")
    reg_set("ui/dark", "может быть")
    reg_set("sort/zone1", "абв")
    reg_set("last_dir", "Z:\\нет такой папки")
    reg_set("compress/level", "ультра")
    reg_set("tour/sign", "наверное")
    reg_set("sign/src_rect", b"\x00\x01\x02", winreg.REG_BINARY)
    reg_set("sort/zone2", 7, winreg.REG_DWORD)
    a = App()
    for n in ("Сортировка", "Сжатие", "Подписи"):
        try:
            click(a.win, f"^{n}$")
            time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            r["errors"].append(f"{n}: {e}")
    shot(a, "11_corrupt_settings")
    r["errors"] += error_windows(a)
    r["checks"].append(("окно открылось и работает", a.alive()))
    r["exit"] = a.close()
    reg_clear()
    no_tours()


@flow("Открытие файла из командной строки («Открыть с помощью»): кириллица, пробелы, битый файл")
def f_cmdline(r):
    for name in ("акты кв 1-5.pdf", "битый.pdf", "с паролем.pdf"):
        a = App([os.path.join(DATA, name)])
        time.sleep(2)
        tx = [s for s in texts(a.win) if "PDF" in s or "Файл" in s or "файл" in s][:6]
        r["notes"].append(f"{name}: " + " | ".join(tx)[:300])
        r["errors"] += error_windows(a)
        r[f"exit {name}"] = a.close()


def main():
    for title, fn in FLOWS:
        r = {"title": title, "checks": [], "notes": [], "errors": []}
        print("===", title, flush=True)
        t = time.monotonic()
        try:
            fn(r)
        except Exception:  # noqa: BLE001
            r["errors"].append(traceback.format_exc())
            ImageGrab.grab(all_screens=True).save(os.path.join(SHOTS, f"fail_{len(REPORT['flows'])}.png"))
        # убрать оставшиеся процессы
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() == "pdftool.exe":
                try:
                    p.kill()
                except psutil.Error:
                    pass
        r["sec"] = round(time.monotonic() - t, 1)
        r["status"] = "ok" if not r["errors"] and all(c[1] for c in r["checks"]) else "fail"
        REPORT["flows"].append(r)
        with open(os.path.join(OUT, "exe_report.json"), "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=1, default=str)
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str), flush=True)
    if os.path.exists(LOG):
        shutil.copy(LOG, os.path.join(OUT, "exe_app_log.txt"))


if __name__ == "__main__":
    main()
