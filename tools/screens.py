"""Скриншоты вкладок без экрана: QT_QPA_PLATFORM=offscreen python tools/screens.py"""
import os
import sys
import tempfile

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
w = MainWindow()
w.resize(1500, 950)
w.show()


def shot(name):
    for _ in range(5):
        app.processEvents()
    w.grab().save(os.path.join(OUT, name))


tmp = tempfile.mkdtemp()
w.sign.open_acts(f"{D}/job2/fileB_3.5MB.pdf")
w.sign.open_receipts(f"{D}/job2/fileA_3.0MB.pdf")
w.sign.set_mapping([3, 4, 7, 6, None, 0, 2])
w.sign.table.setCurrentCell(0, 1)
w.sign.show_row(0)
shot("1_podpisi.png")
w.sign.save(os.path.join(tmp, "sign.pdf"))

w.tabs.setCurrentIndex(1)
w.sort.open(f"{D}/job3/input.pdf")
w.sort.set_values([str(x) for x in (41, 38, 80, 40, 107, 89, 121, 83, 77, 141, 43, 34, 87, 81, 26, 91, 133, 58, 130, 127,
                                    124, 52, 84, 123, 24, 75, 72, 69, 144, 126, 147, 90, 116, 119, 110, 113, 112, 138, 150, 48,
                                    46, 55, 45, 42, 32, 49, 117, 114, 35, 33, 36, 39, 100, 37, 44, 108, 132, 109, 94, 31, 97)]
                  + [None] * 100)
w.sort.table.item(3, 1).setText("41")  # пример повтора
w.sort.table.setCurrentCell(0, 1)
shot("2_sortirovka.png")
w.sort.save(os.path.join(tmp, "sorted.pdf"))

w.tabs.setCurrentIndex(2)
w.image.open_pdf(f"{D}/job2/fileA_3.0MB.pdf")
w.image.open_image(f"{D}/job2/c4_v.png")
w.image.chk_white.setChecked(True)
shot("3_kartinka.png")
w.image.save(os.path.join(tmp, "img.pdf"))

w.tabs.setCurrentIndex(3)
w.pages.add_files([f"{D}/job2/fileA_3.0MB.pdf", f"{D}/job2/fileB_3.5MB.pdf"])
w.pages.open(f"{D}/job2/fileB_3.5MB.pdf")
w.pages.grid.item(1).setSelected(True)
w.pages.rotate(90)
shot("4_stranicy.png")
w.pages.save(os.path.join(tmp, "pages.pdf"))
w.pages.do_merge(os.path.join(tmp, "merged.pdf"))
print("ok", sorted(os.listdir(tmp)))
