"""Сортировка страниц по номеру квартиры."""
from __future__ import annotations

import csv
import re
from collections import Counter
from typing import Optional

import pymupdf

from .pages import rebuild

_APT_RE = re.compile(r"\bкв(?:артир[аыеу])?[.:]?\s*(?:№|N)?[\s_]*(\d+(?:[-/]\d+)*(?:-?[а-яёa-z])?)(?!\w)", re.IGNORECASE)


def guess_apartment(page: pymupdf.Page) -> Optional[str]:
    """Номер квартиры из текстового слоя (если он есть). Сканы без текста → None."""
    text = page.get_text("text") or ""
    m = _APT_RE.search(text)
    return m.group(1) if m else None


def guess_all(doc: pymupdf.Document) -> list[Optional[str]]:
    return [guess_apartment(p) for p in doc]


def apt_key(value: str | None):
    """Ключ сортировки: числа по возрастанию, «12а» после 12, пустые — в конец."""
    v = (value or "").strip().casefold()
    parts = []
    for part in re.findall(r"\d+|\D+", v):
        if part.isdecimal():
            digits = "".join(str(int(ch)) for ch in part).lstrip("0") or "0"
            parts.append((0, len(digits), digits))
        else:
            parts.append((1, part))
    return tuple(parts) or ((2, ""),)


def duplicates(values: list[Optional[str]]) -> set[str]:
    names = [v.strip() for v in values if v and v.strip()]
    counts = Counter(apt_key(v) for v in names)
    return {v for v in names if counts[apt_key(v)] > 1}


def sorted_order(values: list[Optional[str]], descending=False) -> list[int]:
    filled = [i for i, v in enumerate(values) if v and v.strip()]
    empty = [i for i, v in enumerate(values) if not v or not v.strip()]
    return sorted(filled, key=lambda i: apt_key(values[i]), reverse=descending) + empty


def export_sorted(src_path: str, values: list[Optional[str]], out_path: str, descending=False) -> list[int]:
    order = sorted_order(values, descending)
    rebuild(src_path, order, {}, out_path)
    return order


def export_csv(values: list[Optional[str]], out_path: str, descending=False) -> None:
    dup = duplicates(values)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["№ п/п", "Квартира", "Исходная страница", "Повтор"])
        for n, i in enumerate(sorted_order(values, descending), 1):
            v = (values[i] or "").strip()
            w.writerow([n, v, i + 1, "да" if v in dup else ""])
