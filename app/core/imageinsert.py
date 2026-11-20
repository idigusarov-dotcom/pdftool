"""Вставка картинки (печать, подпись, логотип) на страницы PDF."""
from __future__ import annotations

import os

from PIL import Image, ImageOps

from .ink import remove_white_background, to_png_bytes
from .pdfutil import NRect, PdfError, insert_png, open_pdf, save_doc


MAX_SIDE = 3000   # px: для печати/подписи на листе A4 этого с запасом (≈ 350 dpi на всю ширину)


def load_image(path: str, remove_white: bool = False) -> Image.Image:
    """PNG/JPG → RGBA. Фото с телефона поворачиваются по EXIF, слишком большие — уменьшаются
    (иначе уходят гигабайты памяти, а PDF раздувается)."""
    name = os.path.basename(path) or path
    if not os.path.isfile(path):
        raise PdfError(f"Картинка «{name}» не найдена.")
    try:
        with Image.open(path) as im:
            im.draft("RGB", (MAX_SIDE, MAX_SIDE))   # JPEG: сразу читаем уменьшенную копию
            im.load()
            img = ImageOps.exif_transpose(im)
    except Image.DecompressionBombError as e:
        raise PdfError(f"Картинка «{name}» слишком большая. Уменьшите её и попробуйте снова.") from e
    except (OSError, ValueError, SyntaxError) as e:
        raise PdfError(f"Не удалось открыть картинку «{name}»: файл повреждён или это не PNG/JPG.") from e
    if max(img.size) > MAX_SIDE:
        img = img.copy()
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    img = img.convert("RGBA")
    return remove_white_background(img) if remove_white else img


def insert_image(pdf_path: str, img: Image.Image, rect: NRect, pages: list[int], out_path: str) -> int:
    doc = open_pdf(pdf_path)
    try:
        data = to_png_bytes(img)
        for i in pages:
            page = doc[i]
            insert_png(page, rect.to_points(page.rect), data)
        save_doc(doc, out_path, inputs=(pdf_path,))
    finally:
        doc.close()
    return len(pages)
