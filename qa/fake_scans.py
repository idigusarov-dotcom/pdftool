"""Выдуманные «сканы» актов и расписок для проверок (QA и тесты). Все имена, адреса, организации и номера
придуманы; печать подписана «ВЫМЫШЛЕНО». Похоже на скан: тон бумаги, шум, пыль, тень у края, лёгкий поворот,
JPEG, 200 dpi. Раскладка совпадает с рамками программы по умолчанию (номер квартиры, подпись, строка подписи).

    from qa.fake_scans import Scenario, make_pair
    meta = make_pair(dir, Scenario(apartments=["14", "27"], receipts=["27", "14"]))
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
APP_FONTS = os.path.join(os.path.dirname(HERE), "app", "ui", "assets", "fonts")
SERIF = os.path.join(APP_FONTS, "Inter-Regular.ttf")
SERIF_B = os.path.join(APP_FONTS, "Inter-SemiBold.ttf")
HANDS = [os.path.join(FONTS, "MarckScript-Regular.ttf"), os.path.join(FONTS, "BadScript-Regular.ttf"),
         os.path.join(FONTS, "Caveat.ttf")]
DPI = 200
W, H = int(8.27 * DPI), int(11.69 * DPI)
INK = (28, 46, 150)
BLACK_INK = (35, 35, 40)
ADDR = "ул. Вымышленная, д. 7, корп. 2"
DATE = "12.11.2026"
SURNAMES = ["Петрова А. И.", "Соколов В. Н.", "Морозова Е. П.", "Гаврилов Д. С.", "Белова Н. Г.", "Орлов К. М.",
            "Зайцева Т. Р.", "Лебедев Ю. А.", "Комарова О. В.", "Титов Г. Л.", "Фомина С. Д.", "Жуков П. Е."]
FULL = {"Петрова А. И.": "Петрова Анна Ильинична", "Соколов В. Н.": "Соколов Виктор Николаевич",
        "Морозова Е. П.": "Морозова Елена Павловна", "Гаврилов Д. С.": "Гаврилов Денис Сергеевич",
        "Белова Н. Г.": "Белова Нина Григорьевна", "Орлов К. М.": "Орлов Кирилл Маркович",
        "Зайцева Т. Р.": "Зайцева Тамара Романовна", "Лебедев Ю. А.": "Лебедев Юрий Антонович",
        "Комарова О. В.": "Комарова Ольга Викторовна", "Титов Г. Л.": "Титов Глеб Львович",
        "Фомина С. Д.": "Фомина Софья Дмитриевна", "Жуков П. Е.": "Жуков Павел Егорович"}
# где что лежит на странице (доли), для проверок результата
ACT_SIG = (0.50, 0.785, 0.70, 0.835)          # подпись жильца в акте
REC_SIG_LINE = (0.40, 0.645, 0.83)            # строка «Подпись собственника» в расписке: x0, y, x1


def fnt(path, px):
    return ImageFont.truetype(path, int(px))


def P(x, y):
    return int(x * W), int(y * H)


def printed(d, xy, text, size=0.0115, bold=False):
    d.text(P(*xy), text, fill=(25, 25, 25), font=fnt(SERIF_B if bold else SERIF, size * H))


def line(d, x0, x1, y, w=2):
    d.line([P(x0, y), P(x1, y)], fill=(40, 40, 40), width=w)


def hand(layer, xy, text, rng, size=0.022, font=None, color=INK, slant=True):  # noqa: C901
    """Рукописный текст: шрифт-«пропись» + дрожание каждой буквы, наклон, неровный нажим."""
    f = fnt(font or HANDS[rng.integers(0, 2)], size * H)
    x, y = P(*xy)
    for ch in text:
        if ch == " ":
            x += int(size * H * 0.3)
            continue
        bb = f.getbbox(ch)
        cw, chh = bb[2] + 6, bb[3] + 12
        g = Image.new("L", (cw * 2, chh * 2), 0)
        ImageDraw.Draw(g).text((cw // 2, chh // 2), ch, fill=255, font=f)
        g = g.rotate(float(rng.normal(0, 4)) + (6 if slant else 0), resample=Image.BICUBIC)
        a = np.asarray(g, np.float32) / 255 * rng.uniform(0.75, 1.0)
        col = np.array(color) + rng.integers(-10, 10, 3)
        stamp_rgba(layer, x - cw // 2, y - chh // 2 + int(rng.normal(0, 2)), a, col)
        x += int(f.getlength(ch) * rng.uniform(0.9, 1.05))
    return x / W


def stamp_rgba(layer, x, y, alpha, col):
    h, w = alpha.shape
    L = layer
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    reg = L[y0:y1, x0:x1]
    L[y0:y1, x0:x1] = reg * (1 - a) + np.array(col, np.float32) * a


def signature(layer, x0, y0, w, h, rng, faint_tail=False, color=INK, letters="Ав"):
    """Росчерк: заглавные буквы прописью + петли (трохоида) + подчёркивание, нажим меняется."""
    hand(layer, (x0 / W, (y0 - 0.35 * h) / H), letters, rng, size=h / H * 1.05, font=HANDS[1], color=color)
    k = rng.uniform(13, 18)
    r = h * rng.uniform(0.35, 0.5)
    path = []
    for t in np.linspace(0, 1, 160):
        path.append((x0 + 0.35 * w + 0.6 * w * t - r * 0.8 * math.sin(k * t), y0 + 0.4 * h + r * math.cos(k * t)
                     - 0.3 * h * t))
    draw_path(layer, path, rng, color, base=4.2)
    ux = np.linspace(x0 - 0.05 * w, x0 + 1.0 * w, 50)
    draw_path(layer, [(x, y0 + 0.85 * h - 0.5 * h * (i / 50) ** 2) for i, x in enumerate(ux)], rng, color, base=3.4)
    if faint_tail:   # бледный «хвост» — ручка почти не писала; автоматика его теряет
        tx = np.linspace(x0 + 1.0 * w, x0 + 1.3 * w, 40)
        draw_path(layer, [(x, y0 + 0.75 * h - 0.6 * h * math.sin(i / 9)) for i, x in enumerate(tx)], rng,
                  (178, 184, 206), base=2.8, press=False)


def draw_path(layer, path, rng, color, base=3.0, press=True):
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    pad = 12
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(W, int(max(xs)) + pad), min(H, int(max(ys)) + pad)
    if x1 <= x0 or y1 <= y0:
        return
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(m)
    n = len(path)
    for i in range(n - 1):
        p = 0.55 + 0.45 * math.sin(math.pi * i / n) if press else 1.0
        wdt = max(1, int(base * p * W / 1654 + rng.normal(0, 0.3)))
        d.line([(int(path[i][0]) - x0, int(path[i][1]) - y0), (int(path[i + 1][0]) - x0, int(path[i + 1][1]) - y0)],
               fill=255, width=wdt)
    a = np.asarray(m.filter(ImageFilter.GaussianBlur(0.8)), np.float32) / 255
    a *= np.clip(rng.normal(0.92, 0.08, a.shape), 0.6, 1)
    reg = layer[y0:y1, x0:x1]
    layer[y0:y1, x0:x1] = reg * (1 - a[..., None]) + np.array(color, np.float32) * a[..., None]


def round_stamp(layer, cx, cy, r, rng, text="ООО «РОМАШКА-СТРОЙ» * ДЛЯ ДОКУМЕНТОВ * ", center="ВЫМЫШЛЕНО"):
    size = int(2.3 * r)
    im = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(im)
    c = size // 2
    d.ellipse((c - r, c - r, c + r, c + r), outline=255, width=max(3, r // 28))
    d.ellipse((c - 0.72 * r, c - 0.72 * r, c + 0.72 * r, c + 0.72 * r), outline=255, width=max(2, r // 45))
    f = fnt(SERIF_B, r * 0.17)
    for i, ch in enumerate(text):
        ang = -math.pi / 2 + 2 * math.pi * i / len(text)
        g = Image.new("L", (int(r * 0.3),) * 2, 0)
        ImageDraw.Draw(g).text((int(r * 0.07), 0), ch, fill=255, font=f)
        g = g.rotate(-math.degrees(ang) - 90, resample=Image.BICUBIC)
        px, py = c + 0.86 * r * math.cos(ang) - g.width / 2, c + 0.86 * r * math.sin(ang) - g.height / 2
        im.paste(255, (int(px), int(py)), g)
    f2 = fnt(SERIF_B, r * 0.2)
    tw = d.textlength(center, font=f2)
    d.text((c - tw / 2, c - r * 0.12), center, fill=255, font=f2)
    im = im.rotate(float(rng.uniform(-25, 25)), resample=Image.BICUBIC)
    a = np.asarray(im, np.float32) / 255
    a *= np.clip(rng.normal(0.8, 0.25, a.shape), 0, 1)           # неровный оттиск
    a *= 0.85
    stamp_rgba(layer, int(cx - size / 2), int(cy - size / 2), a, (70, 60, 170))


def finish(layer, rng, angle):
    """Бумага, шум, лёгкий поворот, размытие, JPEG — как со сканера."""
    img = Image.fromarray(np.clip(layer, 0, 255).astype(np.uint8))
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(236, 234, 226))
    a = np.asarray(img, np.float32)
    yy, xx = np.mgrid[:H, :W]
    a *= (1 - 0.03 * (xx / W) - 0.02 * (yy / H))[..., None]
    a += rng.normal(0, 6.5, a.shape)
    a[:, :18] *= 0.82                                   # тень у края, как от крышки сканера
    for _ in range(40):                                   # пылинки
        y, x = rng.integers(0, H), rng.integers(0, W)
        a[y:y + 3, x:x + 3] *= 0.6
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=68)
    return buf.getvalue()


def paper(rng):
    base = np.array([243, 241, 233], np.float32)
    return np.broadcast_to(base, (H, W, 3)).copy()




def illegible(layer, xy, rng, color=INK):
    """Неразборчивый «номер»: каракули вместо цифр."""
    x0, y0 = P(*xy)
    path = [(x0 + i * 4 + rng.normal(0, 2), y0 + 25 + 22 * math.sin(i / 2.0) + rng.normal(0, 4)) for i in range(28)]
    draw_path(layer, path, rng, color, base=3.5)


@dataclass
class Scenario:
    apartments: list                       # номера в актах (по страницам актов); None — номера нет
    receipts: list                         # номера в расписках (по страницам)
    ink: str = "blue"                      # blue / black — чем подписывались жильцы
    stamps: tuple = (0, 2, 4)              # на каких актах печать подрядчика
    illegible_acts: tuple = ()             # акты с неразборчивым номером
    pages_per_act: int = 1                 # 2 — перед каждой страницей с подписью есть страница текста
    seed: int = 2026
    names: dict = field(default_factory=dict)   # номер -> «Фамилия И. О.»


def name_for(sc: Scenario, apt) -> str:
    if apt in sc.names:
        return sc.names[apt]
    k = (int(apt) if str(apt).isdigit() else len(str(apt))) % len(SURNAMES)
    return SURNAMES[k]


def act_page(sc: Scenario, k, apt, rng):
    short = name_for(sc, apt if apt is not None else str(k))
    full = FULL[short]
    color = INK if sc.ink == "blue" else BLACK_INK
    L = paper(rng)
    im = Image.fromarray(L.astype(np.uint8))
    d = ImageDraw.Draw(im)
    printed(d, (0.44, 0.06), "АКТ", 0.016, True)
    printed(d, (0.22, 0.085), "о выполнении работ по замене стояков отопления (образец)", 0.012)
    printed(d, (0.08, 0.125), "г. Москва")
    line(d, 0.68, 0.90, 0.145)
    printed(d, (0.08, 0.17), "Мы, нижеподписавшиеся, представитель подрядчика ООО «Ромашка-Строй» (вымышленная")
    printed(d, (0.08, 0.19), "организация) и собственник квартиры, составили настоящий акт о том, что работы")
    printed(d, (0.08, 0.21), "по замене стояков выполнены в полном объёме, в срок и с надлежащим качеством.")
    printed(d, (0.08, 0.25), "Адрес: " + ADDR + " (адрес вымышлен)")
    printed(d, (0.08, 0.338), "Квартира №")
    line(d, 0.19, 0.42, 0.365)
    printed(d, (0.08, 0.40), "Собственник:")
    line(d, 0.20, 0.90, 0.42)
    printed(d, (0.08, 0.60), "Подрядчик: ООО «Ромашка-Строй»")
    line(d, 0.40, 0.62, 0.635)
    printed(d, (0.14, 0.775), "В чем и расписываемся:")
    printed(d, (0.14, 0.808), "1.")
    line(d, 0.17, 0.45, 0.83)
    line(d, 0.48, 0.70, 0.83)
    printed(d, (0.20, 0.835), "(Ф. И. О.)", 0.009)
    printed(d, (0.55, 0.835), "(подпись)", 0.009)
    L = np.asarray(im, np.float32).copy()
    hand(L, (0.70, 0.128), DATE, rng, 0.02, color=color)
    if apt is not None and k in sc.illegible_acts:
        illegible(L, (0.23, 0.333), rng, color)
    elif apt is not None:
        hand(L, (0.23, 0.333), str(apt), rng, 0.03, font=HANDS[2], slant=False, color=color)
    hand(L, (0.22, 0.395), full, rng, 0.022, color=color)
    signature(L, 0.43 * W, 0.595 * H, 0.15 * W, 0.03 * H, rng, letters="Ив")       # подпись подрядчика (синяя)
    if k in sc.stamps:
        round_stamp(L, 0.56 * W, 0.62 * H, int(0.085 * W), rng)
    hand(L, (0.18, 0.793), short, rng, 0.024, color=color)
    signature(L, 0.50 * W, 0.795 * H, 0.13 * W, 0.028 * H, rng, letters=short[:2], color=color)
    return finish(L, rng, float(rng.uniform(-0.6, 0.6)))


def text_page(rng, k):
    """Страница-«оборот» без номера и подписи (многостраничный акт)."""
    L = paper(rng)
    im = Image.fromarray(L.astype(np.uint8))
    d = ImageDraw.Draw(im)
    printed(d, (0.30, 0.06), f"Приложение к акту (образец), лист {k + 1}", 0.013, True)
    for i in range(30):
        printed(d, (0.08, 0.12 + 0.025 * i), "Перечень работ: демонтаж, монтаж, опрессовка, уборка помещения.")
    return finish(np.asarray(im, np.float32).copy(), rng, float(rng.uniform(-0.5, 0.5)))


def receipt_page(sc: Scenario, k, apt, rng):
    L = paper(rng)
    im = Image.fromarray(L.astype(np.uint8))
    d = ImageDraw.Draw(im)
    printed(d, (0.27, 0.07), "Расписка об отсутствии претензий (образец)", 0.014, True)
    printed(d, (0.12, 0.15), "Я,")
    line(d, 0.15, 0.90, 0.17)
    printed(d, (0.12, 0.225), "Собственник кв. №")
    line(d, 0.34, 0.47, 0.25)
    printed(d, (0.49, 0.225), ", дома по адресу: " + ADDR)
    for i, t in enumerate(["подтверждаю, что произведена замена стояков холодного и горячего водоснабжения,",
                           "отопления и канализации. Работы выполнены качественно, в квартире убрано.",
                           "Претензий к выполненным работам не имею."]):
        printed(d, (0.12, 0.31 + 0.025 * i), t)
    printed(d, (0.12, 0.62), "Подпись собственника:")
    line(d, 0.40, 0.83, 0.645)
    printed(d, (0.12, 0.72), "Дата:")
    line(d, 0.20, 0.42, 0.74)
    L = np.asarray(im, np.float32).copy()
    if apt is not None:
        hand(L, (0.17, 0.145), FULL[name_for(sc, apt)], rng, 0.022)
        hand(L, (0.36, 0.219), str(apt), rng, 0.03, font=HANDS[2], slant=False)
    hand(L, (0.22, 0.712), DATE, rng, 0.02)
    return finish(L, rng, float(rng.uniform(-0.6, 0.6)))


def to_pdf(pages, path):
    doc = pymupdf.open()
    for jpg in pages:
        pg = doc.new_page(width=595.3, height=841.9)
        pg.insert_image(pg.rect, stream=jpg)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def make_pair(folder: str, sc: Scenario, acts_name="акты.pdf", rec_name="расписки.pdf") -> dict:
    """Пишет акты и расписки; возвращает пути и «правильный ответ» (какая страница акта к какой расписке)."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(sc.seed)
    pages, sig_pages = [], []
    for k, apt in enumerate(sc.apartments):
        if sc.pages_per_act == 2:
            pages.append(text_page(rng, k))
        sig_pages.append(len(pages))
        pages.append(act_page(sc, k, apt, rng))
    recs = [receipt_page(sc, k, apt, rng) for k, apt in enumerate(sc.receipts)]
    acts, rec = os.path.join(folder, acts_name), os.path.join(folder, rec_name)
    to_pdf(pages, acts)
    to_pdf(recs, rec)
    expected = []
    for apt in sc.receipts:
        idx = [i for i, a in enumerate(sc.apartments) if a is not None and a == apt]
        expected.append(sig_pages[idx[0]] if len(idx) == 1 else None)
    return {"acts": acts, "receipts": rec, "expected": expected, "sig_pages": sig_pages}
