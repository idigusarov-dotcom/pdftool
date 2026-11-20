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
        fam = app.font().family()
        assert fam == "Inter", f"шрифт не загрузился: {fam}"
        icons = [n for n in ("pen-tool", "layers", "scissors", "image", "files", "save", "circle-help")
                 if style.icon(n).isNull()]
        assert not icons, f"не загрузились иконки: {icons}"
        return {"font": fam}
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
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 1.5:
            app.processEvents()
            time.sleep(0.02)
        shot = os.path.join(out_dir, "selftest_window.png")
        assert w.grab().save(shot), "не удалось сохранить снимок окна"
        for t in w.all_tabs():
            t.undo_stack.setClean()
        w.close()
        return os.path.basename(shot)
    check("окно программы", window)

    report["ok"] = all(c["ok"] for c in report["checks"].values())
    with open(os.path.join(out_dir, "selftest.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return 0 if report["ok"] else 1
