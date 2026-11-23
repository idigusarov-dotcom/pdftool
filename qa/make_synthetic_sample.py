"""Полностью синтетический образец для тестов «Вырезать и перенести» (qa/samples/source_synthetic.pdf).

Всё нарисовано программно: печатный текст, линии бланка, «почерк» (кривые и шрифт), синяя «подпись»,
сине-фиолетовая круглая печать. Реальных людей, адресов и телефонов нет.
Запуск: python qa/make_synthetic_sample.py"""
from __future__ import annotations

import math
import os
import sys

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "app", "ui", "assets", "fonts", "Inter-Regular.ttf")
OUT = os.path.join(ROOT, "qa", "samples", "source_synthetic.pdf")

BLACK, BLUE, STAMP = (0.08, 0.08, 0.08), (0.12, 0.22, 0.78), (0.33, 0.25, 0.78)


def wave(page, x0, y, x1, amp, color, width, seed):
    """Рукописная «строка»: волнистая линия с петлями — без настоящих букв."""
    pts, n = [], 60
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        yy = y + amp * math.sin(t * 22 + seed) + 0.5 * amp * math.sin(t * 57 + 2 * seed)
        pts.append(pymupdf.Point(x, yy))
    page.draw_polyline(pts, color=color, width=width)


def main(out=OUT):
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    W, H = p.rect.width, p.rect.height
    txt = dict(fontname="inter", fontfile=FONT, color=BLACK)
    p.insert_text((150, 80), "Образец бланка (синтетика)", fontsize=14, **txt)
    p.insert_text((60, 140), "Я,", fontsize=11, **txt)
    p.draw_line((80, 142), (540, 142), color=BLACK, width=0.6)
    wave(p, 95, 132, 330, 4, BLUE, 1.6, 1)                         # синее «ФИО»
    p.insert_text((60, 180), "Собственник кв. №", fontsize=11, **txt)
    p.draw_line((165, 182), (215, 182), color=BLACK, width=0.6)
    p.insert_text((175, 178), "00", fontsize=13, fontname="inter", fontfile=FONT, color=BLUE)
    for k, line in enumerate(("Подтверждаю, что работы по образцу выполнены качественно.",
                              "Это тестовый документ, в нём нет данных реальных людей.")):
        p.insert_text((60, 230 + 18 * k), line, fontsize=11, **txt)
    p.insert_text((60, 330), "Претензий не имею.", fontsize=11, **txt)
    # синяя «подпись»
    sig = [pymupdf.Point(330 + 6 * i, 325 - 14 * math.sin(i / 2.2) - (8 if i % 5 == 0 else 0)) for i in range(18)]
    p.draw_polyline(sig, color=BLUE, width=2)
    # чёрный «почерк» на линии бланка: y 0.545..0.60 страницы, x 0.10..0.53
    y_line = 0.59 * H
    p.draw_line((0.06 * W, y_line), (0.94 * W, y_line), color=BLACK, width=0.7)   # линия бланка (длинная)
    wave(p, 0.12 * W, 0.565 * H, 0.50 * W, 5, BLACK, 1.8, 3)
    p.insert_text((0.12 * W, 0.585 * H), "Согласен", fontsize=15, fontname="inter", fontfile=FONT,
                  color=BLACK)
    # круглая печать внутри x 0.445..0.675, y 0.582..0.738 (не заходит на строку почерка)
    cx, cy, r = 0.56 * W, 0.67 * H, 0.085 * W
    p.insert_text((cx - 70, cy - 18), "М.П.   Исполнитель:", fontsize=11, **txt)   # печатный текст под печатью
    p.draw_circle((cx, cy), r, color=STAMP, width=2.2)
    p.draw_circle((cx, cy), r * 0.72, color=STAMP, width=1.4)
    for i, ch in enumerate("ОБРАЗЕЦ ПЕЧАТИ * ОБРАЗЕЦ ПЕЧАТИ * "):
        a = -math.pi / 2 + i * 2 * math.pi / 34
        p.insert_text((cx + r * 0.86 * math.cos(a) - 3, cy + r * 0.86 * math.sin(a) + 3), ch, fontsize=6,
                      fontname="inter", fontfile=FONT, color=STAMP)
    p.insert_text((cx - 26, cy + 4), "ТЕСТ", fontsize=14, fontname="inter", fontfile=FONT, color=STAMP)
    wave(p, cx - 60, cy + 10, cx + 50, 9, BLUE, 1.8, 5)            # подпись поверх печати
    # дата и «телефон» шрифтом, галочки
    p.insert_text((0.08 * W, 0.86 * H), "01.01.2000   тел. 0-000-000-00-00", fontsize=12, **txt)
    for x in (0.62, 0.70):
        p.draw_polyline([pymupdf.Point(x * W, 0.855 * H), pymupdf.Point(x * W + 6, 0.862 * H),
                         pymupdf.Point(x * W + 18, 0.842 * H)], color=BLACK, width=1.6)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    d.save(out, garbage=3, deflate=True)
    d.close()
    return out


if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else OUT))
