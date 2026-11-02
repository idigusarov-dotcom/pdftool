"""Номера квартир по зонам: чтение страниц и автосопоставление актов с расписками."""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

import numpy as np
import pymupdf

from .digits import DigitResult, read_best
from .pdfutil import NRect, render_page

OCR_DPI = 150


def read_document(path: str, zones: Sequence[NRect], mode: str = "blue", sensitivity: float = 0.5,
                  progress: Callable[[int, int], bool | None] | None = None,
                  pages: Optional[Sequence[int]] = None) -> list[DigitResult]:
    """Читает номер на каждой странице. progress(i, n) может вернуть False — отмена."""
    out: list[DigitResult] = []
    with pymupdf.open(path) as doc:
        idx = list(pages) if pages is not None else list(range(len(doc)))
        for k, i in enumerate(idx):
            if progress and progress(k, len(idx)) is False:
                break
            out.append(read_best(render_page(doc, i, OCR_DPI), zones, mode, sensitivity))
    return out


def _pair_score(a: DigitResult, b: DigitResult) -> float:
    """Лог-вероятность того, что на двух страницах написан один и тот же номер."""
    if a.probs is None or b.probs is None or not len(a.probs) or not len(b.probs):
        return -30.0
    if len(a.probs) != len(b.probs):
        # разное число цифр — сравниваем по тексту с большим штрафом
        return -12.0 - 3.0 * abs(len(a.probs) - len(b.probs)) + (4.0 if a.text == b.text else 0.0)
    return float(sum(math.log(max(float(np.dot(pa, pb)), 1e-6)) for pa, pb in zip(a.probs, b.probs)))


def auto_match(receipts: list[DigitResult], acts: list[DigitResult], min_score: float = -6.0):
    """Сопоставляет расписки с актами (один акт — одной расписке, жадно по лучшему счёту).
    Возвращает (mapping, уверенность 0..1 по каждой расписке)."""
    pairs = []
    for r, rr in enumerate(receipts):
        for a, aa in enumerate(acts):
            pairs.append((_pair_score(rr, aa), r, a))
    pairs.sort(reverse=True)
    mapping: list[Optional[int]] = [None] * len(receipts)
    conf = [0.0] * len(receipts)
    used_r, used_a = set(), set()
    for sc, r, a in pairs:
        if sc < min_score:
            break
        if r in used_r or a in used_a:
            continue
        mapping[r] = a
        conf[r] = math.exp(sc)
        used_r.add(r)
        used_a.add(a)
    return mapping, conf
