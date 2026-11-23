"""Перенос подписей и расшифровок из актов в расписки."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pymupdf

from .ink import extract_ink, has_ink, to_png_bytes
from .pdfutil import NRect, PdfError, check_output_path, insert_png, open_pdf, render_page, save_doc

# Значения по умолчанию подобраны по реальным актам/распискам (Кржижановского 5/1)
DEFAULT_SRC_RECT = NRect(0.139, 0.770, 0.701, 0.840)   # блок «В чем и расписываемся» в акте
DEFAULT_DST_RECT = NRect(0.381, 0.593, 0.831, 0.650)   # строка «Собственник кв. №» в расписке


@dataclass
class TransferJob:
    acts_path: str
    receipts_path: str
    # mapping[i] = номер страницы акта (0-based) для расписки i, или None — оставить пустой
    mapping: list[Optional[int]]
    src_rect: NRect = field(default_factory=lambda: DEFAULT_SRC_RECT)
    dst_rect: NRect = field(default_factory=lambda: DEFAULT_DST_RECT)
    src_overrides: dict[int, NRect] = field(default_factory=dict)   # по странице акта
    dst_overrides: dict[int, NRect] = field(default_factory=dict)   # по странице расписки
    dpi: int = 200
    uniform_color: bool = True  # единый синий/чёрный; в экране «Подписи» галочка «Сохранить настоящий цвет» снимает это
    trim_left: bool = True   # прижать подпись к левому краю целевой области
    ink_mode: str = "blue"   # blue / black / any
    sensitivity: float = 0.5
    remove_form: bool = True  # чёрная ручка: убирать печатный текст бланка (общий у всех актов)
    _form: dict = field(default_factory=dict, repr=False, compare=False)


def trim_x(rgba: np.ndarray, pad: int = 4) -> tuple[np.ndarray, float, float]:
    """Обрезает пустые поля слева/справа. Возвращает (картинка, доля_слева, доля_ширины)."""
    cols = np.where((rgba[..., 3] > 38).any(0))[0]
    w = rgba.shape[1]
    if cols.size == 0:
        return rgba, 0.0, 1.0
    l, r = max(0, cols[0] - pad), min(w, cols[-1] + pad + 1)
    return rgba[:, l:r], l / w, (r - l) / w


def _place(page: pymupdf.Page, rgba: np.ndarray, src_pts: pymupdf.Rect, dst: pymupdf.Rect, trim: bool) -> None:
    """Масштаб берётся по всей исходной области; при trim подпись прижимается влево."""
    full = fit_rect(src_pts.width, src_pts.height, dst)
    if trim:
        rgba, _, frac_w = trim_x(rgba)
        full = pymupdf.Rect(full.x0, full.y0, full.x0 + full.width * frac_w, full.y1)
    insert_png(page, full, to_png_bytes(rgba))


def guess_mapping(n_receipts: int, n_acts: int) -> list[Optional[int]]:
    """Без OCR: по порядку. Пользователь исправляет в таблице."""
    return [i if i < n_acts else None for i in range(n_receipts)]


def _crop(acts: pymupdf.Document, act_index: int, rect: NRect, dpi: int) -> np.ndarray:
    img = render_page(acts, act_index, dpi)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rect.to_pixels(w, h)
    return img[y0:y1, x0:x1]


def cut_signature(acts: pymupdf.Document, act_index: int, rect: NRect, dpi: int = 200,
                  uniform_color: bool = True, mode: str = "blue", sensitivity: float = 0.5,
                  exclude: np.ndarray | None = None) -> np.ndarray:
    return extract_ink(_crop(acts, act_index, rect, dpi), uniform=uniform_color, mode=mode,
                       sensitivity=sensitivity, exclude=exclude)


FORM_PAD = 0.03   # бланк ищем в рамке с запасом: печать, обрезанная краем рамки, иначе не совмещается


def _padded(acts: pymupdf.Document, a: int, rect: NRect, dpi: int):
    """(кусок с запасом, срез рамки внутри него, сама рамка)."""
    img = render_page(acts, a, dpi)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rect.to_pixels(w, h)
    p = NRect(max(0.0, rect.x0 - FORM_PAD), max(0.0, rect.y0 - FORM_PAD),
              min(1.0, rect.x1 + FORM_PAD), min(1.0, rect.y1 + FORM_PAD))
    px0, py0, px1, py1 = p.to_pixels(w, h)
    px0, py0, px1, py1 = min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1)
    return img[py0:py1, px0:px1], (slice(y0 - py0, y1 - py0), slice(x0 - px0, x1 - px0)), img[y0:y1, x0:x1]


def _form_ref(job: "TransferJob", acts: pymupdf.Document):
    """Бланк по актам задачи (до 12 страниц с общей рамкой); считается один раз на задачу."""
    from . import formmask
    key = (job.src_rect, job.dpi, len(acts))
    if job._form.get("key") != key:
        pages = sorted({m for m in job.mapping if m is not None and 0 <= m < len(acts)
                        and m not in job.src_overrides}) or list(range(len(acts)))
        if len(pages) > formmask.MAX_PAGES:
            pages = [pages[int(i)] for i in np.linspace(0, len(pages) - 1, formmask.MAX_PAGES)]
        job._form = {"key": key,
                     "ref": formmask.FormRef([_padded(acts, p, job.src_rect, job.dpi)[0] for p in pages])}
    return job._form["ref"]


def _cut_dpi(job: "TransferJob", acts: pymupdf.Document, a: int, src: NRect, dpi: int) -> np.ndarray:
    exclude = None
    if job.remove_form and job.ink_mode in ("black", "any") and src == job.src_rect and len(acts) >= 3:
        ref = _form_ref(job, acts)
        if ref.ok:
            big, inner, crop = _padded(acts, a, src, dpi)
            m = ref.mask(big)
            exclude = None if m is None else m[inner]
    if exclude is None:
        crop = _crop(acts, a, src, dpi)
    return extract_ink(crop, uniform=job.uniform_color, mode=job.ink_mode, sensitivity=job.sensitivity,
                       exclude=exclude)


def preview_cut(job: "TransferJob", acts: pymupdf.Document, a: int, src: NRect, dpi: int = 110) -> np.ndarray:
    return _cut_dpi(job, acts, a, src, dpi)


def _cut(job: "TransferJob", acts: pymupdf.Document, a: int, src: NRect) -> np.ndarray:
    return _cut_dpi(job, acts, a, src, job.dpi)

def fit_rect(src_w: float, src_h: float, target: pymupdf.Rect) -> pymupdf.Rect:
    """Вписывает картинку с сохранением пропорций: по левому краю, по центру по вертикали."""
    s = min(target.width / src_w, target.height / src_h)
    w, h = src_w * s, src_h * s
    y0 = target.y0 + (target.height - h) / 2
    return pymupdf.Rect(target.x0, y0, target.x0 + w, y0 + h)


def _act_index(job: TransferJob, i: int, n_acts: int) -> Optional[int]:
    a = job.mapping[i] if i < len(job.mapping) else None
    if a is not None and not (0 <= a < n_acts):
        raise PdfError(f"Расписка стр. {i + 1}: в файле актов нет страницы {a + 1} (в нём {n_acts} стр.). "
                       "Проверьте пары страниц.")
    return a


def apply_transfer(job: TransferJob, out_path: str,
                   progress: Callable[[int, int], None] | None = None) -> dict:
    """Создаёт новый PDF расписок с подписями. Возвращает отчёт."""
    check_output_path(out_path, (job.acts_path, job.receipts_path))
    acts = open_pdf(job.acts_path)
    rec = open_pdf(job.receipts_path)
    try:
        report = {"placed": [], "blank": [], "empty_ink": []}
        for i, page in enumerate(rec):
            if progress:
                progress(i, len(rec))
            a = _act_index(job, i, len(acts))
            if a is None:
                report["blank"].append(i)
                continue
            src = job.src_overrides.get(a, job.src_rect)
            rgba = _cut(job, acts, a, src)
            if not has_ink(rgba):
                report["empty_ink"].append(i)
                continue
            dst = job.dst_overrides.get(i, job.dst_rect).to_points(page.rect)
            _place(page, rgba, src.to_points(acts[a].rect), dst, job.trim_left)
            report["placed"].append((i, a))
        if progress:
            progress(len(rec), len(rec))
        save_doc(rec, out_path, inputs=(job.acts_path, job.receipts_path))
    finally:
        rec.close()
        acts.close()
    return report


def preview_page(job: TransferJob, receipt_index: int, dpi: int = 100) -> np.ndarray:
    """Растр одной расписки с подписью (без сохранения файла)."""
    acts = open_pdf(job.acts_path)
    rec = open_pdf(job.receipts_path)
    try:
        page = rec[receipt_index]
        a = _act_index(job, receipt_index, len(acts))
        if a is not None:
            src = job.src_overrides.get(a, job.src_rect)
            rgba = _cut(job, acts, a, src)
            if has_ink(rgba):
                dst = job.dst_overrides.get(receipt_index, job.dst_rect).to_points(page.rect)
                _place(page, rgba, src.to_points(acts[a].rect), dst, job.trim_left)
        return render_page(rec, receipt_index, dpi)
    finally:
        rec.close()
        acts.close()
