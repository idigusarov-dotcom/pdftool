"""Распознавание рукописных цифр номера квартиры в заданной зоне.

Лёгкая сверточная сеть MNIST (ONNX Model Zoo, mnist-12, лицензия MIT/Apache),
веса перенесены в numpy (models/digits_mnist.npz, ~25 КБ) — без onnxruntime/torch.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image

from .ink import ink_alpha


def _model_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "models", "digits_cnn.npz")
    if not os.path.exists(p):
        p = os.path.join(base, "app", "core", "models", "digits_cnn.npz")
    return p


@lru_cache(maxsize=1)
def _weights():
    return dict(np.load(_model_path()))


def _conv_same(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """x (N,C,H,W), w (O,C,5,5), паддинг SAME."""
    n, c, h, wd = x.shape
    o, _, kh, kw = w.shape
    xp = np.pad(x, ((0, 0), (0, 0), (kh // 2, kh // 2), (kw // 2, kw // 2)))
    win = np.lib.stride_tricks.sliding_window_view(xp, (kh, kw), axis=(2, 3))  # N,C,H,W,kh,kw
    out = np.einsum("nchwij,ocij->nohw", win, w, optimize=True)
    return out + b[None, :, None, None]


def _maxpool(x: np.ndarray, k: int) -> np.ndarray:
    n, c, h, w = x.shape
    h2, w2 = h // k, w // k
    return x[:, :, :h2 * k, :w2 * k].reshape(n, c, h2, k, w2, k).max(axis=(3, 5))


def classify(batch: np.ndarray) -> np.ndarray:
    """batch (N,28,28) float 0..1 (белая цифра на чёрном) → вероятности (N,10)."""
    W = _weights()
    x = batch[:, None].astype(np.float32)
    x = _maxpool(np.maximum(_conv_same(x, W["c1w"], W["c1b"]), 0), 2)
    x = _maxpool(np.maximum(_conv_same(x, W["c2w"], W["c2b"]), 0), 2)
    x = np.maximum(x.reshape(len(x), -1) @ W["f1w"] + W["f1b"], 0)
    logits = x @ W["f2w"] + W["f2b"]
    e = np.exp(logits - logits.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def _label(mask: np.ndarray):
    """Связные компоненты (8-связность) без OpenCV/scipy. Возвращает список (y0,x0,y1,x1,кол-во)."""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    comps = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys, xs):
        if lab[sy, sx]:
            continue
        idx = len(comps) + 1
        stack = [(sy, sx)]
        lab[sy, sx] = idx
        y0 = y1 = sy
        x0 = x1 = sx
        cnt = 0
        while stack:
            y, x = stack.pop()
            cnt += 1
            y0, y1, x0, x1 = min(y0, y), max(y1, y), min(x0, x), max(x1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = idx
                        stack.append((ny, nx))
        comps.append([y0, x0, y1 + 1, x1 + 1, cnt])
    return comps, lab


def _to_mnist(alpha: np.ndarray) -> np.ndarray:
    """Вырезанная цифра → 28x28 как в MNIST: вписать в 20x20, центр масс в центр."""
    if _THICK and (alpha > 0.3).mean() < _THIN:
        from PIL import ImageFilter
        alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(_THICK)), np.float32) / 255
    h, w = alpha.shape
    s = 20.0 / max(h, w)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    img = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).resize((nw, nh), Image.LANCZOS), np.float32)
    out = np.zeros((28, 28), np.float32)
    y0, x0 = (28 - nh) // 2, (28 - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = img
    tot = out.sum()
    if tot > 0:
        yy, xx = np.mgrid[0:28, 0:28]
        cy, cx = (out * yy).sum() / tot, (out * xx).sum() / tot
        out = np.roll(out, (int(round(14 - cy)), int(round(14 - cx))), axis=(0, 1))
    return np.clip(out * _GAIN, 0, 255) * _SCALE


_MIN_MEAN = 0.6
_DARK_V, _DARK_S = 110, 0.35
_GAIN, _SCALE, _THICK, _THIN = 1.3, 1 / 255, 3, 0.2


@dataclass
class DigitResult:
    text: str
    confidence: float      # 0..1 (произведение уверенностей по цифрам)
    n_parts: int
    probs: np.ndarray | None = None   # (n_parts, 10) вероятности по каждой цифре

    @property
    def ok(self) -> bool:
        # 1–3 цифры (в доме не больше 999 квартир) и высокая уверенность сети
        return bool(self.text) and len(self.text) <= 3 and self.confidence >= 0.9


def segment(rgb: np.ndarray, mode: str = "blue", sensitivity: float = 0.5, max_digits: int = 4) -> np.ndarray:
    """Вырезает цифры из зоны → массив (N,28,28) в формате MNIST (0..1)."""
    empty = np.zeros((0, 28, 28), np.float32)
    if rgb.size == 0:
        return empty
    a = ink_alpha(rgb, mode, sensitivity)
    if mode == "blue":
        # тёмные малонасыщенные пиксели — это печатный текст бланка, а не синяя ручка
        f = rgb.astype(np.float32)
        v = f.max(-1)
        sat = (v - f.min(-1)) / np.maximum(v, 1)
        a = a * ~((v < _DARK_V) & (sat < _DARK_S))
    mask = a > 0.35
    h, w = mask.shape
    if mask.sum() < 15:
        return empty
    comps, lab = _label(mask)
    for k, c in enumerate(comps):
        c.append({k + 1})
        c.append(float(a[lab == k + 1].mean()))
    # печатный чёрный текст даёт на сканах слабую цветную «бахрому» — отбрасываем бледные куски
    comps = [c[:6] for c in comps if c[4] >= max(8, 0.0004 * h * w) and c[6] >= _MIN_MEAN]
    if not comps:
        return empty
    hmax = max(c[2] - c[0] for c in comps)
    comps = [c for c in comps if (c[2] - c[0]) >= 0.45 * hmax]
    comps.sort(key=lambda c: c[1])
    merged = []  # склеиваем части одной цифры, перекрывающиеся по X
    for c in comps:
        if merged:
            m = merged[-1]
            ov = min(m[3], c[3]) - max(m[1], c[1])
            if ov > 0.5 * min(m[3] - m[1], c[3] - c[1]):
                merged[-1] = [min(m[0], c[0]), min(m[1], c[1]), max(m[2], c[2]), max(m[3], c[3]), m[4] + c[4],
                              m[5] | c[5]]
                continue
        merged.append(c)
    # слишком широкий кусок (слитные цифры) — делим по ширине
    parts = []
    for c in merged:
        hh, ww = c[2] - c[0], c[3] - c[1]
        k = int(round(ww / (0.75 * hh))) if ww > 1.2 * hh else 1
        k = max(1, min(k, max_digits))
        for j in range(k):
            parts.append((c[0], c[1] + ww * j // k, c[2], c[1] + ww * (j + 1) // k, c[5]))
    parts = parts[:max_digits]
    crops = []
    for y0, x0, y1, x1, ids in parts:
        sub = a[y0:y1, x0:x1] * np.isin(lab[y0:y1, x0:x1], list(ids))
        ys, xs = np.nonzero(sub > 0.2)
        if len(ys):
            sub = sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        crops.append(sub)
    return np.stack([_to_mnist(c) for c in crops]).astype(np.float32)


def read_number(rgb: np.ndarray, mode: str = "blue", sensitivity: float = 0.5, max_digits: int = 4) -> DigitResult:
    """Читает номер (цифры) в вырезанной зоне страницы."""
    batch = segment(rgb, mode, sensitivity, max_digits)
    if len(batch) == 0:
        return DigitResult("", 0.0, 0)
    p = classify(batch)
    digits = "".join(str(int(i)) for i in p.argmax(1))
    conf = float(np.prod(p.max(1)))
    return DigitResult(digits.lstrip("0") or digits, conf, len(batch), p)


def ink_amount(rgb: np.ndarray, mode: str = "blue", sensitivity: float = 0.5) -> float:
    return float((ink_alpha(rgb, mode, sensitivity) > 0.35).sum()) if rgb.size else 0.0


def read_best(page_rgb: np.ndarray, zones, mode: str = "blue", sensitivity: float = 0.5) -> DigitResult:
    """Несколько зон (разные бланки): читаем ту, где больше всего чернил."""
    h, w = page_rgb.shape[:2]
    best, best_ink = DigitResult("", 0.0, 0), 0.0
    for z in zones:
        x0, y0, x1, y1 = z.to_pixels(w, h)
        crop = page_rgb[y0:y1, x0:x1]
        ink = ink_amount(crop, mode, sensitivity)
        if ink > best_ink:
            best, best_ink = read_number(crop, mode, sensitivity), ink
    return best
