"""Операции со страницами: объединение, разделение, поворот, удаление, порядок."""
from __future__ import annotations

import os
import re

import pymupdf

from .pdfutil import save_doc, open_pdf


def merge(paths: list[str], out_path: str) -> int:
    out = pymupdf.open()
    for p in paths:
        with open_pdf(p) as d:
            out.insert_pdf(d)
    n = len(out)
    save_doc(out, out_path, inputs=paths)
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


def split(path: str, groups: list[list[int]], out_dir: str) -> list[str]:
    src = open_pdf(path)
    base = os.path.splitext(os.path.basename(path))[0]
    outs = []
    for g in groups:
        out = pymupdf.open()
        for i in g:
            out.insert_pdf(src, from_page=i, to_page=i)
        name = f"{base}_стр{g[0] + 1}" + (f"-{g[-1] + 1}" if len(g) > 1 else "") + ".pdf"
        op = os.path.join(out_dir, name)
        k = 2
        while os.path.exists(op):
            op = os.path.join(out_dir, name[:-4] + f"_{k}.pdf")
            k += 1
        save_doc(out, op, inputs=(path,))
        out.close()
        outs.append(op)
    src.close()
    return outs


def split_every_page(path: str, out_dir: str) -> list[str]:
    with open_pdf(path) as d:
        n = len(d)
    return split(path, [[i] for i in range(n)], out_dir)


def rebuild(path: str, order: list[int], rotations: dict[int, int], out_path: str) -> int:
    """Новый файл: страницы в порядке `order` (удалённые просто не указаны), с поворотами."""
    src = open_pdf(path)
    out = pymupdf.open()
    for i in order:
        out.insert_pdf(src, from_page=i, to_page=i)
        rot = rotations.get(i, 0) % 360
        if rot:
            p = out[-1]
            p.set_rotation((p.rotation + rot) % 360)
    n = len(out)
    save_doc(out, out_path, inputs=(path,))
    out.close()
    src.close()
    return n
