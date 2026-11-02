"""Общие функции работы с PDF (PyMuPDF)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pymupdf


@dataclass
class NRect:
    """Прямоугольник в долях страницы (0..1), не зависит от DPI."""
    x0: float
    y0: float
    x1: float
    y1: float

    def normalized(self) -> "NRect":
        return NRect(min(self.x0, self.x1), min(self.y0, self.y1),
                     max(self.x0, self.x1), max(self.y0, self.y1))

    def to_points(self, page_rect: pymupdf.Rect) -> pymupdf.Rect:
        r = self.normalized()
        return pymupdf.Rect(page_rect.x0 + r.x0 * page_rect.width, page_rect.y0 + r.y0 * page_rect.height,
                            page_rect.x0 + r.x1 * page_rect.width, page_rect.y0 + r.y1 * page_rect.height)

    def to_pixels(self, w: int, h: int) -> tuple[int, int, int, int]:
        r = self.normalized()
        return (max(0, int(round(r.x0 * w))), max(0, int(round(r.y0 * h))),
                min(w, int(round(r.x1 * w))), min(h, int(round(r.y1 * h))))

    @staticmethod
    def from_points(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> "NRect":
        return NRect((rect.x0 - page_rect.x0) / page_rect.width, (rect.y0 - page_rect.y0) / page_rect.height,
                     (rect.x1 - page_rect.x0) / page_rect.width, (rect.y1 - page_rect.y0) / page_rect.height)


def open_pdf(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


def render_page(doc: pymupdf.Document, index: int, dpi: int = 150) -> np.ndarray:
    """Растр страницы как RGB-массив (H, W, 3) uint8."""
    pix = doc[index].get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[..., :3].copy()


def safe_output_path(input_path: str, suffix: str, ext: str = ".pdf") -> str:
    """Новое имя файла рядом с исходным, никогда не перезаписывает существующий."""
    base, _ = os.path.splitext(input_path)
    cand = f"{base}_{suffix}{ext}"
    n = 2
    while os.path.exists(cand):
        cand = f"{base}_{suffix}_{n}{ext}"
        n += 1
    return cand


def save_doc(doc: pymupdf.Document, out_path: str, inputs: list[str] | tuple[str, ...] = ()) -> str:
    """Сохраняет документ; запрещает запись поверх входных файлов."""
    absout = os.path.abspath(out_path)
    for p in inputs:
        if p and os.path.abspath(p) == absout:
            raise ValueError("Нельзя сохранять поверх исходного файла. Выберите другое имя.")
    doc.save(out_path, garbage=3, deflate=True)
    return out_path


def insert_png(page: pymupdf.Page, display_rect: pymupdf.Rect, png: bytes) -> None:
    """Вставляет картинку так, как её видно на экране, с учётом /Rotate страницы.
    display_rect — в координатах отображаемой (повёрнутой) страницы."""
    rot = page.rotation % 360
    target = display_rect * page.derotation_matrix if rot else display_rect
    page.insert_image(target, stream=png, keep_proportion=False, overlay=True, rotate=rot)
