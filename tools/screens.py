"""Скриншоты вкладок без экрана: QT_QPA_PLATFORM=offscreen python tools/screens.py"""
import os

os.environ["PDFTOOL_SETTINGS"] = "PdfToolTest"
import sys
import tempfile

os.environ["PDFTOOL_TEMPLATES"] = tempfile.mkdtemp(prefix="pdftool_scr_tpl_")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# окна-сообщения в консоль, чтобы скрипт не останавливался
QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[2]))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARN:", a[2]))

from app.main import MainWindow  # noqa: E402

D = os.environ.get("PDFTOOL_DATA", "/workspace")
OUT = os.path.join(os.path.dirname(__file__), "..", "screens")
os.makedirs(OUT, exist_ok=True)
app = QApplication(sys.argv)
from app.main import setup_app  # noqa: E402
from app.ui.infra import settings  # noqa: E402
settings().clear()
for k in ("sign", "batch", "fragment", "sort", "image", "pages"):
    settings().setValue(f"tour/{k}", True)   # не показывать предложение подсказки на скриншотах
setup_app(app)
W, H = (int(x) for x in os.environ.get("SCREEN_SIZE", "1366x768").split("x"))
w = MainWindow()
w.resize(W, H)
w.show()
P = os.environ.get("SCREEN_PREFIX", "v13_")


def shot(name, keep_toasts=False):
    import time
    if not keep_toasts:
        for t in list(w._toasts):
            t.close_now()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.03)
    w.grab().save(os.path.join(OUT, P + name))


tmp = tempfile.mkdtemp()
w.sign.open_acts(f"{D}/job2/fileB_3.5MB.pdf")
w.sign.open_receipts(f"{D}/job2/fileA_3.0MB.pdf")
w.sign.set_mapping([3, 4, 7, 6, None, 0, 2])
w.sign.table.setCurrentCell(0, 2)
w.sign.show_row(0)
w.sign.update_ink_preview()
w.sign.tbar.save("Акт ЦО (Кржижановского)")
for t in list(w._toasts):
    t.close_now()
shot("1_podpisi.png")
w.sign.save(os.path.join(tmp, "sign.pdf"))

w.tabs.setCurrentWidget(w.sort)
w.sort.open(f"{D}/job3/input.pdf")
w.sort.recognize()
import time  # noqa: E402
t0 = time.time()
while not any(w.sort.values_by_page()) and time.time() - t0 < 60:
    app.processEvents()
    time.sleep(0.05)
for _ in range(30):
    app.processEvents()
    time.sleep(0.03)
w.sort.table.setCurrentCell(0, 1)
shot("2_sortirovka.png")
w.sort.save(os.path.join(tmp, "sorted.pdf"))

w.tabs.setCurrentWidget(w.image)
w.image.open_pdf(f"{D}/job2/fileA_3.0MB.pdf")
w.image.open_image(f"{D}/job2/c4_v.png")
w.image.chk_white.setChecked(True)
shot("3_kartinka.png")
w.image.save(os.path.join(tmp, "img.pdf"))

w.tabs.setCurrentWidget(w.pages)
w.pages.add_files([f"{D}/job2/fileA_3.0MB.pdf", f"{D}/job2/fileB_3.5MB.pdf"])
w.pages.open(f"{D}/job2/fileB_3.5MB.pdf")
w.pages.grid.item(1).setSelected(True)
w.pages.rotate(90)
shot("4_stranicy.png")
w.pages.save(os.path.join(tmp, "pages.pdf"))
w.pages.do_merge(os.path.join(tmp, "merged.pdf"))

w.tabs.setCurrentWidget(w.batch)
w.batch.add_pair(f"{D}/job2/fileB_3.5MB.pdf", f"{D}/job2/fileA_3.0MB.pdf", "Акт ЦО (Кржижановского)",
                 os.path.join(tmp, "пакет_1.pdf"))
w.batch.add_pair(f"{D}/job2/fileB_3.5MB.pdf", f"{D}/job2/fileA_3.0MB.pdf", None, os.path.join(tmp, "пакет_2.pdf"))
w.batch.items[0].mapping = [3, 4, 7, 6, None, 0, 2]
w.batch.fill()
w.batch.run_all()
from PySide6.QtCore import QThreadPool  # noqa: E402
t0 = time.time()
while not w.batch.last_results and time.time() - t0 < 120:
    app.processEvents()
    QThreadPool.globalInstance().waitForDone(50)
for _ in range(20):
    app.processEvents()
    time.sleep(0.03)
w.batch._summary.move(w.geometry().center() - w.batch._summary.rect().center())
shot("5_paket.png")
w.batch._summary.grab().save(os.path.join(OUT, P + "5b_paket_itog.png"))
w.batch._summary.close()

w.show_screen(w.fragment)
from app.core.pdfutil import NRect  # noqa: E402
fr = w.fragment
fr.open_src(os.path.join(os.path.dirname(__file__), "..", "qa", "samples", "source_synthetic.pdf"))
fr.open_dst(f"{D}/job2/fileA_3.0MB.pdf")
fr.cb_mode.setCurrentIndex(fr.cb_mode.findData("color"))
fr.view_src.set_nrect(NRect(0.445, 0.582, 0.675, 0.738))
fr.recut()
fr.view_dst.set_nrect(NRect(0.55, 0.62, 0.78, 0.78), fr.view_dst.boxes["main"].pixmap)
fr.commit("место")
fr.place_current()
shot("10_fragment.png")
fr.save(os.path.join(tmp, "fragment.pdf"))
tour = w.start_tour()
tour.go(2)
shot("10_tour_fragment_3.png")
tour.close_tour()

# пошаговые подсказки
w.show_screen(w.sign)
tour = w.start_tour()
for i, nm in ((0, "6_tour_podpisi_1.png"), (2, "6_tour_podpisi_3.png"), (6, "6_tour_podpisi_7.png"),
              (10, "6_tour_podpisi_11.png")):
    tour.go(i)
    shot(nm)
tour.close_tour()
w.show_screen(w.batch)
tour = w.start_tour()
tour.go(3)
shot("6_tour_paket_4.png")
tour.close_tour()
w.show_screen(w.helppage)
shot("7_spravka.png")
w.show_screen(w.sign)
w.toast("Готово! Сохранён файл расписки_с_подписями.pdf. Подписей поставлено: 6.", "ok",
        action=("Открыть папку", lambda: None))
shot("8_uvedomlenie.png", keep_toasts=True)
w.toggle_theme()
shot("9_temnaya_tema.png")
w.toggle_theme()
print(w.batch.summary_text(w.batch.last_results))
print("ok", sorted(os.listdir(tmp)))
if os.environ.get("SCREEN_KEEP"):
    import shutil
    shutil.copytree(tmp, os.environ["SCREEN_KEEP"], dirs_exist_ok=True)
