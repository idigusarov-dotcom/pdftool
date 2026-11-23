"""Синтетические кропы подписи разными цветами ручек (вымышленные ФИО, без реальных данных)."""
from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "qa" / "fonts"
APP_FONTS = ROOT / "app" / "ui" / "assets" / "fonts"
HAND = str(FONTS / "BadScript-Regular.ttf")
SERIF = str(APP_FONTS / "Inter-Regular.ttf")
SERIF_B = str(APP_FONTS / "Inter-SemiBold.ttf")

PENS = {
    "blue_ballpoint": dict(rgb=(28, 46, 150), label="синяя шариковая"),
    "pale_blue": dict(rgb=(100, 145, 200), label="бледно-синяя", alpha=0.55),
    "navy": dict(rgb=(15, 25, 80), label="тёмно-синяя / navy"),
    "black": dict(rgb=(35, 35, 40), label="чёрная шариковая"),
    "purple": dict(rgb=(90, 40, 140), label="фиолетовая"),
    "red": dict(rgb=(180, 30, 40), label="красная"),
    "green": dict(rgb=(20, 110, 55), label="зелёная"),
    "blue_gel": dict(rgb=(20, 70, 210), label="синяя гелевая"),
    "black_gel": dict(rgb=(15, 15, 18), label="чёрная гелевая"),
    "pencil": dict(rgb=(88, 88, 92), label="карандаш", alpha=0.62),
    "mix": dict(rgb=(28, 46, 150), rgb2=(35, 35, 40), label="смесь: ФИО синим, росчерк чёрным"),
}

PAPERS = {
    "cream": (243, 241, 233),
    "yellow": (228, 208, 162),
    "grey": (198, 198, 202),
}

# Какой режим пользователь выберет для этого цвета (синяя по умолчанию — для синих/фиолетовых).
RECOMMENDED_MODE = {
    "blue_ballpoint": "blue", "pale_blue": "blue", "navy": "blue", "purple": "blue", "blue_gel": "blue",
    "black": "black", "black_gel": "black", "pencil": "black",
    "red": "any", "green": "any", "mix": "any",
}


def font(path, size):
    return ImageFont.truetype(path, int(size))


def draw_path(layer, path, rng, color, base=3.0, press=True, alpha=0.9):
    if len(path) < 2:
        return
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    x0, y0 = int(min(xs) - 12), int(min(ys) - 12)
    x1, y1 = int(max(xs) + 13), int(max(ys) + 13)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(layer.shape[1], x1), min(layer.shape[0], y1)
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(m)
    n = len(path)
    for i in range(n - 1):
        p = 0.55 + 0.45 * math.sin(math.pi * i / n) if press else 1.0
        wdt = max(1, int(base * p + rng.normal(0, 0.3)))
        d.line([(int(path[i][0]) - x0, int(path[i][1]) - y0),
                (int(path[i + 1][0]) - x0, int(path[i + 1][1]) - y0)], fill=255, width=wdt)
    a = np.asarray(m.filter(ImageFilter.GaussianBlur(0.7)), np.float32) / 255
    a *= np.clip(rng.normal(alpha, 0.06, a.shape), 0.35, 1)
    reg = layer[y0:y1, x0:x1]
    layer[y0:y1, x0:x1] = reg * (1 - a[..., None]) + np.array(color, np.float32) * a[..., None]


def hand_text(layer, xy, text, rng, size, color, font_path=HAND, alpha=0.9):
    f = font(font_path, size)
    im = Image.new("L", (int(size * (len(text) + 2) * 1.1), int(size * 2.2)), 0)
    ImageDraw.Draw(im).text((int(size * 0.2), int(size * 0.15)), text, fill=255, font=f)
    im = im.rotate(float(rng.uniform(-4, 6)), resample=Image.BICUBIC, expand=True)
    a = np.asarray(im, np.float32) / 255
    a *= np.clip(rng.normal(alpha, 0.05, a.shape), 0.4, 1)
    y, x = int(xy[1]), int(xy[0])
    h, w = a.shape
    y1, x1 = min(layer.shape[0], y + h), min(layer.shape[1], x + w)
    aa = a[: y1 - y, : x1 - x]
    reg = layer[y:y1, x:x1]
    layer[y:y1, x:x1] = reg * (1 - aa[..., None]) + np.array(color, np.float32) * aa[..., None]
    mask = np.zeros(layer.shape[:2], np.float32)
    mask[y:y1, x:x1] = aa
    return mask


