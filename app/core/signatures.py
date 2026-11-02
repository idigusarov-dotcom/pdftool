"""Перенос подписей и расшифровок из актов в расписки."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pymupdf

from .ink import extract_ink, has_ink, to_png_bytes
from .pdfutil import NRect, render_page, save_doc

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
    uniform_color: bool = True
    trim_left: bool = True   # прижать подпись к левому краю целевой области


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
    page.insert_image(full, stream=to_png_bytes(rgba), keep_proportion=False, overlay=True)


def guess_mapping(n_receipts: int, n_acts: int) -> list[Optional[int]]:
    """Без OCR: по порядку. Пользователь исправляет в таблице."""
    return [i if i < n_acts else None for i in range(n_receipts)]


def cut_signature(acts: pymupdf.Document, act_index: int, rect: NRect, dpi: int = 200,
                  uniform_color: bool = True) -> np.ndarray:
    img = render_page(acts, act_index, dpi)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rect.to_pixels(w, h)
    return extract_ink(img[y0:y1, x0:x1], uniform=uniform_color)


def fit_rect(src_w: float, src_h: float, target: pymupdf.Rect) -> pymupdf.Rect:
    """Вписывает картинку с сохранением пропорций: по левому краю, по центру по вертикали."""
    s = min(target.width / src_w, target.height / src_h)
    w, h = src_w * s, src_h * s
    y0 = target.y0 + (target.height - h) / 2
    return pymupdf.Rect(target.x0, y0, target.x0 + w, y0 + h)


def apply_transfer(job: TransferJob, out_path: str,
                   progress: Callable[[int, int], None] | None = None) -> dict:
    """Создаёт новый PDF расписок с подписями. Возвращает отчёт."""
    acts = pymupdf.open(job.acts_path)
    rec = pymupdf.open(job.receipts_path)
    report = {"placed": [], "blank": [], "empty_ink": []}
    for i, page in enumerate(rec):
        if progress:
            progress(i, len(rec))
        a = job.mapping[i] if i < len(job.mapping) else None
        if a is None:
            report["blank"].append(i)
            continue
        src = job.src_overrides.get(a, job.src_rect)
        rgba = cut_signature(acts, a, src, job.dpi, job.uniform_color)
        if not has_ink(rgba):
            report["empty_ink"].append(i)
            continue
        dst = job.dst_overrides.get(i, job.dst_rect).to_points(page.rect)
        _place(page, rgba, src.to_points(acts[a].rect), dst, job.trim_left)
        report["placed"].append((i, a))
    save_doc(rec, out_path, inputs=(job.acts_path, job.receipts_path))
    rec.close()
    acts.close()
    return report


def preview_page(job: TransferJob, receipt_index: int, dpi: int = 100) -> np.ndarray:
    """Растр одной расписки с подписью (без сохранения файла)."""
    acts = pymupdf.open(job.acts_path)
    rec = pymupdf.open(job.receipts_path)
    page = rec[receipt_index]
    a = job.mapping[receipt_index] if receipt_index < len(job.mapping) else None
    if a is not None:
        src = job.src_overrides.get(a, job.src_rect)
        rgba = cut_signature(acts, a, src, job.dpi, job.uniform_color)
        if has_ink(rgba):
            dst = job.dst_overrides.get(receipt_index, job.dst_rect).to_points(page.rect)
            _place(page, rgba, src.to_points(acts[a].rect), dst, job.trim_left)
    img = render_page(rec, receipt_index, dpi)
    rec.close()
    acts.close()
    return img
