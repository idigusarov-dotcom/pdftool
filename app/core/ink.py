"""Выделение подписи (синие чернила) и удаление белого фона у картинок."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

DEFAULT_INK_RGB = (28, 48, 160)  # единый цвет "шариковой ручки"


def _rgb_to_hsv_cv(rgb: np.ndarray) -> np.ndarray:
    """HSV в шкале OpenCV (H 0..180, S,V 0..255) без зависимости от cv2."""
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx = f.max(-1)
    mn = f.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = ((g - b)[rm] / d[rm]) % 6
    h[gm] = (b - r)[gm] / d[gm] + 2
    h[bm] = (r - g)[bm] / d[bm] + 4
    h = h * 30.0  # 60 градусов / 2
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0) * 255
    return np.dstack([h, s, mx * 255])


def blue_ink_alpha(rgb: np.ndarray, sat_min: float = 14, hue_lo: float = 92, hue_hi: float = 145) -> np.ndarray:
    """Маска синих/фиолетовых чернил 0..1 (как в ручной обработке job2)."""
    hsv = _rgb_to_hsv_cv(rgb)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    a = np.clip((S - sat_min) / 40.0, 0, 1) * ((H >= hue_lo) & (H <= hue_hi)) * np.clip((250 - V) / 35.0, 0, 1)
    return a.astype(np.float32)


INK_MODES = {"blue": "синяя", "black": "чёрная", "any": "любая"}
# для «Вырезать и перенести»: ещё цветная печать и «без удаления фона»
FRAGMENT_MODES = {"blue": "синие чернила", "black": "чёрные чернила / карандаш", "color": "цветная печать (штамп)",
                  "any": "всё, кроме белого фона", "none": "без удаления фона (как есть)"}


def _remove_long_lines(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Убирает длинные горизонтальные и вертикальные отрезки (линии бланка)."""
    out = mask.copy()
    for axis in (1, 0):
        m = mask if axis == 1 else mask.T
        o = out if axis == 1 else out.T
        # длина серии подряд идущих пикселей вдоль оси
        d = np.diff(np.pad(m.astype(np.int8), ((0, 0), (1, 1))), axis=1)
        for r in range(m.shape[0]):
            starts = np.nonzero(d[r] == 1)[0]
            ends = np.nonzero(d[r] == -1)[0]
            for s0, e0 in zip(starts, ends):
                if e0 - s0 >= min_len:
                    o[r, s0:e0] = False
    return out


