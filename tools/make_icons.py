"""Рисует значок программы (свой, без чужих логотипов): синий скруглённый квадрат, белый лист
с загнутым уголком и синяя «подпись». Выход: app/ui/assets/PdfTool.icns (macOS), PdfTool.ico (Windows),
app/ui/assets/icons/app-logo.png (логотип в боковой панели).  Запуск: python tools/make_icons.py"""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(ROOT, "app", "ui", "assets")
S = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def squircle_mask(size, box, n=5.0):
    """Маска «суперэллипса» (форма значков macOS Big Sur+), сглаженная 4x."""
    k = 4
    big = Image.new("L", (size * k, size * k), 0)
    x0, y0, x1, y1 = [v * k for v in box]
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    for i in range(720):
        t = 2 * math.pi * i / 720
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + rx * math.copysign(abs(c) ** (2 / n), c), cy + ry * math.copysign(abs(s) ** (2 / n), s)))
    ImageDraw.Draw(big).polygon(pts, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def render(full_bleed=False):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    box = (0, 0, S, S) if full_bleed else (100, 100, 924, 924)    # сетка значков macOS: 824 из 1024
    # тень под значком
    if not full_bleed:
        sh = squircle_mask(S, (100, 118, 924, 942)).filter(ImageFilter.GaussianBlur(18))
        img.paste((0, 0, 0, 70), (0, 0), sh)
    # фон-градиент
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (64, 156, 255), (0, 98, 224)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bot, y / S) + (255,))
    img.paste(grad, (0, 0), squircle_mask(S, box))
    x0, y0, x1, y1 = box
    w = x1 - x0
    # лист бумаги
    k = 4
    paper = Image.new("RGBA", (S * k, S * k), (0, 0, 0, 0))
    pd = ImageDraw.Draw(paper)
    px0, py0 = x0 + w * 0.25, y0 + w * 0.17
    px1, py1 = x0 + w * 0.75, y0 + w * 0.83
    fold = w * 0.15
    r = w * 0.045
    poly = [(px0 + r, py0), (px1 - fold, py0), (px1, py0 + fold), (px1, py1 - r), (px1 - r, py1),
            (px0 + r, py1), (px0, py1 - r), (px0, py0 + r)]
    pd.polygon([(x * k, y * k) for x, y in poly], fill=(255, 255, 255, 255))
    for cx, cy in ((px1 - r, py1 - r), (px0 + r, py1 - r), (px0 + r, py0 + r)):
        pd.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=(255, 255, 255, 255))
    pd.polygon([(x * k, y * k) for x, y in ((px1 - fold, py0), (px1, py0 + fold), (px1 - fold, py0 + fold))],
               fill=(205, 222, 248, 255))
    # строки текста
    lc = (196, 210, 232, 255)
    lw = w * 0.028
    for i, frac in enumerate((0.62, 0.78, 0.70)):
        y = py0 + w * (0.22 + i * 0.075)
        x_end = px0 + (px1 - px0 - w * 0.12) * frac + w * 0.06
        pd.rounded_rectangle([(px0 + w * 0.06) * k, (y - lw / 2) * k, x_end * k, (y + lw / 2) * k],
                             radius=lw / 2 * k, fill=lc)
    # подпись — плавная синяя линия
    pts = []
    for i in range(200):
        t = i / 199
        x = px0 + w * 0.07 + t * (px1 - px0 - w * 0.14)
        y = py0 + w * 0.53 - math.sin(t * math.pi * 2.6) * w * 0.045 * (1 - t * 0.4) + t * w * 0.02
        pts.append((x * k, y * k))
    pd.line(pts, fill=(0, 102, 230, 255), width=int(w * 0.03 * k), joint="curve")
    # линия под подписью
    ly = py0 + w * 0.62
    pd.rounded_rectangle([(px0 + w * 0.06) * k, (ly - lw / 2) * k, (px1 - w * 0.06) * k, (ly + lw / 2) * k],
                         radius=lw / 2 * k, fill=lc)
    paper = paper.resize((S, S), Image.LANCZOS)
    # мягкая тень листа
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.paste((0, 40, 110, 90), (0, int(w * 0.012)), paper.split()[3].filter(ImageFilter.GaussianBlur(10)))
    img = Image.alpha_composite(img, shadow)
    img = Image.alpha_composite(img, paper)
    return img


def main():
    mac = render()
    mac.save(os.path.join(A, "PdfTool.icns"), sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256),
                                                        (512, 512), (1024, 1024)])
    mac.save(os.path.join(A, "PdfTool_1024.png"))
    # Windows: без полей и тени (значки Windows занимают почти весь квадрат)
    win = render(full_bleed=True)
    inset = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    inset.paste(win.resize((928, 928), Image.LANCZOS), (48, 48))
    inset.save(os.path.join(A, "PdfTool.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                       (128, 128), (256, 256)])
    # логотип в боковой панели: сам «квадрат», без полей, 96x96 (для Retina при 30 pt)
    logo = mac.crop((100, 100, 924, 924)).resize((96, 96), Image.LANCZOS)
    logo.save(os.path.join(A, "icons", "app-logo.png"))
    print("ok")


if __name__ == "__main__":
    main()