def signature_flourish(layer, x0, y0, w, h, rng, color, alpha=0.9):
    path = []
    for i in range(70):
        t = i / 69
        r = 0.35 * h * (1 + 0.15 * math.sin(7 * t))
        path.append((x0 + 0.1 * w + 0.85 * w * t - r * 0.7 * math.sin(5 * t),
                     y0 + 0.45 * h + r * math.cos(5 * t) + rng.normal(0, 0.4)))
    before = layer.copy()
    draw_path(layer, path, rng, color, base=4.0, alpha=alpha)
    return (np.abs(layer.astype(np.float32) - before).sum(-1) > 8).astype(np.float32)


def printed_text(layer, xy, text, size, bold=False, color=(35, 35, 38)):
    f = font(SERIF_B if bold else SERIF, size)
    im = Image.new("L", (int(size * (len(text) + 2)), int(size * 2)), 0)
    ImageDraw.Draw(im).text((0, 0), text, fill=255, font=f)
    a = np.asarray(im, np.float32) / 255 * 0.92
    y, x = int(xy[1]), int(xy[0])
    h, w = a.shape
    y1, x1 = min(layer.shape[0], y + h), min(layer.shape[1], x + w)
    aa = a[: y1 - y, : x1 - x, None]
    layer[y:y1, x:x1] = layer[y:y1, x:x1] * (1 - aa) + np.array(color, np.float32) * aa
    mask = np.zeros(layer.shape[:2], bool)
    mask[y:y1, x:x1] = a[: y1 - y, : x1 - x] > 0.25
    return mask


def hline(layer, x0, x1, y, color=(40, 40, 42), width=2):
    mask = np.zeros(layer.shape[:2], bool)
    y0, y1 = int(y) - width, int(y) + width + 1
    xa, xb = int(x0), int(x1)
    layer[y0:y1, xa:xb] = color
    mask[y0:y1, xa:xb] = True
    return mask


def finish_scan(layer, rng, kind="cream"):
    a = layer.astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    if kind == "phone":
        mid = a.mean()
        a = mid + (a - mid) * 0.72
        vign = 1 - 0.22 * (((xx / w - 0.5) ** 2 + (yy / h - 0.45) ** 2) * 4)
        a *= vign[..., None]
        a += (8, 4, -6)
        a += rng.normal(0, 9.0, a.shape)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.1))
        small = img.resize((w // 2, h // 2), Image.BILINEAR).resize((w, h), Image.BILINEAR)
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=42)
        return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    a *= (1 - 0.025 * (xx / w) - 0.015 * (yy / h))[..., None]
    a += rng.normal(0, 6.0, a.shape)
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.55))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=68)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))


def make_crop(pen_key, paper_key="cream", phone=False, seed=2026, W=900, H=280):
    """Кроп зоны подписи. Возвращает rgb, core_ink, form_only, paper_only, spec.
    core_ink — плотная часть штриха (без бледной каймы после JPEG), по ней считаем полноту."""
    spec = PENS[pen_key]
    rng = np.random.default_rng(seed)
    paper = np.array(PAPERS[paper_key], np.float32)
    layer = np.broadcast_to(paper, (H, W, 3)).copy()
    form = np.zeros((H, W), bool)
    form |= printed_text(layer, (18, 40), "Подпись собственника:", 22)
    form |= printed_text(layer, (18, 200), "(Ф. И. О.)", 16)
    form |= printed_text(layer, (520, 200), "(подпись)", 16)
    form |= hline(layer, 18, 870, 188, width=2)

    c1 = spec["rgb"]
    c2 = spec.get("rgb2", c1)
    alpha = spec.get("alpha", 0.9)
    ink = np.zeros((H, W), np.float32)
    ink = np.maximum(ink, hand_text(layer, (40, 95), "Гаврилов Д. С.", rng, 42, c1, HAND, alpha=alpha))
    ink = np.maximum(ink, signature_flourish(layer, 520, 90, 300, 80, rng, c2, alpha=alpha))

    rgb = finish_scan(layer, rng, "phone" if phone else "cream")
    core = ink >= 0.45
    fat = np.asarray(Image.fromarray((ink * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 40
    form_gt = np.asarray(Image.fromarray((form.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(3))) > 0
    form_only = form_gt & ~fat
    paper_only = ~fat & ~form_gt
    return rgb, core, form_only, paper_only, spec
