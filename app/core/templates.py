"""Шаблоны бланков: рамки подписи, зоны номера, чернила + «отпечаток» макета для автоопределения.

Хранятся как JSON в %APPDATA%\\PdfTool\\templates\\*.json (Linux: ~/.local/share/PdfTool/templates).
Отпечаток — профиль строк печатного (тёмного, неокрашенного) текста страницы при 60 dpi.
По нему для каждой страницы выбирается ближайший шаблон и вычисляется вертикальный сдвиг
бланка, на который сдвигаются зоны номера.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

import numpy as np
import pymupdf

from .pdfutil import NRect, render_page, open_pdf

FP_DPI = 75
FP_LEN = 600          # длина профиля (ресэмплинг по высоте страницы)
MAX_SHIFT = 0.15      # максимальный вертикальный сдвиг бланка, доля высоты
MIN_SCORE = 0.45      # ниже — «шаблон не узнан»
VERSION = 1


def templates_dir() -> str:
    d = os.environ.get("PDFTOOL_TEMPLATES")
    if not d:
        from .logsetup import data_dir
        d = os.path.join(data_dir(), "templates")
    os.makedirs(d, exist_ok=True)
    return d


def _r(v) -> Optional[NRect]:
    return None if v is None else NRect(*[float(x) for x in v])


def _l(r: Optional[NRect]):
    return None if r is None else [round(r.x0, 5), round(r.y0, 5), round(r.x1, 5), round(r.y1, 5)]


@dataclass
class Template:
    name: str
    src_rect: Optional[NRect] = None                    # подпись в акте
    dst_rect: Optional[NRect] = None                    # куда ставить в расписке
    zones_act: list[NRect] = field(default_factory=list)
    zones_rec: list[NRect] = field(default_factory=list)
    ink_mode: str = "blue"
    sensitivity: float = 0.5
    keep_colors: bool = True
    fp_act: Optional[list[float]] = None                # отпечаток бланка акта
    fp_rec: Optional[list[float]] = None                # отпечаток бланка расписки

    # ---- сериализация ----
    def to_dict(self) -> dict:
        return {"format": "PdfTool-template", "version": VERSION, "name": self.name,
                "src_rect": _l(self.src_rect), "dst_rect": _l(self.dst_rect),
                "zones_act": [_l(z) for z in self.zones_act], "zones_rec": [_l(z) for z in self.zones_rec],
                "ink_mode": self.ink_mode, "sensitivity": float(self.sensitivity),
                "keep_colors": bool(self.keep_colors),
                "fp_act": self.fp_act, "fp_rec": self.fp_rec}

    @staticmethod
    def from_dict(d: dict) -> "Template":
        if d.get("format") != "PdfTool-template":
            raise ValueError("Это не файл шаблона PdfTool")
        return Template(name=str(d["name"]), src_rect=_r(d.get("src_rect")), dst_rect=_r(d.get("dst_rect")),
                        zones_act=[_r(z) for z in d.get("zones_act") or []],
                        zones_rec=[_r(z) for z in d.get("zones_rec") or []],
                        ink_mode=d.get("ink_mode", "blue"), sensitivity=float(d.get("sensitivity", 0.5)),
                        keep_colors=bool(d.get("keep_colors", True)),
                        fp_act=d.get("fp_act"), fp_rec=d.get("fp_rec"))


# ---------- хранилище ----------
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _fname(name: str) -> str:
    """Имя файла шаблона, допустимое и на Windows (без \\/:*?"<>|, не CON/NUL…)."""
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip() or "шаблон"
    if safe.split(".")[0].upper() in _WIN_RESERVED:
        safe = "_" + safe
    return os.path.join(templates_dir(), safe + ".json")


def save(t: Template) -> str:
    p = _fname(t.name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(t.to_dict(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def load_all() -> list[Template]:
    out = []
    try:
        folder = templates_dir()
        names = sorted(os.listdir(folder))
    except OSError:
        from .logsetup import log
        log.exception("Не удалось открыть папку шаблонов")
        return []
    for fn in names:
        if fn.endswith(".json"):
            try:
                with open(os.path.join(folder, fn), encoding="utf-8") as f:
                    out.append(Template.from_dict(json.load(f)))
            except Exception:  # noqa: BLE001 — битый файл не должен ронять программу
                from .logsetup import log
                log.exception("Не удалось прочитать шаблон %s", fn)
    return sorted(out, key=lambda t: t.name.lower())


def get(name: str) -> Optional[Template]:
    return next((t for t in load_all() if t.name == name), None)


def delete(name: str) -> None:
    p = _fname(name)
    if os.path.exists(p):
        os.remove(p)


def rename(old: str, new: str) -> Template:
    t = get(old)
    if t is None:
        raise KeyError(old)
    if new != old and get(new) is not None:
        raise ValueError(f"Шаблон «{new}» уже есть")
    t.name = new
    save(t)
    if _fname(old) != _fname(new):
        delete(old)
    return t


def export_to(t: Template, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(t.to_dict(), f, ensure_ascii=False, indent=1)
    return path


def import_from(path: str, overwrite: bool = False) -> Template:
    with open(path, encoding="utf-8") as f:
        t = Template.from_dict(json.load(f))
    if not overwrite and get(t.name) is not None:
        base, k = t.name, 2
        while get(f"{base} ({k})") is not None:
            k += 1
        t.name = f"{base} ({k})"
    save(t)
    return t


# ---------- отпечаток и автоопределение ----------
def _print_mask(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.float32)
    mx, mn = f.max(2), f.min(2)
    sat = (mx - mn) / np.maximum(mx, 1)
    return ((mx < 150) & (sat < 0.35)).astype(np.float32)


def page_profile(img: np.ndarray) -> np.ndarray:
    m = _print_mask(img)
    w = m.shape[1]
    p = m[:, int(w * .05):int(w * .95)].mean(1)
    return np.interp(np.linspace(0, len(p) - 1, FP_LEN), np.arange(len(p)), p).astype(np.float32)


def fingerprint(doc: pymupdf.Document, index: int) -> list[float]:
    return [round(float(x), 4) for x in page_profile(render_page(doc, index, FP_DPI))]


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def match(fp_ref: Sequence[float], prof: np.ndarray, center: float = 0.5, half: float = 0.07) -> tuple[float, float]:
    """Сдвиг страницы относительно эталона по окну профиля вокруг center (доля высоты).
    Возвращает (сходство -1..1, сдвиг по Y в долях высоты)."""
    ref = np.asarray(fp_ref, np.float32)
    n = len(ref)
    c, h, m = int(center * n), max(4, int(half * n)), int(MAX_SHIFT * n)
    lo, hi = max(0, c - h), min(n, c + h)
    win = ref[lo:hi]
    best = (-1.0, 0.0)
    for d in range(-m, m + 1):
        if lo + d < 0 or hi + d > len(prof):
            continue
        sc = _ncc(win, prof[lo + d:hi + d])
        if sc > best[0]:
            best = (sc, d / n)
    return best


@dataclass
class Detected:
    template: Optional[Template]
    score: float
    dy: float
    prof: Optional[np.ndarray] = None

    def dy_at(self, r: Optional[NRect], role: str) -> float:
        """Сдвиг бланка, измеренный около рамки r (подпись бывает далеко от зоны номера)."""
        if self.template is None or r is None or self.prof is None:
            return self.dy
        fp = self.template.fp_act if role == "act" else self.template.fp_rec
        sc, dy = match(fp, self.prof, (r.y0 + r.y1) / 2)
        return dy if sc >= MIN_SCORE else self.dy

    def zones(self, role: str) -> list[NRect]:
        if self.template is None:
            return []
        zs = self.template.zones_act if role == "act" else self.template.zones_rec
        return [NRect(z.x0, z.y0 + self.dy, z.x1, z.y1 + self.dy) for z in zs]


def detect(prof: np.ndarray, temps: Sequence[Template], role: str) -> Detected:
    best = Detected(None, -1.0, 0.0)
    for t in temps:
        fp = t.fp_act if role == "act" else t.fp_rec
        zs = t.zones_act if role == "act" else t.zones_rec
        if not fp:
            continue
        center = (zs[0].y0 + zs[0].y1) / 2 if zs else 0.5
        sc, dy = match(fp, prof, center)
        if sc > best.score:
            best = Detected(t, sc, dy)
    if best.score < MIN_SCORE:
        return Detected(None, best.score, 0.0, prof)
    best.prof = prof
    return best


def detect_pages(path: str, temps: Sequence[Template], role: str,
                 pages: Optional[Sequence[int]] = None) -> dict[int, Detected]:
    """Определяет бланк каждой страницы файла (role = "act" или "rec")."""
    out: dict[int, Detected] = {}
    with open_pdf(path) as doc:
        for i in (pages if pages is not None else range(len(doc))):
            out[i] = detect(page_profile(render_page(doc, i, FP_DPI)), temps, role)
    return out


def shifted(r: Optional[NRect], dy: float) -> Optional[NRect]:
    return None if r is None else NRect(r.x0, r.y0 + dy, r.x1, r.y1 + dy)


def apply_detected_frames(job, temps: Sequence[Template]) -> dict:
    """Для каждой страницы АКТА берёт рамку подписи из узнанного шаблона (со сдвигом бланка).
    Рамку в расписке не двигаем: на расписках сдвиг по профилю оказался ненадёжным (job2 — хуже, чем без него).
    Уже заданные вручную рамки (overrides) не трогает. Возвращает {"act": {стр: имя}, "rec": {}}."""
    names = {"act": {}, "rec": {}}
    if not any(t.fp_act for t in temps):
        return names
    acts_used = sorted({a for a in job.mapping if a is not None})
    for a, d in detect_pages(job.acts_path, temps, "act", acts_used).items():
        if d.template is not None:
            names["act"][a] = d.template.name
            if d.template.src_rect is not None and a not in job.src_overrides:
                job.src_overrides[a] = shifted(d.template.src_rect, d.dy_at(d.template.src_rect, "act"))
    return names
