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
from app.ui.infra import settings  # noqa: E402
settings().clear()
w = MainWindow()
w.resize(1500, 950)
w.show()


def shot(name):
    import time
    for _ in range(20):
        app.processEvents()
        time.sleep(0.03)
    w.grab().save(os.path.join(OUT, name))


tmp = tempfile.mkdtemp()
w.sign.open_acts(f"{D}/job2/fileB_3.5MB.pdf")
w.sign.open_receipts(f"{D}/job2/fileA_3.0MB.pdf")
w.sign.set_mapping([3, 4, 7, 6, None, 0, 2])
w.sign.table.setCurrentCell(0, 2)
w.sign.show_row(0)
w.sign.update_ink_preview()
w.sign.tbar.save("Акт ЦО (Кржижановского)")
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
w.batch._summary.grab().save(os.path.join(OUT, "5b_paket_itog.png"))
print(w.batch.summary_text(w.batch.last_results))
print("ok", sorted(os.listdir(tmp)))
