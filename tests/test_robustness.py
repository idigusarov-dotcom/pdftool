"""Надёжность на синтетических файлах (работает и в CI на Windows, без реальных сканов):
битые/пустые/запароленные PDF, пути с кириллицей и пробелами, сохранение, фото с телефона, самопроверка."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from app.core import batch, imageinsert, pages, signatures, sorting, templates  # noqa: E402
from app.core.pdfutil import NRect, PdfError, check_output_path, open_pdf, same_file, save_doc  # noqa: E402

FONT = os.path.join(os.path.dirname(__file__), "..", "app", "ui", "assets", "fonts", "Inter-Regular.ttf")
SRC = NRect(0.15, 0.70, 0.50, 0.82)
DST = NRect(0.5, 0.85, 0.9, 0.95)


def make_pdf(path, n, text="Акт кв. № {i}"):
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page()
        p.insert_text((72, 100), text.format(i=i + 1), fontname="inter", fontfile=FONT, fontsize=14)
        p.draw_line((72, 600), (500, 600))
        p.draw_bezier((120, 640), (160, 600), (200, 680), (260, 630), color=(0.1, 0.2, 0.7), width=2)
    d.save(path)
    d.close()
    return path


@pytest.fixture
def files(tmp_path):
    d = tmp_path / "Папка с пробелами и кириллицей"
    d.mkdir()
    f = {"dir": str(d), "acts": make_pdf(str(d / "Акты кв 1-5.pdf"), 5),
         "rec": make_pdf(str(d / "Расписки кв 1-5.pdf"), 5, "Расписка кв. № {i}")}
    (d / "пустой.pdf").write_bytes(b"")
    (d / "битый.pdf").write_bytes(b"%PDF-1.4\n garbage\n")
    (d / "не pdf.pdf").write_bytes(os.urandom(3000))
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(d / "с паролем.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1", owner_pw="2")
    doc.close()
    for k in ("пустой", "битый", "не pdf", "с паролем"):
        f[k] = str(d / f"{k}.pdf")
    return f


BAD = [("пустой", "пустой"), ("битый", "повреждён"), ("не pdf", "повреждён"), ("с паролем", "паролем")]


@pytest.mark.parametrize("key,word", BAD)
def test_open_pdf_bad_files_give_clear_message(files, key, word):
    with pytest.raises(PdfError) as e:
        open_pdf(files[key])
    assert word in str(e.value) and os.path.basename(files[key]) in str(e.value)


def test_open_pdf_missing(files):
    with pytest.raises(PdfError, match="не найден"):
        open_pdf(os.path.join(files["dir"], "нет такого.pdf"))


def test_same_file_and_output_checks(files, tmp_path):
    a = files["acts"]
    assert same_file(a, os.path.join(files["dir"], ".", os.path.basename(a)))
    if sys.platform.startswith("win"):
        assert same_file(a, a.upper()) and same_file(a, a.replace("\\", "/"))
    with pytest.raises(PdfError, match="исходного"):
        check_output_path(a, (a,))
    with pytest.raises(PdfError, match="Папки"):
        check_output_path(str(tmp_path / "нет папки" / "x.pdf"))
    with pytest.raises(PdfError, match="имя"):
        check_output_path("  ")


def test_save_doc_is_atomic_and_explains_locked_file(files, monkeypatch):
    out = os.path.join(files["dir"], "результат.pdf")
    with open(out, "wb") as f:
        f.write(b"old")
    doc = pymupdf.open(files["acts"])

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PdfError, match="открыт в другой программе"):
        save_doc(doc, out)
    monkeypatch.undo()
    assert open(out, "rb").read() == b"old"                 # старый файл не испорчен
    assert not [n for n in os.listdir(files["dir"]) if n.endswith(".tmp")]   # мусора не осталось
    save_doc(doc, out)
    assert len(pymupdf.open(out)) == 5


def test_core_flows_with_cyrillic_paths(files):
    d, a, r = files["dir"], files["acts"], files["rec"]
    job = signatures.TransferJob(a, r, [0, 1, None, 3, 4], src_rect=SRC, dst_rect=DST)
    out = os.path.join(d, "расписки с подписями.pdf")
    rep = signatures.apply_transfer(job, out)
    assert len(rep["placed"]) == 4 and rep["blank"] == [2]
    assert [len(p.get_images()) for p in pymupdf.open(out)] == [1, 1, 0, 1, 1]
    assert pages.merge([a, r], os.path.join(d, "объединённый.pdf")) == 10
    with pymupdf.open(a) as doc:
        assert sorting.guess_all(doc) == ["1", "2", "3", "4", "5"]
    outs = pages.split(a, pages.parse_ranges("1-2, 5", 5), d)
    assert [len(pymupdf.open(o)) for o in outs] == [2, 1]


def test_stale_mapping_gives_clear_error(files):
    job = signatures.TransferJob(files["acts"], files["rec"], [0, 7, None, None, None], src_rect=SRC, dst_rect=DST)
    out = os.path.join(files["dir"], "x.pdf")
    with pytest.raises(PdfError, match="нет страницы 8"):
        signatures.apply_transfer(job, out)
    assert not os.path.exists(out)


def test_batch_checks_inputs_and_output(files, tmp_path):
    ok = batch.BatchItem(files["acts"], files["rec"], os.path.join(files["dir"], "o.pdf"))
    assert batch.check_items([ok]) == []
    bad = batch.BatchItem(files["битый"], files["rec"], str(tmp_path / "нет" / "o.pdf"))
    errs = batch.check_items([bad])
    assert any("повреждён" in e for e in errs) and any("нет папки" in e for e in errs)
    with pytest.raises(ValueError):
        batch.run_batch([bad])


def test_phone_photo_rotated_and_downscaled(tmp_path):
    p = str(tmp_path / "фото печати.jpg")
    im = Image.new("RGB", (6000, 4000), "white")
    ex = im.getexif()
    ex[0x0112] = 6          # «повернуть на 90°», как пишут телефоны
    im.save(p, exif=ex)
    img = imageinsert.load_image(p, True)
    assert img.width < img.height                       # повёрнута по EXIF
    assert max(img.size) <= imageinsert.MAX_SIDE        # уменьшена
    assert img.mode == "RGBA"


def test_broken_image_clear_message(tmp_path):
    p = tmp_path / "битая.png"
    p.write_bytes(b"\x89PNG garbage")
    with pytest.raises(PdfError, match="повреждён"):
        imageinsert.load_image(str(p))


@pytest.mark.parametrize("name", ["CON", "nul", "Акт: ЦО/2", "COM1.акт"])
def test_template_names_safe_on_windows(name, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", str(tmp_path))
    templates.save(templates.Template(name))
    base = os.path.splitext(os.path.basename(templates._fname(name)))[0]
    assert base.split(".")[0].upper() not in templates._WIN_RESERVED
    assert not any(c in base for c in '\\/:*?"<>|')
    assert templates.get(name) is not None


# ---------------- интерфейс ----------------
@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setenv("PDFTOOL_SETTINGS", "PdfToolTestRobust")
    monkeypatch.setenv("PDFTOOL_TEMPLATES", str(tmp_path / "tpl"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.main import MainWindow
    from app.ui.infra import settings
    settings().clear()
    w = MainWindow()
    w.show()
    app.processEvents()
    yield w
    for t in w.all_tabs():
        t.undo_stack.setClean()
    w.close()


def _openers(w):
    return [(w.sort, "doc", w.sort.open), (w.pages, "doc", w.pages.open), (w.image, "doc", w.image.open_pdf),
            (w.sign, "acts", w.sign.open_acts), (w.sign, "rec", w.sign.open_receipts),
            (w.fragment, "src", w.fragment.open_src), (w.fragment, "dst", w.fragment.open_dst)]


@pytest.mark.parametrize("key", [k for k, _ in BAD])
def test_every_screen_survives_bad_pdf(win, files, key):
    """Битый файл: понятное уведомление, без исключения, ранее открытый файл остаётся."""
    for tab, attr, fn in _openers(win):
        fn(files["acts"])
        before = getattr(tab, attr)
        assert before is not None
        n = len(win._toasts)
        fn(files[key])
        assert getattr(tab, attr) is before
        assert len(win._toasts) == n + 1 and win._toasts[-1].property("kind") == "error"


def test_image_screen_bad_picture(win, files, tmp_path):
    p = tmp_path / "битая.png"
    p.write_bytes(b"\x89PNG garbage")
    win.image.open_pdf(files["acts"])
    n = len(win._toasts)
    win.image.open_image(str(p))
    assert win.image.img_path == "" and len(win._toasts) == n + 1


def test_batch_review_bad_pair_does_not_open_stale(win, files):
    win.batch.add_pair(files["битый"], files["rec"])
    win.batch.table.setCurrentCell(0, 1)
    win.batch.review()
    assert win.sign.job is None              # «Подписи» не открылись на полупустой паре


def test_with_ext():
    from app.ui.widgets import with_ext
    assert with_ext("C:/Документы/итог", "PDF (*.pdf)") == "C:/Документы/итог.pdf"
    assert with_ext("итог.pdf", "PDF (*.pdf)") == "итог.pdf"
    assert with_ext("список", "CSV (*.csv)") == "список.csv"
    assert with_ext("", "PDF (*.pdf)") == ""


def test_selftest_passes(tmp_path, monkeypatch):
    from app import selftest
    import json
    monkeypatch.setenv("PDFTOOL_SETTINGS", "PdfToolSelfTest")   # run() меняет окружение — monkeypatch вернёт
    monkeypatch.setenv("PDFTOOL_TEMPLATES", str(tmp_path / "tpl"))
    assert selftest.run(str(tmp_path)) == 0, open(tmp_path / "selftest.json", encoding="utf-8").read()
    rep = json.load(open(tmp_path / "selftest.json", encoding="utf-8"))
    assert rep["ok"] and os.path.exists(tmp_path / "selftest_window.png")
