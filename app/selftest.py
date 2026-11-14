"""Самопроверка собранной программы: `PdfTool.exe --self-test <папка>`.

Нужна для CI (проверить, что в exe попали шрифты, иконки, модель цифр, что работают PyMuPDF и Qt,
пути с кириллицей и пробелами) и для поддержки: на любом ПК можно запустить и прислать selftest.json.
Ничего не меняет в настройках пользователя: свои настройки и папка шаблонов внутри <папки>.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback


def _make_pdf(path: str, n: int, font: str, text: str) -> None:
    import pymupdf
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page()
        p.insert_text((72, 100), text.format(i=i + 1), fontname="inter", fontfile=font, fontsize=14)
        p.draw_bezier((120, 640), (160, 600), (200, 680), (260, 630), color=(0.1, 0.2, 0.7), width=2)
    d.save(path)
    d.close()


def run(out_dir: str) -> int:
    out_dir = os.path.abspath(out_dir)
    work = os.path.join(out_dir, "Проверка путей с пробелами")
    os.makedirs(work, exist_ok=True)
    os.environ["PDFTOOL_SETTINGS"] = "PdfToolSelfTest"
    os.environ["PDFTOOL_TEMPLATES"] = os.path.join(work, "шаблоны")
    report: dict = {"frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform, "checks": {}}

    def check(name, fn):
        t0 = time.perf_counter()
        try:
            res = fn()
            report["checks"][name] = {"ok": True, "result": res, "sec": round(time.perf_counter() - t0, 2)}
        except Exception:  # noqa: BLE001
            report["checks"][name] = {"ok": False, "error": traceback.format_exc()}

    from PySide6.QtWidgets import QApplication
    from . import APP_NAME, __version__
    report["version"] = __version__
    import platform
    report["machine"] = platform.machine()
    report["os"] = platform.mac_ver()[0] and f"macOS {platform.mac_ver()[0]}" or platform.platform()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AkvaTehStroy")

    from .main import MainWindow, setup_app
    from .ui import style

    def assets():
        fonts = [style.asset("fonts", f) for f in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")]
        missing = [f for f in fonts if not os.path.isfile(f)]
        assert not missing, f"нет шрифтов: {missing}"
        setup_app(app)
        from PySide6.QtGui import QFontDatabase
        assert "Inter" in QFontDatabase.families(), "запасной шрифт Inter не загрузился"
        fam = app.font().family()
        if sys.platform == "darwin":       # системный шрифт macOS (SF Pro)
            assert fam == QFontDatabase.systemFont(QFontDatabase.GeneralFont).family(), fam
        elif sys.platform.startswith("win"):
            assert fam.startswith("Segoe UI") or fam == "Inter", fam
        icons = [n for n in ("pen-tool", "layers", "scissors", "image", "files", "save", "circle-help",
                             "chevron-up", "chevron-down")
                 if style.icon(n).isNull()]
        assert not icons, f"не загрузились иконки: {icons}"
        assert os.path.isfile(style.asset("icons", "app-logo.png")), "нет логотипа"
        from PySide6 import __version__ as qt
        import pymupdf
        return {"font": fam, "qt": qt, "pymupdf": pymupdf.VersionBind,
                "data_dir": __import__("app.core.logsetup", fromlist=["log_dir"]).log_dir()}
    check("шрифты и иконки", assets)

    def digits():
        import numpy as np
        from .core import digits as dg
        p = dg.classify(np.zeros((2, 28, 28), np.float32))
        assert p.shape == (2, 10)
        return os.path.basename(dg._model_path())
    check("модель цифр", digits)

    font = style.asset("fonts", "Inter-Regular.ttf")
    acts = os.path.join(work, "Акты кв 1-3.pdf")
    rec = os.path.join(work, "Расписки кв 1-3.pdf")

    def core():
        import pymupdf
        from PIL import Image
        from .core import fragment, imageinsert, pages, signatures, sorting, templates
        from .core.pdfutil import NRect
        _make_pdf(acts, 3, font, "Акт кв. № {i}")
        _make_pdf(rec, 3, font, "Расписка кв. № {i}")
        job = signatures.TransferJob(acts, rec, [0, None, 2], src_rect=NRect(0.15, 0.70, 0.50, 0.82),
                                     dst_rect=NRect(0.5, 0.85, 0.9, 0.95))
        rep = signatures.apply_transfer(job, os.path.join(work, "с подписями.pdf"))
        assert len(rep["placed"]) == 2 and rep["blank"] == [1], rep
        assert pages.merge([acts, rec], os.path.join(work, "объединённый.pdf")) == 6
        assert len(pages.split_every_page(acts, work)) == 3
        with pymupdf.open(acts) as d:
            assert sorting.guess_all(d) == ["1", "2", "3"]
        png = os.path.join(work, "печать.png")
        Image.new("RGB", (200, 200), (30, 60, 200)).save(png)
        img = imageinsert.load_image(png, True)
        imageinsert.insert_image(acts, img, NRect(.3, .3, .5, .5), [0, 2], os.path.join(work, "с картинкой.pdf"))
        pl = fragment.Placement(acts, 0, NRect(0.15, 0.70, 0.50, 0.82), 1, NRect(0.5, 0.1, 0.85, 0.2))
        fragment.apply(rec, [pl], os.path.join(work, "с фрагментом.pdf")).close()
        t = templates.Template("Акт ЦО «тест»", src_rect=NRect(0.1, 0.1, 0.5, 0.2))
        templates.save(t)
        assert templates.get(t.name) is not None
        return "ok"
    check("операции с PDF", core)

    def window():
        from .ui.infra import settings
        for k in ("sign", "batch", "fragment", "sort", "image", "pages"):   # без приглашений к подсказкам
            settings().setValue(f"tour/{k}", True)
        w = MainWindow()
        w.show()
        app.processEvents()
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i)
            app.processEvents()
        w.show_screen(w.sign)
        if os.path.exists(acts) and os.path.exists(rec):
            w.sign.open_acts(acts)
            w.sign.open_receipts(rec)
        def pump(sec):
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < sec:
                app.processEvents()
                time.sleep(0.02)
        pump(1.5)
        shot = os.path.join(out_dir, "selftest_window.png")
        assert w.grab().save(shot), "не удалось сохранить снимок окна"
        shots = [os.path.basename(shot)]
        size = w.screen().availableGeometry().size() if w.screen() else None
        w.resize(min(1280, size.width() if size else 1280), min(800, size.height() if size else 800))
        no_hscroll = {}
        for mode in ("light", "dark"):
            w.set_theme_mode(mode)
            pump(0.8)
            p = os.path.join(out_dir, f"selftest_window_{mode}.png")
            assert w.grab().save(p), "не удалось сохранить снимок окна"
            shots.append(os.path.basename(p))
        w.show_screen(w.fragment)
        pump(0.5)
        w.grab().save(os.path.join(out_dir, "selftest_fragment_dark.png"))
        w.set_theme_mode("light")
        w.resize(1024, 700)
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i)
            pump(0.2)
            no_hscroll[type(w.tabs.currentWidget()).__name__] = w.tabs.widget(i).horizontalScrollBar().maximum() == 0
        w.show_screen(w.pages)
        pump(0.3)
        w.grab().save(os.path.join(out_dir, "selftest_narrow_1024.png"))
        bad = [k for k, ok in no_hscroll.items() if not ok]
        assert not bad, f"прокрутка вбок при ширине окна 1024: {bad}"
        w.set_theme_mode("system")
        for t in w.all_tabs():
            t.undo_stack.setClean()
        w.close()
        return {"shots": shots, "compact_at_1024": bool(w._compact)}
    check("окно программы", window)

    report["ok"] = all(c["ok"] for c in report["checks"].values())
    with open(os.path.join(out_dir, "selftest.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return 0 if report["ok"] else 1
