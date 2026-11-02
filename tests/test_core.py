"""Тесты ядра на реальных файлах (пути можно переопределить переменной PDFTOOL_DATA)."""
import os
import sys
import tempfile

import numpy as np
import pymupdf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core import pages, signatures, sorting, imageinsert  # noqa: E402
from app.core.ink import blue_ink_alpha  # noqa: E402
from app.core.pdfutil import NRect, render_page  # noqa: E402

DATA = os.environ.get("PDFTOOL_DATA", "/workspace")
ACTS = f"{DATA}/job2/fileB_3.5MB.pdf"
RECEIPTS = f"{DATA}/job2/fileA_3.0MB.pdf"
JOB2_RESULT = f"{DATA}/job2/receipts_with_signatures.pdf"
JOB3 = f"{DATA}/job3/input.pdf"
need = pytest.mark.skipif(not os.path.exists(ACTS), reason="нет тестовых файлов")

# Известное соответствие job2: расписка -> акт (1-based), None = пусто (отказ)
JOB2_MAP = [4, 5, 8, 7, None, 1, 3]
# Области подписи в актах job2 (пиксели при 200 dpi, лист 1654x2339): x0, x1, линия, вверх, вниз
JOB2_SRC = {1: (110, 1110, 1831, 125, 22), 3: (262, 1010, 1930, 104, 38), 4: (230, 1160, 1925, 125, 38),
            5: (230, 1160, 1928, 125, 38), 7: (230, 1060, 1929, 125, 38), 8: (230, 1060, 1917, 100, 38)}
W, H = 1654, 2339
LINE_PT, X_PT, SCALE = 535.5, 226.8, 0.8  # линия «Собственник» в расписке, левый край, масштаб


def job2_job():
    m = [None if a is None else a - 1 for a in JOB2_MAP]
    job = signatures.TransferJob(ACTS, RECEIPTS, m)
    rec = pymupdf.open(RECEIPTS)
    for r, a in enumerate(JOB2_MAP):
        if a is None:
            continue
        x0, x1, ln, up, dn = JOB2_SRC[a]
        job.src_overrides[a - 1] = NRect(x0 / W, (ln - up) / H, x1 / W, (ln + dn) / H)
        pr = rec[r].rect
        k = 72 / 200 * SCALE
        top = LINE_PT - up * k
        job.dst_overrides[r] = NRect.from_points(
            pymupdf.Rect(X_PT, top, X_PT + (x1 - x0) * k, top + (up + dn) * k), pr)
    return job


_BASE = {}


def ink_bbox(doc, i, band=(440, 600)):
    """Рамка ДОБАВЛЕННЫХ чернил (сравнение с исходной распиской), 1 px = 1 pt."""
    if i not in _BASE:
        _BASE[i] = blue_ink_alpha(render_page(pymupdf.open(RECEIPTS), i, 72)[band[0]:band[1]]) > 0.3
    img = render_page(doc, i, 72)
    a = (blue_ink_alpha(img[band[0]:band[1]]) > 0.5) & ~_BASE[i]
    ys, xs = np.where(a)
    if len(xs) < 20:
        return None
    return (xs.min(), ys.min() + band[0], xs.max(), ys.max() + band[0])


@need
def test_signature_transfer_reproduces_job2():
    job = job2_job()
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.pdf")
        rep = signatures.apply_transfer(job, out)
        assert rep["blank"] == [4]
        assert len(rep["placed"]) == 6
        mine, ref = pymupdf.open(out), pymupdf.open(JOB2_RESULT)
        assert len(mine) == len(ref) == 7
        for i in range(7):
            a, b = ink_bbox(mine, i), ink_bbox(ref, i)
            if JOB2_MAP[i] is None:
                assert a is None and b is None
                continue
            assert a is not None and b is not None
            assert all(abs(p - q) <= 8 for p, q in zip(a, b)), (i, a, b)
        # исходные файлы не изменились
        assert os.path.getsize(RECEIPTS) > 0


@need
def test_guess_mapping_and_preview():
    assert signatures.guess_mapping(3, 2) == [0, 1, None]
    img = signatures.preview_page(job2_job(), 0, dpi=50)
    assert img.ndim == 3


def test_sorting_logic():
    vals = ["12", "3", None, "12", "3а", "100"]
    assert sorting.duplicates(vals) == {"12"}
    assert [vals[i] for i in sorting.sorted_order(vals)] == ["3", "3а", "12", "12", "100", None]


@pytest.mark.skipif(not os.path.exists(JOB3), reason="нет job3")
def test_sorting_job3():
    import csv
    rows = list(csv.DictReader(open(f"{DATA}/job3/inventory.csv", encoding="utf-8")))
    src = pymupdf.open(JOB3)
    vals = [None] * len(src)
    for r in rows:
        p = int(r.get("page") or 0)
        v = (r.get("apt") or r.get("apartment") or "").split()[0] if (r.get("apt") or r.get("apartment")) else ""
        if 1 <= p <= len(src):
            vals[p - 1] = "".join(ch for ch in v if ch.isdigit()) or None
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "s.pdf")
        order = sorting.export_sorted(JOB3, vals, out)
        assert len(pymupdf.open(out)) == len(src)
        nums = [int(vals[i]) for i in order if vals[i]]
        assert nums == sorted(nums)
        sorting.export_csv(vals, os.path.join(td, "s.csv"))
    # у сканов нет текстового слоя — угадывание просто возвращает None, без ошибок
    assert len(sorting.guess_all(src)) == len(src)


@need
def test_page_ops():
    with tempfile.TemporaryDirectory() as td:
        m = os.path.join(td, "m.pdf")
        assert pages.merge([ACTS, RECEIPTS], m) == 15
        g = pages.parse_ranges("1-3, 5, 9-7", 15)
        assert g == [[0, 1, 2], [4], [8, 7, 6]]
        outs = pages.split(m, g, td)
        assert [len(pymupdf.open(o)) for o in outs] == [3, 1, 3]
        assert len(pages.split_every_page(RECEIPTS, td)) == 7
        r = os.path.join(td, "r.pdf")
        assert pages.rebuild(m, [2, 0, 1], {0: 90}, r) == 3
        d = pymupdf.open(r)
        assert d[1].rotation == 90 and d[0].rotation == 0
        with pytest.raises(ValueError):
            pages.parse_ranges("1-99", 15)
        with pytest.raises(ValueError):
            pages.rebuild(m, [0], {}, m)  # нельзя поверх исходного


@need
def test_image_insert():
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "s.png")
        im = Image.new("RGB", (200, 100), "white")
        im.paste((20, 40, 200), (50, 30, 150, 70))
        im.save(png)
        img = imageinsert.load_image(png, remove_white=True)
        a = np.array(img)[..., 3]
        assert a[0, 0] == 0 and a[50, 100] == 255
        out = os.path.join(td, "i.pdf")
        imageinsert.insert_image(RECEIPTS, img, NRect(0.1, 0.1, 0.4, 0.2), [0, 2], out)
        d = pymupdf.open(out)
        assert len(d[0].get_images()) == len(pymupdf.open(RECEIPTS)[0].get_images()) + 1
