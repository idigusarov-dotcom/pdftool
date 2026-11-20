"""Сортировка страниц по номеру квартиры."""
from __future__ import annotations

import csv
import re
from collections import Counter
from typing import Optional

import pymupdf

from .pdfutil import save_doc, open_pdf

_APT_RE = re.compile(r"кв(?:артир[аы])?\.?\s*(?:№|N)?\s*_*\s*(\d{1,4})", re.IGNORECASE)


def guess_apartment(page: pymupdf.Page) -> Optional[str]:
    """Номер квартиры из текстового слоя (если он есть). Сканы без текста → None."""
    text = page.get_text("text") or ""
    m = _APT_RE.search(text)
    return m.group(1) if m else None


def guess_all(doc: pymupdf.Document) -> list[Optional[str]]:
    return [guess_apartment(p) for p in doc]


def apt_key(value: str | None):
    """Ключ сортировки: числа по возрастанию, «12а» после 12, пустые — в конец."""
    v = (value or "").strip()
    m = re.match(r"(\d+)\s*(.*)", v)
    if m:
        return (0, int(m.group(1)), m.group(2).lower())
    return (1, 0, v.lower())


def duplicates(values: list[Optional[str]]) -> set[str]:
    c = Counter(v.strip() for v in values if v and v.strip())
    return {k for k, n in c.items() if n > 1}


def sorted_order(values: list[Optional[str]]) -> list[int]:
    return sorted(range(len(values)), key=lambda i: (apt_key(values[i]), i))


def export_sorted(src_path: str, values: list[Optional[str]], out_path: str) -> list[int]:
    src = open_pdf(src_path)
    order = sorted_order(values)
    out = pymupdf.open()
    for i in order:
        out.insert_pdf(src, from_page=i, to_page=i)
    save_doc(out, out_path, inputs=(src_path,))
    out.close()
    src.close()
    return order


def export_csv(values: list[Optional[str]], out_path: str) -> None:
    dup = duplicates(values)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["№ п/п", "Квартира", "Исходная страница", "Повтор"])
        for n, i in enumerate(sorted_order(values), 1):
            v = (values[i] or "").strip()
            w.writerow([n, v, i + 1, "да" if v in dup else ""])
