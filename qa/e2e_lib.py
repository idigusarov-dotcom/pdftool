"""Помощники для сквозных проверок (tests/test_e2e_*.py): окно программы без экрана, ожидание фоновых задач,
проверка СОДЕРЖИМОГО результата (есть ли чернила в нужном месте, чья это подпись, сколько страниц, порядок)."""
from __future__ import annotations

import os
import time

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

from app.core.pdfutil import NRect

SHOTS = os.environ.get("PDFTOOL_E2E_SHOTS", "")       # папка для снимков экрана (необязательно)


def pump(app, sec=0.2):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, cond, timeout=120.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def shot(w, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        w.grab().save(os.path.join(SHOTS, name + ".png"))


def render(path_or_doc, page=0, dpi=72) -> np.ndarray:
    doc = pymupdf.open(path_or_doc) if isinstance(path_or_doc, str) else path_or_doc
    try:
        pm = doc[page].get_pixmap(dpi=dpi, alpha=False)
        return np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3).copy()
    finally:
        if isinstance(path_or_doc, str):
            doc.close()


def blue_mask(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.int16)
    return (f[..., 2] - f[..., 0] > 45) & (f[..., 2] > 90)


def dark_mask(img: np.ndarray) -> np.ndarray:
    return img.astype(np.int16).max(-1) < 110


def crop(img: np.ndarray, r) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = (r.x0, r.y0, r.x1, r.y1) if isinstance(r, NRect) else r
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def ink_in(path, page, rect, kind="blue", dpi=72) -> int:
    """Сколько пикселей чернил в рамке (доли страницы)."""
    c = crop(render(path, page, dpi), rect)
    return int((blue_mask(c) if kind == "blue" else dark_mask(c)).sum())


def _norm_mask(m: np.ndarray, size=(160, 40)) -> np.ndarray | None:
    ys, xs = np.nonzero(m)
    if len(xs) < 15:
        return None
    # рамка по 1…99% пикселей: одиночные точки-соринки не растягивают её
    y0, y1 = np.percentile(ys, [1, 99]).astype(int)
    x0, x1 = np.percentile(xs, [1, 99]).astype(int)
    m = m[y0:y1 + 1, x0:x1 + 1]
    im = Image.fromarray((m * 255).astype(np.uint8)).resize(size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.5))
    a = np.asarray(im, np.float32) / 255
    return (a - a.mean()) / (a.std() + 1e-6)


def which_act(out_pdf, rec_page, dst_rect, acts_pdf, act_pages, src_rect, kind="blue", dpi=110, refs=None, base_pdf=None):
    """Чья подпись стоит в расписке: сравнивает форму чернил в синей рамке с подписями в актах.
    Возвращает (номер страницы акта или None, похожесть 0..1)."""
    fn = blue_mask if kind == "blue" else dark_mask
    om = fn(crop(render(out_pdf, rec_page, dpi), dst_rect))
    if base_pdf is not None:          # только то, что добавила программа (без печати самой расписки)
        bm = fn(crop(render(base_pdf, rec_page, dpi), dst_rect))
        om = om & ~bm
    out = _norm_mask(om)
    if out is None:
        return None, 0.0
    best, score = None, -1.0
    for a in act_pages:
        m = refs[a] if refs is not None else \
            fn(crop(render(acts_pdf, a, dpi), src_rect))
        ref = _norm_mask(m)
        if ref is None:
            continue
        s = float((out * ref).mean())
        if s > score:
            best, score = a, s
    return best, score


def page_count(path) -> int:
    with pymupdf.open(path) as d:
        return len(d)
