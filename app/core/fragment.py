"""«Вырезать и перенести»: любой фрагмент (подпись, печать, надпись) со страницы PDF A на страницу PDF B."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import cached_property
from typing import Optional

import numpy as np
import pymupdf
from PIL import Image

from . import retouch as rt
from .ink import extract_ink, to_png_bytes
from .pdfutil import NRect, insert_png, render_page, save_doc, open_pdf

CUT_DPI = 300


def cut_parts(doc: pymupdf.Document, page: int, rect: NRect, mode: str = "blue", sensitivity: float = 0.5,
              keep_colors: bool = True, dpi: int = CUT_DPI):
    """Всё, что нужно для вырезания и ручной доработки: (автоматический RGBA, исходный кусок RGB,
    рамка в пикселях страницы, размер страницы в пикселях)."""
    img = render_page(doc, page, dpi)
    h, w = img.shape[:2]
    box = rect.to_pixels(w, h)
    x0, y0, x1, y1 = box
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((1, 1, 4), np.uint8), np.full((1, 1, 3), 255, np.uint8), box, (w, h)
    if mode == "none":
        auto = np.dstack([crop, np.full(crop.shape[:2], 255, np.uint8)])
    else:
        auto = extract_ink(crop, uniform=not keep_colors, mode=mode, sensitivity=sensitivity)
    return auto, crop, box, (w, h)


def cut(doc: pymupdf.Document, page: int, rect: NRect, mode: str = "blue", sensitivity: float = 0.5,
        keep_colors: bool = True, dpi: int = CUT_DPI, retouch=()) -> np.ndarray:
    """RGBA фрагмента. mode: blue/black/color/any — фон убирается; none — вырезается как есть (непрозрачно).
    retouch — ручные правки (мазки «Ластик» / «Вернуть чернила»), накладываются поверх автоматики."""
    auto, crop, box, page_px = cut_parts(doc, page, rect, mode, sensitivity, keep_colors, dpi)
    if not retouch:
        return auto
    return rt.apply(auto, crop, retouch, box, page_px, mode, keep_colors)


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


def _extend_edge_colors(image: Image.Image) -> Image.Image:
    """Заполняет RGB под прозрачностью соседними цветами для интерполяции PDF."""
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    if alpha.getextrema()[0] > 0 or image.size == (1, 1) or not alpha.getbbox():
        return rgb
    smaller = image.resize(((image.width + 1) // 2, (image.height + 1) // 2), Image.Resampling.BOX)
    background = _extend_edge_colors(smaller).resize(image.size, Image.Resampling.BILINEAR)
    background.paste(rgb, mask=alpha.point(lambda value: 255 if value else 0))
    return background


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
    retouch: tuple = ()                       # ручные правки (tuple[retouch.Stroke, ...])
    _png: Optional[bytes] = field(default=None, repr=False, compare=False)

    def png(self) -> bytes:
        if self._png is None:
            with open_pdf(self.src_path) as d:
                rgba, _ = trim(cut(d, self.src_page, self.src_rect, self.mode, self.sensitivity, self.keep_colors,
                                     retouch=self.retouch))
            if self.opacity < 1:
                rgba = rgba.copy()
                rgba[..., 3] = (rgba[..., 3] * self.opacity).astype(np.uint8)
            self._png = to_png_bytes(rgba)
        return self._png

    @cached_property
    def pdf_image(self) -> tuple[bytes, bytes | None]:
        png = self.png()
        if not self.retouch:
            return png, None
        # При уменьшении PDF-просмотрщик фильтрует цвет и маску отдельно.
        # Чёрный RGB стёртых пикселей дал бы тёмную кайму вдоль мазка.
        with Image.open(io.BytesIO(png)) as image:
            return to_png_bytes(_extend_edge_colors(image)), to_png_bytes(image.getchannel("A"))


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


def ink_rect(src_rect: NRect, rgba_frac) -> NRect:
    """Где на исходной странице (в долях) лежат чернила обрезанного фрагмента."""
    fx0, fy0, fx1, fy1 = rgba_frac
    w, h = src_rect.x1 - src_rect.x0, src_rect.y1 - src_rect.y0
    return NRect(src_rect.x0 + fx0 * w, src_rect.y0 + fy0 * h, src_rect.x0 + fx1 * w, src_rect.y0 + fy1 * h)


def page_aspect(src_page_rect, dst_page_rect) -> float:
    """Множитель для follow_rect(aspect=…): переводит масштаб по ширине в масштаб по высоте."""
    return (src_page_rect.height * dst_page_rect.width) / (dst_page_rect.height * src_page_rect.width)


def follow_rect(old_dst: NRect, old_ink: NRect, new_ink: NRect, same_source: bool = True,
                aspect: float | None = None) -> NRect:
    """Новая рамка на странице «Куда», когда поменялись границы чернил (стёрли край ластиком, вернули штрих,
    сменили чувствительность, подвинули красную рамку). Масштаб (сколько места на «Куда» занимает доля
    исходной страницы) сохраняется — фрагмент не растягивается. Если это те же чернила (тот же источник и
    границы пересекаются) — оставшиеся штрихи остаются ровно на своём месте; иначе новый фрагмент встаёт
    в тот же левый верхний угол в том же масштабе.
    aspect — (высота «Откуда» × ширина «Куда») / (высота «Куда» × ширина «Откуда»): с ним масштаб берётся
    по ширине и одинаков по обеим осям (пропорции фрагмента всегда настоящие, как раньше)."""
    sx = (old_dst.x1 - old_dst.x0) / max(old_ink.x1 - old_ink.x0, 1e-9)
    if aspect is None:
        sy = (old_dst.y1 - old_dst.y0) / max(old_ink.y1 - old_ink.y0, 1e-9)
    else:
        sy = sx * aspect
    w, h = (new_ink.x1 - new_ink.x0) * sx, (new_ink.y1 - new_ink.y0) * sy
    overlap = (min(old_ink.x1, new_ink.x1) > max(old_ink.x0, new_ink.x0)
               and min(old_ink.y1, new_ink.y1) > max(old_ink.y0, new_ink.y0))
    if same_source and overlap:
        x0 = old_dst.x0 + (new_ink.x0 - old_ink.x0) * sx
        y0 = old_dst.y0 + (new_ink.y0 - old_ink.y0) * sy
    else:
        x0, y0 = old_dst.x0, old_dst.y0
    return NRect(x0, y0, x0 + w, y0 + h)


def apply(target_path: str, placements: list[Placement], out_path: Optional[str] = None) -> pymupdf.Document:
    """Ставит все фрагменты. С out_path — сохраняет (исходники не перезаписываются), иначе возвращает документ
    в памяти (для предпросмотра)."""
    doc = open_pdf(target_path)
    for pl in placements:
        page = doc[pl.dst_page]
        insert_png(page, pl.dst_rect.to_points(page.rect), *pl.pdf_image)
    if out_path:
        save_doc(doc, out_path, inputs=tuple({target_path, *[p.src_path for p in placements]}))
    return doc
