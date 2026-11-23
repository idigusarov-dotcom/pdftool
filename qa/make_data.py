"""Тестовые данные для проверки на Windows (всё синтетическое, реальные сканы в репозиторий не кладём)."""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "app", "ui", "assets", "fonts", "Inter-Regular.ttf")


def scan_page_jpeg(seed: int, dpi: int = 200, text: str = "", digits: str = "", quality: int = 80,
                   grain: int = 3) -> bytes:
    """Страница A4 «со сканера»: серый шум, строки текста, при желании — синие цифры в зоне номера."""
    rng = np.random.default_rng(seed)
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    small = (232 + rng.integers(-18, 18, (h // grain, w // grain))).clip(0, 255).astype("uint8")
    im = Image.fromarray(small).resize((w, h)).convert("RGB")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, int(dpi * 0.16))
    for k in range(18):
        y = int(h * 0.30) + k * int(dpi * 0.32)
        d.text((int(w * 0.1), y), f"Строка {k + 1} акта, страница {seed} — {text}", fill=(30, 30, 30), font=f)
    if digits:   # синие цифры в зоне номера (DEFAULT_ZONE экрана «Сортировка»: 0.345..0.455 x 0.218..0.256)
        fd = ImageFont.truetype(FONT, int(h * 0.030))
        d.text((int(w * 0.36), int(h * 0.221)), digits, fill=(20, 40, 190), font=fd)
    # «подпись» синими чернилами внизу
    pts = [(int(w * (0.55 + 0.03 * i)), int(h * (0.86 + 0.012 * ((i * 7) % 3 - 1)))) for i in range(8)]
    d.line(pts, fill=(25, 50, 200), width=max(2, dpi // 60))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=quality)
    return b.getvalue()


def scan_pdf(path: str, n: int, dpi: int = 200, digits=None, quality: int = 80, seed0: int = 0,
             grain: int = 3) -> str:
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=595, height=842)
        dg = digits[i] if digits else ""
        p.insert_image(p.rect, stream=scan_page_jpeg(seed0 + i, dpi, f"№{i + 1}", dg, quality, grain))
    doc.save(path)
    doc.close()
    return path


def text_pdf(path: str, n: int, label: str = "Акт кв. № {i}", sign: bool = True, pw: str | None = None) -> str:
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 80), label.format(i=i + 1), fontname="inter", fontfile=FONT, fontsize=14)
        p.draw_line((72, 700), (300, 700), color=(0, 0, 0))
        if sign:
            p.draw_bezier((90, 690), (140, 640), (190, 740), (260, 680), color=(0.1, 0.2, 0.8), width=2)
    if pw:
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw)
    else:
        doc.save(path)
    doc.close()
    return path


def make_all(out: str) -> dict:
    os.makedirs(out, exist_ok=True)
    d = {}
    j = lambda *a: os.path.join(out, *a)  # noqa: E731
    d["acts"] = text_pdf(j("акты кв 1-5.pdf"), 5)
    d["receipts"] = text_pdf(j("расписки кв 1-5.pdf"), 5, "Расписка кв. № {i}", sign=False)
    d["acts_nosign"] = text_pdf(j("акты без подписей.pdf"), 5, sign=False)
    d["pages10"] = text_pdf(j("десять страниц.pdf"), 10, "Страница {i}")
    d["a3"] = text_pdf(j("файл А.pdf"), 3, "А-{i}")
    d["b3"] = text_pdf(j("файл Б.pdf"), 3, "Б-{i}")
    d["c3"] = text_pdf(j("файл В.pdf"), 3, "В-{i}")
    d["password"] = text_pdf(j("с паролем.pdf"), 2, pw="1234")
    with open(j("битый.pdf"), "wb") as f:
        f.write(open(d["a3"], "rb").read()[:700])
    d["broken"] = j("битый.pdf")
    open(j("пустой.pdf"), "wb").close()
    d["empty"] = j("пустой.pdf")
    with open(j("на самом деле текст.pdf"), "w", encoding="utf-8") as f:
        f.write("это не PDF")
    d["notpdf"] = j("на самом деле текст.pdf")
    # сканы с номерами квартир (для «Сортировки»): перемешанный порядок
    nums = ["17", "3", "45", "8", "112", "21", "5", "60"]
    d["sort_scan"] = scan_pdf(j("сканы для сортировки.pdf"), len(nums), digits=nums)
    d["sort_nums"] = nums
    d["scan20"] = scan_pdf(j("скан 20 страниц.pdf"), 20, dpi=300, quality=70, seed0=100)
    # большой: 300+ страниц, 40+ МБ
    d["big"] = scan_pdf(j("большой скан 320 страниц.pdf"), 320, dpi=150, quality=50, seed0=1000, grain=6)
    # картинки
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(j("печать.png"))
    im = Image.open(j("печать.png"))
    dr = ImageDraw.Draw(im)
    dr.ellipse((20, 20, 380, 380), outline=(30, 60, 200, 255), width=14)
    dr.text((110, 180), "ПЕЧАТЬ", fill=(30, 60, 200, 255), font=ImageFont.truetype(FONT, 48))
    im.save(j("печать.png"))
    d["stamp"] = j("печать.png")
    Image.new("RGB", (3000, 2000), (200, 30, 30)).save(j("фото.jpg"), quality=90)
    d["photo"] = j("фото.jpg")
    for k, v in d.items():
        if isinstance(v, str) and os.path.isfile(v):
            print(f"{k:12s} {os.path.getsize(v) / 1e6:8.2f} MB  {os.path.basename(v)}")
    return d


if __name__ == "__main__":
    make_all(sys.argv[1] if len(sys.argv) > 1 else "qa_data")
