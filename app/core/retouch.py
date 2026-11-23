"""Ручная доработка вырезанного фрагмента: «Ластик» и «Вернуть чернила».

Правки хранятся не как картинка, а как список мазков (поправка к автоматической маске). Координаты мазков —
в долях страницы-источника, поэтому правки не зависят от разрешения (предпросмотр 150 dpi, итог 300 dpi),
переживают сдвиг рамки и смену чувствительности/режима: автоматическая маска пересчитывается, а мазки
накладываются поверх неё заново.

Порядок важен: более поздний мазок перекрывает ранний в тех же пикселях.
«Вернуть чернила» не рисует произвольным цветом — берёт пиксели исходного скана (тот же режим цвета,
что и автоматическое вырезание)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from .ink import DEFAULT_INK_RGB, unblend_white

ERASE, RESTORE = "erase", "restore"
_CODE = {ERASE: 1, RESTORE: 2}


@dataclass(frozen=True)
class Stroke:
    op: str                                   # ERASE / RESTORE
    radius: float                             # радиус кисти в долях ширины страницы
    points: tuple[tuple[float, float], ...]   # точки (x, y) в долях страницы


Retouch = tuple  # tuple[Stroke, ...] — неизменяемый, удобно для отмены и сравнения


def rasterize(strokes, box: tuple[int, int, int, int], page_px: tuple[int, int]) -> np.ndarray:
    """Метки пикселей фрагмента: 0 — как вырезалось автоматически, 1 — стёрто, 2 — возвращено.
    box — (x0, y0, x1, y1) фрагмента в пикселях страницы размером page_px = (W, H)."""
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    lab = np.zeros((h, w), np.uint8)
    W, H = page_px
    for s in strokes:
        code = _CODE.get(s.op)
        if not code or not s.points:
            continue
        r = max(0.5, s.radius * W)
        pts = [(x * W - x0, y * H - y0) for x, y in s.points]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) + r < 0 or min(xs) - r > w or max(ys) + r < 0 or min(ys) - r > h:
            continue
        m = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(m)
        if len(pts) > 1:
            d.line(pts, fill=255, width=max(1, int(round(2 * r))))
        for px, py in pts:
            d.ellipse((px - r, py - r, px + r, py + r), fill=255)
        lab[np.asarray(m) > 0] = code
    return lab


def paper_level(crop_rgb: np.ndarray, block: int = 48):
    """Уровень бумаги по самому фрагменту: карта H×W (яркость «самого светлого канала» минус шум, т. е.
    min(R,G,B)) и её цвет H×W×3. Сканы бывают серые, желтоватые, с градиентом — белой бумагу считать нельзя.
    Считаем по блокам (80-й процентиль — бумага, даже если в блоке есть чернила), плавно растягиваем на весь
    кусок. Блок почти целиком в чернилах (печать) не должен «стать бумагой» — снизу ограничиваем общим уровнем."""
    rgb = crop_rgb.astype(np.float32)
    mn = rgb.min(-1)
    h, w = mn.shape
    g = float(np.percentile(mn, 90))
    floor = g - 25.0
    ny, nx = max(1, round(h / block)), max(1, round(w / block))
    lv = np.empty((ny, nx), np.float32)
    col = np.empty((ny, nx, 3), np.float32)
    for i in range(ny):
        for j in range(nx):
            ys = slice(i * h // ny, max(i * h // ny + 1, (i + 1) * h // ny))
            xs = slice(j * w // nx, max(j * w // nx + 1, (j + 1) * w // nx))
            m = mn[ys, xs]
            v = max(float(np.percentile(m, 80)), floor)
            lv[i, j] = v
            sel = m >= v - 6
            col[i, j] = rgb[ys, xs][sel].mean(0) if sel.any() else rgb[ys, xs].reshape(-1, 3).max(0)
    up = lambda arr: np.asarray(Image.fromarray(arr).resize((w, h), Image.BILINEAR), np.float32)  # noqa: E731
    level = up(lv)
    color = np.dstack([up(np.ascontiguousarray(col[..., k])) for k in range(3)])
    # шум бумаги: разброс светлых пикселей вокруг уровня (MAD), чтобы зерно скана не «возвращалось»
    d = level - mn
    paperish = d < 20
    noise = 1.4826 * float(np.median(np.abs(d[paperish] - np.median(d[paperish])))) if paperish.any() else 0.0
    return level, color, noise


def restore_layer(crop_rgb: np.ndarray, mode: str, keep_colors: bool) -> np.ndarray:
    """Что появится под кистью «Вернуть чернила»: пиксели исходного скана без бумаги.
    Прозрачность — по тому, насколько пиксель темнее бумаги ВОКРУГ (уровень бумаги оценивается по самому
    фрагменту — серый, желтоватый скан тоже даёт прозрачную бумагу), цвет — как у автоматического
    вырезания: настоящий (без ореола цвета бумаги) или единый цвет синей ручки."""
    if mode == "none":           # «без удаления фона» — возвращаем кусок как есть
        return np.dstack([crop_rgb, np.full(crop_rgb.shape[:2], 255, np.uint8)])
    mn = crop_rgb.astype(np.float32).min(-1)
    level, color, noise = paper_level(crop_rgb)
    margin = max(10.0, 3.5 * noise)            # на белой бумаге (255) — прежний порог 245
    a = np.clip((level - margin - mn) / 70.0, 0, 1) ** 0.7
    a[a < 0.04] = 0
    if keep_colors:
        col = unblend_white(crop_rgb, a, paper=color)
    else:
        col = np.broadcast_to(np.array(DEFAULT_INK_RGB, np.uint8), a.shape + (3,))
    return np.dstack([col, (a * 255).astype(np.uint8)]).astype(np.uint8)


def apply(auto_rgba: np.ndarray, crop_rgb: np.ndarray, strokes, box, page_px, mode: str,
          keep_colors: bool, labels: np.ndarray | None = None) -> np.ndarray:
    """Автоматический фрагмент + ручные правки. Стёртое — прозрачное, возвращённое — из исходного скана
    (непрозрачнее из двух: автоматического и исходного, чтобы не ослабить уже взятые чернила)."""
    if not strokes and labels is None:
        return auto_rgba
    lab = rasterize(strokes, box, page_px) if labels is None else labels
    h, w = auto_rgba.shape[:2]
    lab = lab[:h, :w]
    out = auto_rgba.copy()
    if (lab == 2).any():
        rest = restore_layer(crop_rgb[:h, :w], mode, keep_colors)
        m = lab == 2
        take = m & (rest[..., 3] >= out[..., 3])
        out[take] = rest[take]
    out[lab == 1] = 0
    return out


def page_point(x_px: float, y_px: float, box, page_px) -> tuple[float, float]:
    """Точка в пикселях фрагмента -> доли страницы."""
    return ((box[0] + x_px) / page_px[0], (box[1] + y_px) / page_px[1])
