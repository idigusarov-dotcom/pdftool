"""Общие функции работы с PDF (PyMuPDF)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pymupdf


@dataclass
class NRect:
    """Прямоугольник в долях страницы (0..1), не зависит от DPI."""
    x0: float
    y0: float
    x1: float
    y1: float

    def normalized(self) -> "NRect":
        return NRect(min(self.x0, self.x1), min(self.y0, self.y1),
                     max(self.x0, self.x1), max(self.y0, self.y1))

    def to_points(self, page_rect: pymupdf.Rect) -> pymupdf.Rect:
        r = self.normalized()
        return pymupdf.Rect(page_rect.x0 + r.x0 * page_rect.width, page_rect.y0 + r.y0 * page_rect.height,
                            page_rect.x0 + r.x1 * page_rect.width, page_rect.y0 + r.y1 * page_rect.height)

    def to_pixels(self, w: int, h: int) -> tuple[int, int, int, int]:
        r = self.normalized()
        return (max(0, int(round(r.x0 * w))), max(0, int(round(r.y0 * h))),
                min(w, int(round(r.x1 * w))), min(h, int(round(r.y1 * h))))

    @staticmethod
    def from_points(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> "NRect":
        return NRect((rect.x0 - page_rect.x0) / page_rect.width, (rect.y0 - page_rect.y0) / page_rect.height,
                     (rect.x1 - page_rect.x0) / page_rect.width, (rect.y1 - page_rect.y0) / page_rect.height)


class PdfError(ValueError):
    """Понятная пользователю ошибка: файл нельзя открыть или сохранить."""


class Cancelled(Exception):
    """Пользователь нажал «Отмена» в окне хода работы — файл не сохраняется."""


def same_file(a: str, b: str) -> bool:
    """Один и тот же файл? На Windows регистр букв и вид слэшей не важны."""
    if not a or not b:
        return False
    na = os.path.normcase(os.path.realpath(os.path.abspath(a)))
    nb = os.path.normcase(os.path.realpath(os.path.abspath(b)))
    if na == nb:
        return True
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def open_pdf(path: str) -> pymupdf.Document:
    """Открывает PDF и проверяет, что с ним можно работать. Иначе — PdfError с понятным текстом."""
    name = os.path.basename(path) or path
    if not path or not os.path.isfile(path):
        raise PdfError(f"Файл «{name}» не найден. Возможно, его переместили или удалили.")
    if os.path.getsize(path) == 0:
        raise PdfError(f"Файл «{name}» пустой (0 байт). Скорее всего, он не докачался или повреждён.")
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        raise PdfError(f"Не удалось открыть «{name}»: файл повреждён или это не PDF.") from e
    if not doc.is_pdf:
        doc.close()
        raise PdfError(f"Файл «{name}» — не PDF.")
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise PdfError(f"Файл «{name}» защищён паролем. Снимите пароль (например, распечатайте в PDF) "
                       "и откройте снова.")
    if len(doc) == 0:
        doc.close()
        raise PdfError(f"В файле «{name}» нет ни одной страницы.")
    try:
        doc[0].bound()
    except Exception as e:  # noqa: BLE001
        doc.close()
        raise PdfError(f"Не удалось прочитать страницы «{name}»: файл повреждён.") from e
    return doc


def render_page(doc: pymupdf.Document, index: int, dpi: int = 150) -> np.ndarray:
    """Растр страницы как RGB-массив (H, W, 3) uint8."""
    pix = doc[index].get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[..., :3].copy()


def safe_output_path(input_path: str, suffix: str, ext: str = ".pdf") -> str:
    """Новое имя файла рядом с исходным, никогда не перезаписывает существующий."""
    base, _ = os.path.splitext(input_path)
    cand = f"{base}_{suffix}{ext}"
    n = 2
    while os.path.exists(cand):
        cand = f"{base}_{suffix}_{n}{ext}"
        n += 1
    return cand


def check_output_path(out_path: str, inputs=()) -> None:
    """Проверки до сохранения: имя задано, папка есть, это не исходный файл."""
    if not out_path or not out_path.strip():
        raise PdfError("Не указано имя файла для сохранения.")
    for p in inputs:
        if p and same_file(p, out_path):
            raise PdfError("Нельзя сохранять поверх исходного файла. Выберите другое имя.")
    d = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(d):
        raise PdfError(f"Папки «{d}» нет. Выберите другую папку для сохранения.")


def save_doc(doc: pymupdf.Document, out_path: str, inputs: list[str] | tuple[str, ...] = ()) -> str:
    """Сохраняет документ; запрещает запись поверх входных файлов.
    Пишет сначала во временный файл рядом и только потом подменяет: при сбое старый файл не портится."""
    check_output_path(out_path, inputs)
    d = os.path.dirname(os.path.abspath(out_path))
    tmp = os.path.join(d, f"~{os.path.basename(out_path)}.{os.getpid()}.tmp")
    try:
        doc.save(tmp, garbage=3, deflate=True)
        os.replace(tmp, out_path)
    except PermissionError as e:
        raise PdfError(f"Не удалось записать «{os.path.basename(out_path)}»: файл открыт в другой программе "
                       "(закройте его) или нет прав на запись в эту папку.") from e
    except OSError as e:
        raise PdfError(f"Не удалось записать «{os.path.basename(out_path)}»: {e.strerror or e}") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out_path


def insert_png(page: pymupdf.Page, display_rect: pymupdf.Rect, png: bytes) -> None:
    """Вставляет картинку так, как её видно на экране, с учётом /Rotate страницы.
    display_rect — в координатах отображаемой (повёрнутой) страницы."""
    rot = page.rotation % 360
    target = display_rect * page.derotation_matrix if rot else display_rect
    page.insert_image(target, stream=png, keep_proportion=False, overlay=True, rotate=rot)
