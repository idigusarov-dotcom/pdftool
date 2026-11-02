"""Вставка картинки (печать, подпись, логотип) на страницы PDF."""
from __future__ import annotations

from PIL import Image
import pymupdf

from .ink import remove_white_background, to_png_bytes
from .pdfutil import NRect, insert_png, save_doc


def load_image(path: str, remove_white: bool = False) -> Image.Image:
    img = Image.open(path)
    img.load()
    img = img.convert("RGBA")
    return remove_white_background(img) if remove_white else img


def insert_image(pdf_path: str, img: Image.Image, rect: NRect, pages: list[int], out_path: str) -> int:
    doc = pymupdf.open(pdf_path)
    data = to_png_bytes(img)
    for i in pages:
        page = doc[i]
        insert_png(page, rect.to_points(page.rect), data)
    save_doc(doc, out_path, inputs=(pdf_path,))
    doc.close()
    return len(pages)
