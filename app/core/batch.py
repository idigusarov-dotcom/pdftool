"""Пакетный перенос подписей: несколько пар «акты + расписки» за один проход."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import pymupdf

from . import signatures as sig
from . import templates as tp
from .logsetup import log
from .pdfutil import NRect


class Cancelled(Exception):
    pass


@dataclass
class BatchItem:
    acts_path: str
    receipts_path: str
    out_path: str
    template: Optional[str] = None            # имя шаблона
    mapping: Optional[list] = None            # None — по порядку (или как исправлено во вкладке)
    src_overrides: dict = field(default_factory=dict)
    dst_overrides: dict = field(default_factory=dict)
    auto_detect: bool = True                  # определять бланк каждой страницы по шаблонам


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def check_items(items: Sequence[BatchItem]) -> list[str]:
    """Ошибки до запуска: совпадающие выходные файлы, выход = вход, нет файлов."""
    errs = []
    inputs = {_norm(p) for it in items for p in (it.acts_path, it.receipts_path)}
    seen = {}
    for k, it in enumerate(items, 1):
        for p in (it.acts_path, it.receipts_path):
            if not os.path.exists(p):
                errs.append(f"Пара {k}: нет файла {p}")
        o = _norm(it.out_path)
        if o in inputs:
            errs.append(f"Пара {k}: результат нельзя записать поверх исходного файла ({it.out_path})")
        if o in seen:
            errs.append(f"Пары {seen[o]} и {k}: одинаковое имя результата ({it.out_path})")
        seen.setdefault(o, k)
    return errs


def build_job(it: BatchItem, temps: Sequence[tp.Template]) -> sig.TransferJob:
    with pymupdf.open(it.acts_path) as a, pymupdf.open(it.receipts_path) as r:
        n_a, n_r = len(a), len(r)
    mapping = list(it.mapping) if it.mapping is not None else sig.guess_mapping(n_r, n_a)
    mapping = (mapping + [None] * n_r)[:n_r]
    job = sig.TransferJob(it.acts_path, it.receipts_path, mapping)
    t = next((x for x in temps if x.name == it.template), None) if it.template else None
    if t is not None:
        job.src_rect = t.src_rect or job.src_rect
        job.dst_rect = t.dst_rect or job.dst_rect
        job.ink_mode, job.sensitivity = t.ink_mode, t.sensitivity
    job.src_overrides = {int(k): v for k, v in it.src_overrides.items()}
    job.dst_overrides = {int(k): v for k, v in it.dst_overrides.items()}
    if it.auto_detect and temps:
        tp.apply_detected_frames(job, temps)
    return job


def run_batch(items: Sequence[BatchItem], temps: Sequence[tp.Template] = (),
              progress: Callable[[int, int], bool | None] | None = None) -> list[dict]:
    """Обрабатывает пары по очереди. progress(сделано, всего) может вернуть False — отмена.
    Возвращает итог по каждой паре: status ok/error/cancelled/skipped, placed, blank, empty_ink, error."""
    errs = check_items(items)
    if errs:
        raise ValueError("\n".join(errs))
    sizes = []
    for it in items:
        with pymupdf.open(it.receipts_path) as r:
            sizes.append(len(r))
    total, base = sum(sizes), 0
    results: list[dict] = []
    cancelled = False
    for k, it in enumerate(items):
        res = {"pair": k, "out": it.out_path, "status": "ok", "placed": 0, "blank": [], "empty_ink": [], "error": ""}
        if cancelled:
            res["status"] = "skipped"
            results.append(res)
            continue

        def prog(i, n, base=base):
            if progress and progress(base + i, total) is False:
                raise Cancelled()

        try:
            prog(0, 0)
            job = build_job(it, temps)
            rep = sig.apply_transfer(job, it.out_path, progress=prog)
            res.update(placed=len(rep["placed"]), blank=rep["blank"], empty_ink=rep["empty_ink"])
        except Cancelled:
            cancelled = True
            res["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001 — ошибка одной пары не останавливает остальные
            log.exception("Пакет: пара %d", k + 1)
            res["status"], res["error"] = "error", str(e)
        results.append(res)
        base += sizes[k]
    if progress and not cancelled:
        progress(total, total)
    return results
