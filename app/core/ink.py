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


def _remove_specks(a: np.ndarray, radius: int = 7, min_pixels: int = 30) -> np.ndarray:
    """Убирает одиночные точки (пыль скана): оставляем чернила только там, где в окне
    (2*radius+1)^2 набирается не меньше min_pixels «уверенных» пикселей. Без OpenCV."""
    from PIL import ImageFilter
    core = (a > 0.5).astype(np.float32)
    win = (2 * radius + 1) ** 2
    dens = Image.fromarray((core * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(radius))
    ok = np.asarray(dens, np.float32) / 255.0 * win >= min_pixels
    keep = Image.fromarray((ok * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
    return a * (np.asarray(keep) > 0)


def extract_ink(rgb: np.ndarray, color=DEFAULT_INK_RGB, uniform: bool = True) -> np.ndarray:
    """Возвращает RGBA (H,W,4) uint8: только чернила, фон прозрачный."""
    a = blue_ink_alpha(rgb)
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
        col = rgb
    return np.dstack([col, (a * 255).astype(np.uint8)]).astype(np.uint8)


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
