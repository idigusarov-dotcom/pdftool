"""Автономное распознавание текста на сканах (Tesseract внутри MuPDF, модели rus+eng в программе).

MuPDF не даёт выбрать режим сегментации Tesseract (psm), поэтому качество добирается подготовкой картинки:
выравнивание фона и контраста, удаление пыли, выпрямление наклона, запас белого поля и масштаб ~300 dpi.
Строки собираются по их положению на картинке и возвращаются вместе с рамками на странице PDF."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf as pdf
from PIL import Image, ImageFilter

from .resources import resource

TARGET_DPI = 300
MAX_SIDE = 4200          # пикселей; большие области уменьшаются, чтобы распознавание не тянулось минутами
PAD = 24


@dataclass(frozen=True)
class OcrLine:
    text: str
    rect: tuple          # рамка строки в координатах неповёрнутой страницы PDF
    height: float        # высота строки в пунктах — подсказка для размера шрифта замены


def _render(page, rect):
    """Участок страницы так, как его видит пользователь (с учётом поворота), примерно в 300 dpi."""
    clip = pdf.Rect(rect) * page.rotation_matrix
    clip.normalize()
    scale = min(TARGET_DPI / 72, MAX_SIDE / max(clip.width, clip.height, 1))
    pix = page.get_pixmap(matrix=pdf.Matrix(scale, scale), clip=clip, alpha=False, colorspace=pdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return gray.astype(np.float32), clip, scale


def _flatten(gray):
    """Делит на оценку фона: тень, жёлтая бумага и градиент от сканера становятся белым листом."""
    h, w = gray.shape
    k = max(1, min(h, w) // 40)
    small = Image.fromarray(gray.astype(np.uint8)).resize((max(1, w // k), max(1, h // k)), Image.BOX)
    bg = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2)).resize((w, h), Image.BILINEAR)
    bg = np.maximum(np.asarray(bg, np.float32), 1)
    flat = np.clip(gray / bg * 255, 0, 255)
    lo, hi = np.percentile(flat, (0.5, 99.5))
    if hi - lo < 8:
        return flat
    return np.clip((flat - lo) * 255 / (hi - lo), 0, 255)


def _otsu(img):
    hist = np.bincount(img.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    w0 = np.cumsum(hist)
    m0 = np.cumsum(hist * np.arange(256))
    w1 = total - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (m0[-1] * w0 / total - m0) ** 2 / (w0 * w1)
    return int(np.nanargmax(between[:-1]))


def _despeckle(ink, flat, loose=None):
    """Убирает пыль: мелкие пятна вдали от букв. Точки, запятые и точки над «ё» стоят вплотную к буквам — их не трогаем."""
    from .ink import _components
    comps = _components(ink)
    if len(comps) < 3:
        return flat, False
    sizes = np.array([len(ys) for ys, _ in comps])
    boxes = [(ys.min(), ys.max(), xs.min(), xs.max()) for ys, xs in comps]
    heights = np.array([b[1] - b[0] + 1 for b in boxes])
    # Высота буквы — по крупным областям; пылинки обычно в разы меньше.
    # Пылинок может быть в десять раз больше, чем букв, поэтому опираемся на самые высокие области.
    letter = float(np.median(heights[heights >= 0.4 * np.percentile(heights, 99.5)]))
    tiny = max(6.0, (letter * 0.22) ** 2)
    near = np.zeros(ink.shape, bool)
    gx, gy = int(letter * 0.4) + 1, int(letter * 0.45) + 1
    for (y0, y1, x0, x1), size in zip(boxes, sizes):
        if size > tiny and (y1 - y0) > letter * 0.35:
            near[max(0, y0 - gy):y1 + gy + 1, max(0, x0 - gx):x1 + gx + 1] = True
    out = flat.copy()
    removed = 0
    # Пыль бывает и бледнее порога чернил: её ищем по мягкому порогу, а буквы — по строгому.
    for ys, xs in (comps if loose is None else _components(loose)):
        # Возле букв убираем только бледные пятна: настоящие точки и запятые тёмные.
        if len(ys) <= tiny * 1.5 and (not near[ys, xs].any() or not ink[ys, xs].any()):
            out[ys, xs] = 255
            removed += 1
    letters = int((sizes > tiny).sum())
    return out, removed > letters


def _box_sum(a, r):
    c = np.pad(a, ((r + 1, r), (r + 1, r))).cumsum(0).cumsum(1)
    return c[2 * r + 1:, 2 * r + 1:] - c[:-2 * r - 1, 2 * r + 1:] - c[2 * r + 1:, :-2 * r - 1] + c[:-2 * r - 1, :-2 * r - 1]


def _skew_angle(ink):
    """Угол наклона строк по резкости горизонтальной проекции (±10°)."""
    h, w = ink.shape
    f = max(1, max(h, w) // 900)
    small = Image.fromarray((ink[::f, ::f] * 255).astype(np.uint8))
    if np.asarray(small).sum() == 0:
        return 0.0

    def score(angle):
        rot = np.asarray(small.rotate(angle, resample=Image.NEAREST, expand=True), np.float32)
        prof = rot.sum(1)
        return float(np.square(np.diff(prof)).sum())

    best = max(np.arange(-10, 10.01, 1.0), key=score)
    best = max(np.arange(best - 1, best + 1.01, 0.2), key=score)
    return float(best) if abs(best) >= 0.3 else 0.0


def prepare(gray):
    """Картинка для Tesseract и угол выпрямления. Возвращает (uint8, угол)."""
    flat = _flatten(gray)
    ink = flat < _otsu(flat)
    if ink.mean() > 0.5:          # почти всё «чернила» — светлый текст на тёмном; не трогаем
        return np.clip(gray, 0, 255).astype(np.uint8), 0.0
    threshold = _otsu(flat)
    flat, dusty = _despeckle(ink, flat, flat < threshold + (255 - threshold) * 0.55)
    if dusty:   # пыли больше, чем букв: добиваем одиночные точки внутри строк медианой 3×3
        flat = np.asarray(Image.fromarray(flat.astype(np.uint8)).filter(ImageFilter.MedianFilter(3)), float)
    angle = _skew_angle(flat < _otsu(flat))
    img = Image.fromarray(flat.astype(np.uint8))
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    padded = Image.new("L", (img.width + 2 * PAD, img.height + 2 * PAD), 255)
    padded.paste(img, (PAD, PAD))
    return np.asarray(padded), angle


def _ocr_page(img):
    h, w = img.shape
    gray = pdf.Pixmap(pdf.csGRAY, w, h, np.ascontiguousarray(img).tobytes(), 0)
    pix = pdf.Pixmap(pdf.csRGB, gray)       # распознавание MuPDF принимает только цветные картинки
    pix.set_dpi(TARGET_DPI, TARGET_DPI)
    data = pix.pdfocr_tobytes(language="rus+eng", tessdata=resource("core", "models", "tessdata"))
    return pdf.open("pdf", data)


def _group_lines(page):
    """Слова → строки по центру строки, слева направо. Рамки в пикселях картинки.

    Строку не «раздуваем» объединением рамок: одно высокое слово (мусор, скобка) иначе
    склеивало соседние строки в одну.
    """
    k = TARGET_DPI / 72
    words = [(pdf.Rect(w[:4]) * k, w[4]) for w in page.get_text("words")]
    if not words:
        return []
    typical = float(np.median([b.height for b, _ in words])) or 1.0
    words.sort(key=lambda t: (t[0].y0 + t[0].y1) / 2)
    lines = []                      # [сумма центров, число, слова]
    for box, word in words:
        cy = (box.y0 + box.y1) / 2
        best = min(lines, key=lambda l: abs(l[0] / l[1] - cy), default=None)
        if best is not None and abs(best[0] / best[1] - cy) < 0.5 * typical:
            best[0] += cy
            best[1] += 1
            best[2].append((box, word))
        else:
            lines.append([cy, 1, [(box, word)]])
    result = []
    for _, _, ws in sorted(lines, key=lambda l: l[0] / l[1]):
        ws.sort(key=lambda t: t[0].x0)
        normal = [b for b, _ in ws if b.height <= 2 * typical] or [b for b, _ in ws]
        box = pdf.Rect(normal[0])
        for b in normal[1:]:
            box |= b
        result.append((box, " ".join(w for _, w in ws)))
    return result


def recognize_lines(page, rect) -> list[OcrLine]:
    gray, clip, scale = _render(page, rect)
    img, angle = prepare(gray)
    with _ocr_page(img) as ocr:
        grouped = _group_lines(ocr[0])
    # Обратно: поле, выпрямление, масштаб, смещение рамки, поворот страницы.
    h0, w0 = gray.shape
    hr, wr = img.shape[0] - 2 * PAD, img.shape[1] - 2 * PAD
    rad = np.deg2rad(angle)
    cos, sin = np.cos(rad), np.sin(rad)
    to_page = ~page.rotation_matrix

    def back(x, y):
        x, y = x - PAD - wr / 2, y - PAD - hr / 2
        # PIL.rotate(angle) поворачивает против часовой стрелки; обратный поворот — по часовой.
        ox = cos * x - sin * y + w0 / 2
        oy = sin * x + cos * y + h0 / 2
        return pdf.Point(clip.x0 + ox / scale, clip.y0 + oy / scale) * to_page

    result = []
    for box, words in grouped:
        pts = [back(x, y) for x in (box.x0, box.x1) for y in (box.y0, box.y1)]
        r = pdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
        result.append(OcrLine(words, tuple(r), (box.y1 - box.y0) / scale))
    return result


def recognize(page, rect) -> str:
    return "\n".join(line.text for line in recognize_lines(page, rect))
