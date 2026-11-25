"""Сжатие PDF со сканами (для отправки по почте).

Каждая картинка-скан уменьшается до нужного разрешения (dpi — с учётом того, каким размером она
показана на странице) и пересохраняется в JPEG с выбранным качеством. Картинка меняется, только если
стала заметно меньше. Не трогаются: прозрачные картинки (подписи, печати, вставленные программой),
чёрно-белые 1-битные сканы и маленькие значки. Текст, линии и всё нерастровое остаются как есть.
Исходный файл не изменяется: результат — новый файл.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Callable

import pymupdf
from PIL import Image

from .pdfutil import Cancelled, PdfError, human_error, open_pdf, safe_output_path, staged_pdf  # noqa: F401 — Cancelled для вызывающих


@dataclass(frozen=True)
class Level:
    title: str
    dpi: int
    quality: int
    hint: str


LEVELS = {
    "strong": Level("Сильное — для почты", 120, 50,
                    "Самый маленький файл. Обычный текст читается, мелкий шрифт может стать нечётким."),
    "medium": Level("Среднее", 150, 65, "Разумный размер, текст и подписи читаются хорошо."),
    "light": Level("Лёгкое", 200, 80, "Почти без потери качества, файл уменьшается меньше."),
}
DEFAULT_LEVEL = "medium"


def _image_usage(doc: pymupdf.Document) -> dict[int, tuple[int, float]]:
    """xref картинки → (страница, наибольшая площадь показа в кв. дюймах)."""
    use: dict[int, tuple[int, float]] = {}
    for pno, page in enumerate(doc):
        for info in page.get_image_info(xrefs=True):
            x = info.get("xref", 0)
            if not x:
                continue          # встроенные в поток страницы (inline) картинки не трогаем
            b = pymupdf.Rect(info["bbox"])
            area = abs(b.width * b.height) / (72 * 72)
            if x not in use or area > use[x][1]:
                use[x] = (use.get(x, (pno, 0))[0], area)
    return use


def _recode(doc: pymupdf.Document, xref: int, area_in2: float, level: Level):
    """(JPEG, ширина, высота, цветовое пространство) или None, если картинку лучше не трогать."""
    if doc.xref_get_key(xref, "SMask")[0] != "null" or doc.xref_get_key(xref, "Mask")[0] != "null":
        return None                                   # прозрачность (подписи, печати)
    if doc.xref_get_key(xref, "ImageMask")[1] == "true":
        return None
    bpc = doc.xref_get_key(xref, "BitsPerComponent")[1]
    if bpc == "1":
        return None                                   # 1-битный скан уже маленький, JPEG его раздует
    pix = pymupdf.Pixmap(doc, xref)
    if pix.width * pix.height < 200 * 200:
        return None
    if pix.alpha:
        return None
    if pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)       # CMYK и прочее — в RGB
    mode = "L" if pix.n == 1 else "RGB"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if area_in2 > 0:
        dpi = ((pix.width * pix.height) / area_in2) ** 0.5
        if dpi > level.dpi * 1.1:
            k = level.dpi / dpi
            img = img.resize((max(1, round(pix.width * k)), max(1, round(pix.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=level.quality, optimize=True)
    return buf.getvalue(), img.width, img.height, ("/DeviceGray" if mode == "L" else "/DeviceRGB")


def _write_jpeg(doc: pymupdf.Document, xref: int, data: bytes, w: int, h: int, cs: str) -> None:
    """Подменяет поток картинки прямо в её объекте (все страницы, где она показана, видят новую)."""
    doc.update_stream(xref, data, compress=False)
    for k, v in (("Filter", "/DCTDecode"), ("Width", str(w)), ("Height", str(h)), ("ColorSpace", cs),
                 ("BitsPerComponent", "8"), ("DecodeParms", "null"), ("Decode", "null"), ("Intent", "null")):
        doc.xref_set_key(xref, k, v)


def compress(path: str, out_path: str, level: str = DEFAULT_LEVEL,
             progress: Callable[[int, int], None] | None = None, *, only_if_smaller: bool = False) -> dict:
    """Сжимает PDF в новый файл. progress(i, n) может бросить Cancelled — тогда ничего не сохраняется.
    Возвращает {"before", "after", "images", "changed"} (размеры в байтах)."""
    lv = LEVELS[level]
    before = os.path.getsize(path)
    doc = open_pdf(path)
    try:
        use = _image_usage(doc)
        changed = 0
        items = sorted(use.items())
        for i, (xref, (pno, area)) in enumerate(items):
            if progress:
                progress(i, len(items))
            try:
                new = _recode(doc, xref, area, lv)
            except (RuntimeError, ValueError, OSError):
                new = None                             # необычная картинка — оставляем как есть
            if new is None:
                continue
            old_len = len(doc.xref_stream_raw(xref) or b"")
            if old_len and len(new[0]) < old_len * 0.9:
                _write_jpeg(doc, xref, *new)
                changed += 1
        if progress:
            progress(len(items), len(items))
        if not use:
            raise PdfError("В этом PDF нет сканов-картинок — сжимать нечего.")
        with staged_pdf(doc, out_path, inputs=(path,)) as tmp:
            after = os.path.getsize(tmp)
            saved = not only_if_smaller or after < before * 0.97
            if saved:
                os.replace(tmp, out_path)
    finally:
        doc.close()
    return {"before": before, "after": after, "saved": saved, "images": len(use), "changed": changed}


def compress_many(paths: list[str], level: str, progress=None) -> list[tuple[str, str, str]]:
    """Для каждого исходника возвращает путь, имя сохранённой копии и итог обработки."""
    results = []
    for index, path in enumerate(paths):
        def update(done, total):
            if progress:
                progress(index, len(paths))

        try:
            update(0, 1)
            out = safe_output_path(path, "сжатый")
            report = compress(path, out, level, update, only_if_smaller=True)
        except Cancelled:
            results.extend((p, "", "Остановлено") for p in paths[index:])
            break
        except (OSError, PdfError, RuntimeError, ValueError) as exc:
            results.append((path, "", human_error(exc)))
        else:
            if report["saved"]:
                sizes = f"{human_size(report['before'])} → {human_size(report['after'])}"
                results.append((path, out, sizes))
            else:
                results.append((path, "", "Уже хорошо сжат"))
    return results


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} МБ".replace(".", ",")
    return f"{max(1, round(n / 1024))} КБ"
