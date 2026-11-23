"""Печатный текст бланка в рамке подписи (для чёрной ручки).

Синюю ручку от печатного текста отличает цвет. Чёрную — нет: в рамку «В чем и расписываемся» попадают
«1.», «(Ф. И. О.)», «(подпись)», и они переносились в расписку. Но бланк у всех актов один и тот же,
а подписи разные: то, что тёмное на одном и том же месте у большинства актов, — бланк. Скан каждого акта
чуть сдвинут и повёрнут, поэтому совмещаем по полосам (каждую полосу — своим сдвигом)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

MIN_PAGES = 3          # меньше трёх актов — не с чем сравнивать
MAX_PAGES = 12         # для оценки бланка хватает дюжины страниц
MAX_SHIFT = 18         # px при 200 dpi
STRIPS = 3            # не больше трёх полос и не уже 300 px: в узкой полосе мало печати для совмещения
OVERLAP = 40         # px
STRIP_R = 3          # px: насколько полоса может «уйти» от общего сдвига
COVER = 0.8         # штрих целиком на бланке — печать; рукопись бланк лишь пересекает


def dark(rgb: np.ndarray) -> np.ndarray:
    """Тёмные малонасыщенные пиксели (печать, чёрная ручка)."""
    f = rgb.astype(np.float32)
    mx, mn = f.max(-1), f.min(-1)
    return (f.mean(-1) < 150) & (mx - mn < 70)


def _dilate(m: np.ndarray, k: int) -> np.ndarray:
    return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(k))) > 0


def _shift(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(m)
    h, w = m.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = m[ys, xs]
    return out


def _best_shift(ref: np.ndarray, m: np.ndarray, center=(0, 0), radius: int = MAX_SHIFT) -> tuple[int, int]:
    """Сдвиг m к ref (корреляция размытых масок через БПФ), в пределах radius от center."""
    a = np.asarray(Image.fromarray((ref * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), np.float32)
    b = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), np.float32)
    if a.std() < 1e-3 or b.std() < 1e-3:
        return center
    A, B = np.fft.rfft2(a - a.mean()), np.fft.rfft2(b - b.mean())
    c = np.fft.irfft2(A * np.conj(B), s=a.shape)
    h, w = a.shape
    best, arg = -np.inf, (0, 0)
    cy, cx = center
    for dy in range(cy - radius, cy + radius + 1):
        row = c[dy % h]
        for dx in range(cx - radius, cx + radius + 1):
            v = row[dx % w]
            if v > best:
                best, arg = v, (dy, dx)
    return arg


class FormRef:
    """Бланк, найденный по нескольким актам. mask(crop) — маска печатного текста на любой странице."""

    def __init__(self, crops: list[np.ndarray]):
        crops = [c for c in crops if c is not None and c.size]
        self.ok = len(crops) >= MIN_PAGES and len({c.shape[:2] for c in crops}) == 1
        if not self.ok:
            return
        if len(crops) > MAX_PAGES:
            crops = [crops[int(i)] for i in np.linspace(0, len(crops) - 1, MAX_PAGES)]
        h, w = self.shape = crops[0].shape[:2]
        k = max(1, min(STRIPS, w // 300))
        self.bounds = [(int(i * w / k), int((i + 1) * w / k)) for i in range(k)]
        masks = [dark(c) for c in crops]
        n = len(masks)
        need = n if n <= 5 else int(np.ceil(0.8 * n))       # тёмное почти у всех — бланк
        # 1) грубо: сдвиг всей картинки к первой странице; 2) точно: каждая полоса — к найденному бланку
        # (в нём уже нет подписей, и чужая подпись не «перетягивает» совмещение)
        self.ref = masks[0]
        whole = [(0, w)]
        count = np.zeros((h, w), np.int32)
        for m in masks:
            (dy, dx), = self._shifts(m, whole)
            count += _dilate(_shift(m, dy, dx), 3)  # грубо — с запасом
        self.ref = count >= need
        for _ in range(2):
            counts = [np.zeros((h, w), np.int32) for _ in self.bounds]
            for m in masks:
                for k, (dy, dx) in enumerate(self._shifts(m)):
                    counts[k] += _dilate(_shift(m, dy, dx), 3)
            ref = np.zeros((h, w), bool)
            for (x0, x1), c in zip(self.bounds, counts):     # полосы с нахлёстом: буква на стыке — целиком
                e0, e1 = max(0, x0 - OVERLAP), min(w, x1 + OVERLAP)
                ref[:, e0:e1] |= c[:, e0:e1] >= need
            self.ref = ref

    def _shifts(self, m, bounds=None):
        g = _best_shift(self.ref, m)
        if bounds is not None:
            return [g]
        # каждая полоса — уточнение общего сдвига (небольшой поворот скана), не дальше STRIP_R px:
        # в полосе может почти не быть печати, и тогда «свободный» поиск совмещал бы подписи
        return [_best_shift(self.ref[:, x0:x1], m[:, x0:x1], g, STRIP_R) if self.ref[:, x0:x1].sum() > 50 else g
                for x0, x1 in self.bounds]

    def mask(self, crop: np.ndarray) -> np.ndarray | None:
        if not self.ok or crop is None or not crop.size:
            return None
        oh, ow = crop.shape[:2]
        if (oh, ow) != self.shape:
            crop = np.asarray(Image.fromarray(crop).resize(self.shape[::-1], Image.BILINEAR))
        h, w = self.shape
        page = dark(crop)
        m = np.zeros((h, w), bool)
        for (x0, x1), (dy, dx) in zip(self.bounds, self._shifts(page)):
            e0, e1 = max(0, x0 - OVERLAP), min(w, x1 + OVERLAP)   # с нахлёстом: буква на стыке полос — целиком
            m[:, e0:e1] |= _shift(self.ref, -dy, -dx)[:, e0:e1]
        m = _dilate(m, 5)
        # убираем целиком только те штрихи страницы, что почти полностью лежат на бланке (печатные буквы,
        # линии); рукопись, которая где-то пересекает бланк, остаётся целой
        from .ink import _components, _remove_long_lines
        lines = page & ~_remove_long_lines(page, max(40, int(0.35 * w)))
        own = _dilate(page & ~_dilate(lines, 3), 3)     # линии бланка не «склеивают» рукопись с печатью
        res = _dilate(lines, 3)
        for ys, xs in _components(own):
            if m[ys, xs].mean() >= COVER:
                res[ys, xs] = True
        if (oh, ow) != self.shape:
            res = np.asarray(Image.fromarray(res.astype(np.uint8) * 255).resize((ow, oh), Image.NEAREST)) > 0
        return res


def form_masks(crops: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
    """crops: страница -> кусок скана (H×W×3, одинакового размера). Возвращает страница -> маска бланка."""
    ref = FormRef(list(crops.values()))
    if not ref.ok:
        return {}
    return {p: ref.mask(c) for p, c in crops.items()}