def ink_alpha(rgb: np.ndarray, mode: str = "blue", sensitivity: float = 0.5) -> np.ndarray:
    """Маска чернил 0..1. mode: blue — синие/фиолетовые; black — тёмные серые/чёрные
    (линии бланка убираются); any — любые не-белые. sensitivity 0..1: выше — берём бледнее."""
    sens = float(np.clip(sensitivity, 0, 1))
    if mode == "blue":
        return blue_ink_alpha(rgb, sat_min=26 - 24 * sens)  # 0.5 → 14, как в job2
    if mode == "color":   # любой цветной оттенок (синий, фиолетовый, красный штамп); чёрный текст не берём
        hsv = _rgb_to_hsv_cv(rgb)
        smin = 40 - 30 * sens
        return (np.clip((hsv[..., 1] - smin) / 40.0, 0, 1) * np.clip((252 - hsv[..., 2]) / 30.0, 0, 1)).astype(
            np.float32)
    gray = rgb.astype(np.float32).mean(-1)
    thr = 120 + 110 * sens          # порог яркости
    a = np.clip((thr - gray) / 40.0, 0, 1)
    if mode == "black":
        sat = _rgb_to_hsv_cv(rgb)[..., 1]
        a = a * (sat < 80)
        h, w = a.shape
        lines = (a > 0.3) & ~_remove_long_lines(a > 0.3, max(40, int(0.35 * w)))
        if h > 40:
            lines |= (a > 0.3) & ~_remove_long_lines((a > 0.3), max(40, int(0.5 * h)))
        # расширяем найденные линии на 1 пиксель, чтобы не оставались «тени»
        from PIL import ImageFilter
        lines = np.asarray(Image.fromarray((lines * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
        a = a * ~lines
    if mode == "any":   # тёмное ИЛИ цветное: светлые цветные оттиски печати не теряются
        a = np.maximum(a, ink_alpha(rgb, "color", sensitivity))
    return a.astype(np.float32)


def _components(mask: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Связные области (8-связность) небольшой маски: список (ys, xs). Обход в ширину — маска
    здесь — только отброшенные «точки», их немного, так что без OpenCV/SciPy это быстро."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return []
    idx = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}
    seen = np.zeros(len(ys), bool)
    out = []
    for i in range(len(ys)):
        if seen[i]:
            continue
        seen[i] = True
        stack, comp = [i], []
        while stack:
            j = stack.pop()
            comp.append(j)
            y, x = int(ys[j]), int(xs[j])
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    k = idx.get((y + dy, x + dx))
                    if k is not None and not seen[k]:
                        seen[k] = True
                        stack.append(k)
        c = np.array(comp)
        out.append((ys[c], xs[c]))
    return out


def stroke_width(core: np.ndarray) -> float:
    """Толщина штриха в пикселях: 2 * площадь / периметр (для линии ≈ её ширина)."""
    from PIL import ImageFilter
    area = int(core.sum())
    if area < 20:
        return 2.0
    er = np.asarray(Image.fromarray((core * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))) > 0
    perim = int((core & ~er).sum())
    return float(np.clip(2.0 * area / max(perim, 1), 1.5, 12.0))


def _beside_letters(kept: np.ndarray, ys, xs, sw: float, reach: int) -> bool:
    """Точка стоит рядом с буквой, как точка после инициала: слева/справа (в пределах reach) есть
    заметный кусок чернил, который поднимается выше точки (буква выше точки) и опускается примерно до
    её низа (точка — на строке, а не висит над или под надписью). Цветная «бахрома» сжатия у края
    печатного текста и пыль у кромки этому не отвечают."""
    h, w = kept.shape
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    win = kept[max(0, y0 - int(8 * sw) - reach):min(h, y1 + reach + 1), max(0, x0 - reach):min(w, x1 + reach + 1)]
    if win.sum() < 4 * sw * sw:
        return False
    rows = np.nonzero(win.any(1))[0] + max(0, y0 - int(8 * sw) - reach)
    return rows.min() <= y0 - 2 * sw and rows.max() >= y1 - 2 * sw


def _remove_specks(a: np.ndarray, radius: int = 7, min_pixels: int = 30) -> np.ndarray:
    """Убирает одиночные точки (пыль скана): оставляем чернила только там, где в окне
    (2*radius+1)^2 набирается не меньше min_pixels «уверенных» пикселей. Без OpenCV.

    Но точка, поставленная ручкой рядом с надписью (точки после инициалов «Е. П.», точка над «i»),
    по плотности неотличима от пыли — её возвращаем, если она: (1) не мельче следа ручки
    (площадь от трети квадрата толщины штриха), (2) компактная и круглая — не длиннее ~2,5 толщин, не тоньше
    0,5 толщины, вытянутость до 2,5 раз (обрывки линий бланка и волоски так не выглядят), (3) уверенно
    закрашена, (4) лежит вплотную к уже принятым чернилам (не дальше ~4 толщин штриха), (5) стоит «на строке»
    рядом с буквой (см. _beside_letters).
    Пыль посреди пустого места по-прежнему убирается."""
    from PIL import ImageFilter
    core = a > 0.5
    win = (2 * radius + 1) ** 2
    dens = Image.fromarray((core * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(radius))
    ok = np.asarray(dens, np.float32) / 255.0 * win >= min_pixels
    keep = np.asarray(Image.fromarray((ok * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    lost = core & ~keep
    kept = core & keep
    if lost.any() and kept.sum() >= min_pixels:
        sw = stroke_width(kept)
        reach = int(round(4 * sw + 3))
        near = np.asarray(Image.fromarray((kept * 255).astype(np.uint8)).filter(
            ImageFilter.MaxFilter(2 * reach + 1))) > 0
        comps = _components(lost & near) if (lost & near).sum() < 20000 else []
        rescue = np.zeros_like(core)
        for ys, xs in comps:
            n = len(ys)
            bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            if (n >= max(4, 0.33 * sw * sw) and max(bw, bh) <= 2.5 * sw + 2
                    and min(bw, bh) >= max(2, 0.5 * sw) and max(bw, bh) <= 2.5 * min(bw, bh)
                    and n >= 0.5 * bw * bh and float(a[ys, xs].mean()) >= 0.6
                    and _beside_letters(kept, ys, xs, sw, reach)):
                rescue[ys, xs] = True
        if rescue.any():
            grow = np.asarray(Image.fromarray((rescue * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
            keep = keep | grow
    return a * keep


def extract_ink(rgb: np.ndarray, color=DEFAULT_INK_RGB, uniform: bool = True,
                mode: str = "blue", sensitivity: float = 0.5) -> np.ndarray:
    """Возвращает RGBA (H,W,4) uint8: только чернила, фон прозрачный."""
    a = ink_alpha(rgb, mode, sensitivity)
    a = _remove_specks(a)
    a = np.power(a, 0.7)
    v = a[a > 0.4]
    if v.size:
        gain = min(2.5, 0.95 / max(float(np.percentile(v, 95)), 1e-3))
        a = np.clip(a * gain, 0, 1)
    a[a < 0.04] = 0
    if uniform:
        col = np.broadcast_to(np.array(color, np.uint8), a.shape + (3,))
    else:
        col = unblend_white(rgb, a)
    return np.dstack([col, (a * 255).astype(np.uint8)]).astype(np.uint8)


def unblend_white(rgb: np.ndarray, alpha: np.ndarray, paper=255.0) -> np.ndarray:
    """Настоящий цвет чернил: пиксель скана = a*цвет + (1-a)*бумага  ->  цвет = бумага - (бумага - пиксель)/a.
    Без этого края подписи/печати в режиме «сохранить цвет» получаются бледными с белым ореолом.
    paper — цвет бумаги (число, RGB или карта H×W×3); по умолчанию белая."""
    a = np.clip(alpha.astype(np.float32), 1e-3, 1)[..., None]
    p = np.asarray(paper, np.float32)
    c = p - (p - rgb.astype(np.float32)) / a
    return np.clip(c, 0, 255).astype(np.uint8)


def has_ink(rgba: np.ndarray, min_pixels: int = 30) -> bool:
    return int((rgba[..., 3] > 100).sum()) >= min_pixels


def remove_white_background(img: Image.Image, threshold: int = 235, soft: int = 30) -> Image.Image:
    """Делает белый/светлый фон прозрачным с мягким краем."""
    rgba = np.array(img.convert("RGBA")).astype(np.float32)
    mn = rgba[..., :3].min(-1)
    a = np.clip((threshold - mn) / max(soft, 1), 0, 1)
    rgba[..., 3] = rgba[..., 3] * a
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def to_png_bytes(arr_or_img) -> bytes:
    img = arr_or_img if isinstance(arr_or_img, Image.Image) else Image.fromarray(arr_or_img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
