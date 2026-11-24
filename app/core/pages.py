"""Операции со страницами: объединение, разделение, поворот, удаление, порядок."""
from __future__ import annotations

import os
import re

import pymupdf

from .pdfutil import PdfError, save_doc, open_pdf


def _insert(out: pymupdf.Document, src: pymupdf.Document, name: str, **kw) -> None:
    """insert_pdf с понятной ошибкой, если страницы файла не читаются (PDF повреждён)."""
    try:
        out.insert_pdf(src, **kw)
    except Exception as e:  # noqa: BLE001
        raise PdfError(f"Не удалось взять страницы из «{name}»: PDF повреждён. Пересохраните файл "
                       "(например, «Печать → PDF») и попробуйте снова.") from e


def _copy_pages(out, src, name, order):
    """Копируем диапазон целиком, чтобы MuPDF перенёс ссылки между страницами."""
    if not order or min(order) < 0 or max(order) >= len(src):
        raise PdfError("Выберите страницы в пределах документа.")
    first, last = min(order), max(order)
    _insert(out, src, name, from_page=first, to_page=last)
    if order != list(range(first, last + 1)):
        out.select([i - first for i in order])


def merge(paths: list[str], out_path: str, progress=None) -> int:
    """progress(i, n) — по файлам; может бросить Cancelled (тогда ничего не сохраняется)."""
    out = pymupdf.open()
    try:
        for k, p in enumerate(paths):
            if progress:
                progress(k, len(paths))
            with open_pdf(p) as d:
                _insert(out, d, os.path.basename(p))
        n = len(out)
        if progress:
            progress(len(paths), len(paths))
        save_doc(out, out_path, inputs=paths)
    finally:
        out.close()
    return n


def parse_ranges(text: str, n_pages: int) -> list[list[int]]:
    """«1-3, 5, 7-9» → [[0,1,2],[4],[6,7,8]] (каждая группа — отдельный файл)."""
    groups = []
    for part in re.split(r"[;,]", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–—−]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"Не понимаю диапазон: «{part}»")
        if not (1 <= a <= n_pages and 1 <= b <= n_pages):
            raise ValueError(f"Страницы {part} вне документа (всего {n_pages})")
        step = 1 if b >= a else -1
        groups.append(list(range(a - 1, b - 1 + step, step)))
    if not groups:
        raise ValueError("Не указаны страницы")
    return groups


def split(path: str, groups: list[list[int]], out_dir: str, progress=None) -> list[str]:
    src = open_pdf(path)
    base = os.path.splitext(os.path.basename(path))[0]
    outs = []
    try:
        for k, g in enumerate(groups):
            if progress:
                progress(k, len(groups))
            out = pymupdf.open()
            try:
                _copy_pages(out, src, os.path.basename(path), g)
                name = f"{base}_стр{g[0] + 1}" + (f"-{g[-1] + 1}" if len(g) > 1 else "") + ".pdf"
                op = os.path.join(out_dir, name)
                k2 = 2
                while os.path.exists(op):
                    op = os.path.join(out_dir, name[:-4] + f"_{k2}.pdf")
                    k2 += 1
                save_doc(out, op, inputs=(path,))
            finally:
                out.close()
            outs.append(op)
    finally:
        src.close()
    return outs


def split_every_page(path: str, out_dir: str, progress=None) -> list[str]:
    with open_pdf(path) as d:
        n = len(d)
    return split(path, [[i] for i in range(n)], out_dir, progress=progress)


def rebuild(path: str, order: list[int], rotations: dict[int, int], out_path: str, progress=None) -> int:
    """Новый файл: страницы в порядке `order` (удалённые просто не указаны), с поворотами."""
    with open_pdf(path) as src, pymupdf.open() as doc:
        if progress:
            progress(0, len(order))
        _copy_pages(doc, src, os.path.basename(path), order)
        for k, i in enumerate(order):
            if progress and k % 10 == 0:
                progress(k, len(order))
            rot = rotations.get(i, 0) % 360
            if rot:
                p = doc[k]
                p.set_rotation((p.rotation + rot) % 360)
        if progress:
            progress(len(order), len(order))
        save_doc(doc, out_path, inputs=(path,))
        return len(doc)
