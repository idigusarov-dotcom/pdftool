"""Синтетические «сканы» с выдуманным русским и английским текстом для проверки распознавания.

Реальных документов и людей здесь нет: адрес, ФИО и организация придуманы."""
from __future__ import annotations

import io
import os

import numpy as np
import pymupdf as pdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
INTER = os.path.join(HERE, "..", "app", "ui", "assets", "fonts", "Inter-Regular.ttf")

LINES = [
    "Акт осмотра квартиры № 48 составлен 14 марта 2026 года.",
    "Собственник Белова Нина Григорьевна претензий не имеет.",
    "Работы по замене стояка выполнены в полном объёме.",
    "Адрес: ул. Вымышленная, д. 7, корп. 2, г. Энск.",
    "Invoice No. 2026/17 paid in full, total 125 000 RUB.",
]

# name: (dpi, pt, skew, paper, ink, noise, blur, jpeg, gradient, speckle)
CONDITIONS = {
    "clean_300": (300, 12, 0.0, (255, 255, 255), 20, 0, 0.0, None, 0.0, 0.0),
    "scan_150": (150, 12, 0.0, (250, 249, 246), 30, 6, 0.0, 85, 0.0, 0.0),
    "skew_3": (200, 12, 3.0, (252, 251, 248), 25, 4, 0.0, 90, 0.0, 0.0),
    "skew_7": (200, 12, -7.0, (250, 250, 246), 30, 5, 0.0, 85, 0.0, 0.0),
    "toned_gradient": (200, 12, 0.8, (226, 218, 196), 55, 8, 0.0, 85, 0.18, 0.0),
    "shadow": (200, 12, 1.2, (225, 220, 205), 75, 7, 0.0, 80, 0.55, 0.0),
    "faint": (200, 12, 0.6, (238, 236, 230), 150, 6, 0.4, 85, 0.0, 0.0),
    "phone_like": (170, 12, -1.7, (214, 210, 200), 80, 9, 0.9, 70, 0.22, 0.0),
    "low_100": (100, 10, 0.4, (246, 245, 240), 35, 6, 0.0, 75, 0.0, 0.0),
    "speckle": (200, 12, 0.7, (248, 247, 243), 30, 5, 0.0, 85, 0.0, 0.004),
    "small_9pt": (200, 9, 0.5, (248, 247, 244), 35, 5, 0.0, 85, 0.0, 0.0),
}


def render(lines=LINES, font=INTER, condition="clean_300", seed=7):
    """Возвращает (png, ширина, высота, dpi, прямоугольник текста в пикселях скана)."""
    dpi, pt, skew, paper, ink, noise, blur, jpeg, gradient, speckle = CONDITIONS[condition]
    rng = np.random.default_rng(seed)
    px = pt * dpi / 72
    face = ImageFont.truetype(font, max(6, round(px)))
    step = round(px * 1.55)
    width = round(7.2 * dpi)
    height = round(px * 2 + step * len(lines) + px * 2)
    sheet = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(sheet)
    for i, text in enumerate(lines):
        d.text((round(px * 2), round(px * 1.5) + i * step), text, fill=0, font=face)
    if skew:
        sheet = sheet.rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=255)
    a = np.asarray(sheet, np.float32) / 255.0          # 1 — бумага, 0 — чернила
    rgb = np.empty((height, width, 3), np.float32)
    for c in range(3):
        rgb[..., c] = ink + (paper[c] - ink) * a
    if gradient:
        ramp = 1 - gradient * np.linspace(0, 1, width)[None, :] * np.linspace(0.4, 1, height)[:, None]
        rgb *= ramp[..., None]
    if noise:
        rgb += rng.normal(0, noise, rgb.shape)
    if speckle:
        dots = rng.random((height, width)) < speckle
        rgb[dots] = ink
        grow = np.roll(dots, 1, 0) | np.roll(dots, 1, 1)      # пятнышки 2×2, как пыль на стекле сканера
        rgb[grow & (rng.random((height, width)) < 0.5)] = ink
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    if jpeg:
        img.save(buf, "JPEG", quality=jpeg)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), width, height, dpi, (0, 0, width, height)


def scan_page(doc, lines=LINES, font=INTER, condition="clean_300", seed=7, rotation=0):
    """Страница PDF с картинкой-сканом. Возвращает (страница, рамка текста в точках неповёрнутой страницы).

    rotation=90/270 — скан лежит в файле боком, а поворот страницы показывает его ровно (как у сканеров)."""
    png, w, h, dpi, _ = render(lines, font, condition, seed)
    iw, ih = w * 72 / dpi, h * 72 / dpi
    if rotation in (90, 270):
        page = doc.new_page(width=ih + 144, height=iw + 72)
        rect = pdf.Rect(72, 36, 72 + ih, 36 + iw)
        page.insert_image(rect, stream=png, rotate=rotation)
    else:
        page = doc.new_page(width=iw + 72, height=ih + 144)
        rect = pdf.Rect(36, 72, 36 + iw, 72 + ih)
        page.insert_image(rect, stream=png, rotate=rotation)
    if rotation:
        page.set_rotation(rotation)
    return page, tuple(rect)


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _norm(text):
    return " ".join(text.replace("ё", "е").replace("Ё", "Е").split())


def cer(ref, hyp):
    ref, hyp = _norm(ref), _norm(hyp)
    return _lev(ref, hyp) / max(1, len(ref))


def wer(ref, hyp):
    ref, hyp = _norm(ref).split(), _norm(hyp).split()
    return _lev(ref, hyp) / max(1, len(ref))


def line_order_ok(ref_lines, hyp):
    """Строки распознаны в том же порядке и каждая — отдельной строкой."""
    got = [l for l in (s.strip() for s in hyp.splitlines()) if l]
    if len(got) != len(ref_lines):
        return False
    return all(cer(r, g) < 0.35 for r, g in zip(ref_lines, got))
