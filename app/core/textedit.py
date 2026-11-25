"""Правки в координатах неповёрнутой PDF-страницы. Предпросмотр и запись используют один алгоритм."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache

import pymupdf as pdf

from .pdfutil import Cancelled, PdfError, check_output_path, open_pdf, save_doc
from .resources import resource


@dataclass(frozen=True)
class Target:
    rect: tuple
    text: str = ""
    font: str = "Inter"
    size: float = 12
    color: int = 0
    baseline: float | None = None
    horizontal: bool = True
    # Узкая полоса пересекает знаки строки, не захватывая выносные элементы соседней.
    cuts: tuple = ()


@dataclass(frozen=True)
class Edit:
    page: int
    mode: str
    target: Target
    text: str
    font: str = "Как в документе"
    size: float = 12
    color: int = 0
    align: int = 0
    font_data: bytes | None = None


def bounds(page):
    return page.rect * page.derotation_matrix


def spans(page):
    flags = pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES
    result = []
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        for line in block.get("lines", ()):
            for s in line["spans"]:
                chars = s["chars"]
                text = "".join(c["c"] for c in chars)
                if not text.strip():
                    continue
                cuts = []
                for char in chars:
                    box = pdf.Rect(char["bbox"])
                    if box.is_empty:
                        continue
                    y = (box.y0 + box.y1) / 2
                    cuts.append(((box.x0 + box.x1) / 2, y))
                result.append(Target(tuple(s["bbox"]), text, s["font"], s["size"], s["color"],
                                     s["origin"][1], tuple(line["dir"]) == (1, 0), tuple(cuts)))
    return result


def _font_key(name):
    return name.split("+")[-1].replace("-", "").replace(" ", "").lower()


@lru_cache(maxsize=4)
def _inter(bold):
    filename = "Inter-SemiBold.ttf" if bold else "Inter-Regular.ttf"
    with open(resource("ui", "assets", "fonts", filename), "rb") as stream:
        buf = stream.read()
    return pdf.Font(fontbuffer=buf), buf


@lru_cache(maxsize=4)
def _custom_font(data):
    try:
        return pdf.Font(fontbuffer=data)
    except (ValueError, RuntimeError) as exc:
        raise PdfError("Не удалось прочитать файл шрифта.") from exc


def choose_font(page, edit):
    wanted = edit.target.font if edit.font == "Как в документе" else edit.font
    required = set(edit.text) - {"\n"}
    key = _font_key(wanted)

    def covers(font):
        return all(font.has_glyph(ord(c)) for c in required)

    if edit.font_data is not None:
        font = _custom_font(edit.font_data)
        if not covers(font):
            raise PdfError("Выбранный шрифт не содержит всех введённых символов.")
        return font, edit.font_data, font.name
    if edit.font == "Как в документе":
        for xref, _, _, base, *_ in page.get_fonts():
            if xref and _font_key(base) == key:
                _, _, _, buf = page.parent.extract_font(xref)
                if buf:
                    try:
                        font = pdf.Font(fontbuffer=buf)
                    except (RuntimeError, ValueError):
                        continue
                    if covers(font):
                        return font, buf, font.name
    aliases = {"helvetica": "helv", "timesroman": "tiro", "courier": "cour",
               "helveticabold": "hebo", "helveticaoblique": "heit", "timesbold": "tibo",
               "timesitalic": "tiit", "courierbold": "cobo"}
    if key in aliases and edit.text.isascii():
        alias = aliases[key]
        return pdf.Font(alias), None, alias
    font, buf = _inter("bold" in key or "semibold" in key)
    if not covers(font):
        raise PdfError("В выбранном шрифте нет одного из символов. Выберите другой шрифт.")
    return font, buf, font.name


def line_limits(page, target, neighbours):
    r = pdf.Rect(target.rect)
    left, right = bounds(page).x0, bounds(page).x1
    y = (r.y0 + r.y1) / 2
    for other in neighbours:
        box = pdf.Rect(other.rect)
        if box.y0 < y < box.y1:
            if box.x0 >= r.x1 - .2:
                right = min(right, box.x0)
            if box.x1 <= r.x0 + .2:
                left = max(left, box.x1)
    # Учитываем вертикальные границы ячеек, даже когда соседняя ячейка пуста.
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                a, b = item[1:3]
                xs = [a.x] if abs(a.x - b.x) < .5 and min(a.y, b.y) <= y <= max(a.y, b.y) else []
            elif item[0] == "re" and item[1].y0 <= y <= item[1].y1:
                xs = (item[1].x0, item[1].x1)
            else:
                continue
            for x in xs:
                if x >= r.x1 - .1:
                    right = min(right, x)
                if x <= r.x0 + .1:
                    left = max(left, x)
    return left, right


def _protect(page, areas):
    for annot in page.annots() or ():
        if annot.type[0] == pdf.PDF_ANNOT_REDACT:
            raise PdfError("На странице есть неприменённые пометки удаления. Завершите их перед правкой.")
    protected = [w.rect for w in page.widgets() or ()]
    protected.extend(link["from"] * page.derotation_matrix for link in page.get_links())
    if any(a.intersects(p) for a in areas for p in protected):
        raise PdfError("Область пересекает ссылку или поле формы. Уточните выделение.")


def apply(page, edit):
    if edit.mode == "field":
        for field in page.widgets() or ():
            if field.field_name == edit.target.text and field.rect == pdf.Rect(edit.target.rect):
                set_field(field, edit.text)
                return
        raise PdfError("Поле формы изменилось.")
    r = pdf.Rect(edit.target.rect)
    if r.is_empty or not bounds(page).contains(r):
        raise PdfError("Область выходит за страницу.")
    font, buf, fontname = choose_font(page, edit)
    size = edit.size
    if not 1 <= size <= 400:
        raise PdfError("Размер текста должен быть от 1 до 400 пунктов.")
    rgb = tuple(((edit.color >> shift) & 255) / 255 for shift in (16, 8, 0))
    areas = []
    if edit.mode == "line":
        if not edit.target.horizontal:
            raise PdfError("Для повёрнутой строки используйте выделение области.")
        others = spans(page)
        if edit.target not in others:
            raise PdfError("Текст страницы изменился. Выберите строку заново.")
        others.remove(edit.target)
        left, right = line_limits(page, edit.target, others)
        width = font.text_length(edit.text, fontsize=size)
        x = (r.x0, (r.x0 + r.x1 - width) / 2, r.x1 - width)[edit.align]
        y = edit.target.baseline
        ink = pdf.Rect(x, y - size * .8, x + width, y + size * .2)
        if ("\n" in edit.text or x < left - .1 or x + width > right + .1
                or ink.y0 < 0 or ink.y1 > bounds(page).y1
                or any(ink.intersects(pdf.Rect(t.rect)) for t in others if not r.intersects(pdf.Rect(t.rect)))):
            raise PdfError("Текст не помещается. Уменьшите размер или выберите область для абзаца.")
        areas = [pdf.Rect(cx-.02, cy-.02, cx+.02, cy+.02) for cx, cy in edit.target.cuts]
        if any(a.intersects(pdf.Rect(t.rect)) for t in others for a in areas):
            raise PdfError("В этой области строки перекрываются. Выделите область вручную.")
    elif edit.mode in ("block", "scan"):
        if edit.mode == "block" and any(r.intersects(pdf.Rect(t.rect)) and
                                        not r.contains(pdf.Rect(t.rect)) for t in spans(page)):
            raise PdfError("Выделите строки абзаца целиком.")
        areas = [r]
    elif edit.mode != "add":
        raise PdfError("Неизвестная операция с текстом.")

    # После удаления текста MuPDF может пересоздать таблицу шрифтов страницы.
    if buf:
        fontname = "PT" + hashlib.sha256(buf).hexdigest()[:16]

    def layout():
        if buf:
            page.insert_font(fontname=fontname, fontbuffer=buf)
        shape = page.new_shape()
        if edit.mode == "line":
            shape.insert_text((x, y), edit.text, fontname=fontname, fontsize=size, color=rgb)
        elif shape.insert_textbox(r, edit.text, fontname=fontname, fontsize=size,
                                  color=rgb, align=edit.align, rotate=page.rotation) < 0:
            raise PdfError("Текст не помещается в рамку. Увеличьте область или уменьшите размер.")
        return shape

    shape = layout() if edit.text else None
    if areas:
        _protect(page, areas)
        for a in areas:
            page.add_redact_annot(a, fill=paper_color(page, a) if edit.mode == "scan" else False, cross_out=False)
        page.apply_redactions(images=2 if edit.mode == "scan" else 0, graphics=0, text=0)
        if shape is not None:
            shape = layout()
    if shape is not None:
        shape.commit()
    return font.name


def paper_color(page, rect, margin=6):
    """Цвет бумаги вокруг участка скана: белая заплатка на тонированном скане заметна."""
    import numpy as np
    area = (pdf.Rect(rect) + (-margin, -margin, margin, margin)) & page.rect
    if area.is_empty:
        return (1, 1, 1)
    clip = (area * page.rotation_matrix).normalize()
    inner = (pdf.Rect(rect) * page.rotation_matrix).normalize()
    scale = min(2.0, 600 / max(clip.width, clip.height, 1))
    pix = page.get_pixmap(matrix=pdf.Matrix(scale, scale).prerotate(0), clip=clip, alpha=False,
                          colorspace=pdf.csRGB)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width * 3]
    rgb = rgb.reshape(pix.height, pix.width, 3).astype(float)
    ys, xs = np.mgrid[0:pix.height, 0:pix.width]
    px, py = clip.x0 + (xs + .5) / scale, clip.y0 + (ys + .5) / scale
    ring = ~((px >= inner.x0) & (px <= inner.x1) & (py >= inner.y0) & (py <= inner.y1))
    sample = rgb[ring] if ring.any() else rgb.reshape(-1, 3)
    light = sample.mean(axis=1)
    paper = sample[light >= np.percentile(light, 60)]       # буквы и пыль — темнее бумаги
    return tuple(float(v) / 255 for v in np.median(paper, axis=0))


def file_stamp(path):
    st = os.stat(path)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def arrange(doc, layout):
    if layout is None:
        return
    order = [number for number, _ in layout]
    if order != list(range(len(doc))):
        doc.select(order)
    for index, (_, rotation) in enumerate(layout):
        if rotation:
            page = doc[index]
            page.set_rotation((page.rotation + rotation) % 360)


def draft(path, edits, stamp, layout=None, progress=None):
    if file_stamp(path) != stamp:
        raise PdfError("Исходный файл изменился на диске. Откройте его заново.")
    doc = open_pdf(path)
    try:
        arrange(doc, layout)
        for i, edit in enumerate(edits):
            if progress and progress(i, len(edits)) is False:
                raise Cancelled()
            apply(doc[edit.page], edit)
        if progress and progress(len(edits), len(edits)) is False:
            raise Cancelled()
        return doc
    except Exception:
        doc.close()
        raise


def save(path, out, edits, stamp, layout=None, progress=None):
    check_output_path(out, (path,))
    with draft(path, edits, stamp, layout, progress) as doc:
        save_doc(doc, out, inputs=(path,))


def set_field(field, value):
    if field.field_flags & pdf.PDF_FIELD_IS_READ_ONLY:
        raise PdfError("Поле доступно только для чтения.")
    if field.field_type == pdf.PDF_WIDGET_TYPE_CHECKBOX:
        field.field_value = field.on_state() if value == "1" else "Off"
    elif field.field_type == pdf.PDF_WIDGET_TYPE_TEXT:
        if field.text_maxlen and len(value) > field.text_maxlen:
            raise PdfError(f"В поле помещается до {field.text_maxlen} символов.")
        field.field_value = value
    elif field.field_type in (pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
        if value not in field.choice_values:
            raise PdfError("Выберите значение из списка формы.")
        field.field_value = value
    else:
        raise PdfError("Этот тип поля доступен только для просмотра.")
    field.update()


def recognize(page, rect):
    """Распознанный текст участка скана; строки — в порядке сверху вниз, как на странице."""
    from .ocr import recognize as run
    return run(page, rect)
