"""«Вырезать и перенести»: любой фрагмент (подпись, печать, надпись) со страницы PDF A на страницу PDF B."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pymupdf

from .ink import extract_ink, to_png_bytes
from .pdfutil import NRect, insert_png, render_page, save_doc

CUT_DPI = 300


def cut(doc: pymupdf.Document, page: int, rect: NRect, mode: str = "blue", sensitivity: float = 0.5,
        keep_colors: bool = True, dpi: int = CUT_DPI) -> np.ndarray:
    """RGBA фрагмента. mode: blue/black/color/any — фон убирается; none — вырезается как есть (непрозрачно)."""
    img = render_page(doc, page, dpi)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rect.to_pixels(w, h)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((1, 1, 4), np.uint8)
    if mode == "none":
        return np.dstack([crop, np.full(crop.shape[:2], 255, np.uint8)])
    return extract_ink(crop, uniform=not keep_colors, mode=mode, sensitivity=sensitivity)


def trim(rgba: np.ndarray, pad: int = 6) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Обрезает прозрачные поля. Возвращает картинку и доли (x0, y0, x1, y1) от исходной."""
    m = rgba[..., 3] > 30
    if not m.any():
        return rgba, (0.0, 0.0, 1.0, 1.0)
    ys, xs = np.where(m)
    h, w = m.shape
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    return rgba[y0:y1, x0:x1], (x0 / w, y0 / h, x1 / w, y1 / h)


@dataclass
class Placement:
    """Один перенос: откуда (файл, страница, рамка) и куда (страница, рамка) в целевом файле."""
    src_path: str
    src_page: int
    src_rect: NRect
    dst_page: int
    dst_rect: NRect
    mode: str = "blue"
    sensitivity: float = 0.5
    keep_colors: bool = True
    opacity: float = 1.0
    _png: Optional[bytes] = field(default=None, repr=False, compare=False)

    def png(self) -> bytes:
        if self._png is None:
            with pymupdf.open(self.src_path) as d:
                rgba, _ = trim(cut(d, self.src_page, self.src_rect, self.mode, self.sensitivity, self.keep_colors))
            if self.opacity < 1:
                rgba = rgba.copy()
                rgba[..., 3] = (rgba[..., 3] * self.opacity).astype(np.uint8)
            self._png = to_png_bytes(rgba)
        return self._png


def natural_rect(src_doc: pymupdf.Document, src_page: int, src_rect: NRect, rgba_frac, dst_page_rect,
                 at: tuple[float, float] = (0.35, 0.4)) -> NRect:
    """Рамка на целевой странице в натуральную величину (как на исходной), левый верхний угол в at."""
    sp = src_doc[src_page].rect
    fx0, fy0, fx1, fy1 = rgba_frac
    w_pt = (src_rect.x1 - src_rect.x0) * sp.width * (fx1 - fx0)
    h_pt = (src_rect.y1 - src_rect.y0) * sp.height * (fy1 - fy0)
    w, h = w_pt / dst_page_rect.width, h_pt / dst_page_rect.height
    x0, y0 = min(at[0], 1 - w), min(at[1], 1 - h)
    return NRect(max(0, x0), max(0, y0), max(0, x0) + w, max(0, y0) + h)


def apply(target_path: str, placements: list[Placement], out_path: Optional[str] = None) -> pymupdf.Document:
    """Ставит все фрагменты. С out_path — сохраняет (исходники не перезаписываются), иначе возвращает документ
    в памяти (для предпросмотра)."""
    doc = pymupdf.open(target_path)
    for pl in placements:
        page = doc[pl.dst_page]
        insert_png(page, pl.dst_rect.to_points(page.rect), pl.png())
    if out_path:
        save_doc(doc, out_path, inputs=tuple({target_path, *[p.src_path for p in placements]}))
    return doc
