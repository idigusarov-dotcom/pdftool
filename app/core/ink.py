"""Выделение подписи (синие чернила) и удаление белого фона у картинок."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

DEFAULT_INK_RGB = (28, 48, 160)  # единый цвет "шариковой ручки"
BLACK_INK_RGB = (32, 32, 36)     # единый цвет для чёрной ручки / карандаша


def ink_color_for(mode: str):
    """Единый цвет чернил («настоящий цвет» выключен): чёрная ручка остаётся чёрной, а не синей."""
    return BLACK_INK_RGB if mode in ("black", "any") else DEFAULT_INK_RGB


def _rgb_to_hsv_cv(rgb: np.ndarray) -> np.ndarray:
    """HSV в шкале OpenCV (H 0..180, S,V 0..255) без зависимости от cv2."""
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx = f.max(-1)
    mn = f.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = ((g - b)[rm] / d[rm]) % 6
    h[gm] = (b - r)[gm] / d[gm] + 2
    h[bm] = (r - g)[bm] / d[bm] + 4
    h = h * 30.0  # 60 градусов / 2
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0) * 255
    return np.dstack([h, s, mx * 255])


def _paper_ref(rgb: np.ndarray) -> tuple[float, np.ndarray]:
    """Яркость и цвет бумаги (90-й процентиль самого тёмного канала и средний цвет таких пикселей)."""
    x = rgb.astype(np.float32)
    mn = x.min(-1)
    paper_v = float(np.percentile(mn, 90))
    sel = mn >= paper_v - 10
    if int(sel.sum()) < 20:
        sel = mn >= float(np.percentile(mn, 75))
    paper_c = x[sel].mean(0) if sel.any() else x.reshape(-1, 3).mean(0)
    return paper_v, paper_c


def _paper_level(rgb: np.ndarray) -> float:
    return _paper_ref(rgb)[0]


def _vs_paper(rgb: np.ndarray, paper_v: float, paper_c: np.ndarray):
    """Насколько пиксель темнее бумаги и насколько его цвет от неё отличается (бледная ручка на
    жёлтом фоне в min-канале почти как бумага, зато тон другой)."""
    x = rgb.astype(np.float32)
    mn = x.min(-1)
    dark = np.clip((paper_v - 8.0 - mn) / 36.0, 0, 1)
    dist = np.sqrt(((x - paper_c) ** 2).sum(-1))
    chroma = np.clip((dist - 16.0) / 45.0, 0, 1)
    return dark, chroma, mn, x


def blue_ink_alpha(rgb: np.ndarray, sat_min: float = 14, hue_lo: float = 92, hue_hi: float = 145) -> np.ndarray:
    """Маска синих/фиолетовых чернил 0..1 (как в ручной обработке job2).
    Для вырезки с бледной/navy/жёлтой бумагой ink_alpha(mode='blue') берёт расширенный детектор."""
    hsv = _rgb_to_hsv_cv(rgb)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    a = np.clip((S - sat_min) / 40.0, 0, 1) * ((H >= hue_lo) & (H <= hue_hi)) * np.clip((250 - V) / 35.0, 0, 1)
    return a.astype(np.float32)


def _blue_ink_adaptive(rgb: np.ndarray, sat_min: float = 14, hue_lo: float = 88, hue_hi: float = 152,
                   paper_v: float | None = None, paper_c: np.ndarray | None = None) -> np.ndarray:
    """Маска синих/голубых/фиолетовых чернил 0..1. Порог — относительно бумаги, чтобы бледная и
    тёмно-синяя (navy) ручка не пропадали, а желтоватый фон не считался чернилами."""
    hsv = _rgb_to_hsv_cv(rgb)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if paper_v is None or paper_c is None:
        paper_v, paper_c = _paper_ref(rgb)
    dark, chroma, mn, x = _vs_paper(rgb, paper_v, paper_c)
    hue = (H >= hue_lo) & (H <= hue_hi)
    # узкий тон — как в 01ed5db / job2: печатный текст бланка не считаем синей ручкой
    hue_classic = (H >= 92) & (H <= 145)
    sat = np.clip((S - sat_min) / 40.0, 0, 1)
    classic = sat * hue_classic * np.clip((250.0 - V) / 35.0, 0, 1)
    sat_dark = np.clip((S - 6.0) / 28.0, 0, 1) * np.clip((paper_v - 24.0 - mn) / 35.0, 0, 1)
    # фото с телефона почти обесцвечивает синюю ручку: берём отклонение «в синюю сторону» от бумаги
    dev = paper_c - x
    blue_dir = (dev[..., 2] < dev[..., 0] - 3) | (dev[..., 2] < dev[..., 1] - 3)
    toward = chroma * blue_dir.astype(np.float32)
    strength = np.maximum(np.maximum(sat, sat_dark), toward)
    pix_blue = x[..., 2] - 0.5 * (x[..., 0] + x[..., 1])
    paper_blue = float(paper_c[2] - 0.5 * (paper_c[0] + paper_c[1]))
    # серая линия бланка на белой бумаге чуть «синее» фона (dblue ~4); у ручки обычно >10
    bluer = pix_blue > paper_blue + 10
    extra = strength * hue * np.maximum(dark, toward) * bluer.astype(np.float32)
    # обычная шариковая уже видна как в 01ed5db / job2 — оставляем прежний детектор,
    # без каймы и без захвата линий бланка.
    if (classic > 0.3).sum() >= 2500 and float((classic > 0.3).mean()) >= 0.02:
        return classic.astype(np.float32)
    looks_blue = np.maximum(classic, extra)
    # на СИЛЬНО цветной бумаге (жёлтый скан) синяя ручка выглядит коричневой.
    # Включаем этот путь только если фон сам цветной (на кремовой бумаге иначе
    # печатный чёрный текст «синее» желтоватого фона и попадает в подпись).
    p_s = float(_rgb_to_hsv_cv(np.array([[paper_c]], np.uint8))[0, 0, 1])
    from PIL import ImageFilter
    printish = (mn < 55) & (S < 35)
    near_print = np.asarray(Image.fromarray((printish.astype(np.uint8) * 255)).filter(
        ImageFilter.MaxFilter(5))) > 0
    tinted = (p_s > 35) * bluer.astype(np.float32) * np.maximum(dark, chroma) * (S > 20) * (~near_print)
    a = np.maximum(looks_blue, tinted).astype(np.float32)
    # на обычной бумаге снимаем только серые линии (синюю черту подписи не трогаем);
    # на жёлтой линия бланка выглядит коричневой — снимаем все длинные линии.
    a = _strip_form_lines(a, dull=None if p_s > 35 else (S < 40))
    if p_s > 35:
        a = _strip_printed_letters(a)
    return a


INK_MODES = {"blue": "синяя", "black": "чёрная", "any": "любая"}
# для «Вырезать и перенести»: ещё цветная печать и «без удаления фона»
FRAGMENT_MODES = {"blue": "синие чернила", "black": "чёрные чернила / карандаш", "color": "цветная печать (штамп)",
                  "any": "всё, кроме белого фона", "none": "без удаления фона (как есть)"}


def _remove_long_lines(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Убирает длинные горизонтальные и вертикальные отрезки (линии бланка)."""
    out = mask.copy()
    for axis in (1, 0):
        m = mask if axis == 1 else mask.T
        o = out if axis == 1 else out.T
        # длина серии подряд идущих пикселей вдоль оси
        d = np.diff(np.pad(m.astype(np.int8), ((0, 0), (1, 1))), axis=1)
        for r in range(m.shape[0]):
            starts = np.nonzero(d[r] == 1)[0]
            ends = np.nonzero(d[r] == -1)[0]
            for s0, e0 in zip(starts, ends):
                if e0 - s0 >= min_len:
                    o[r, s0:e0] = False
    return out


def _strip_form_lines(a: np.ndarray, dull: np.ndarray | None = None) -> np.ndarray:
    """Убирает длинные горизонтальные и вертикальные линии бланка. Сначала закрываем мелкие
    разрывы (JPEG), иначе линия распадается на короткие куски и остаётся в подписи.
    dull — если задана, линии ищем только среди этих пикселей (серая черта бланка, а не
    насыщенная синяя черта подписи)."""
    from PIL import ImageFilter
    h, w = a.shape
    core = a > 0.3
    closed = np.asarray(Image.fromarray((core.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(3))) > 0
    if dull is not None:
        closed = closed & dull
    lines = closed & ~_remove_long_lines(closed, max(40, int(0.35 * w)))
    if h > 40:
        lines |= closed & ~_remove_long_lines(closed, max(40, int(0.5 * h)))
    lines = np.asarray(Image.fromarray((lines.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(3))) > 0
    return a * ~lines


def ink_alpha(rgb: np.ndarray, mode: str = "blue", sensitivity: float = 0.5) -> np.ndarray:
    """Маска чернил 0..1. mode: blue — синие/голубые/фиолетовые; black — тёмные серые/чёрные
    (линии бланка и печатные буквы убираются); color — цветные чернила и печати, не чёрный текст
    и не бумага; any — тёмные или цветные, линии и печатные буквы бланка у тёмного тоже убираются.
    sensitivity 0..1: выше — берём бледнее. Пороги — от цвета бумаги кадра, не от «почти белого»."""
    sens = float(np.clip(sensitivity, 0, 1))
    paper_v, paper_c = _paper_ref(rgb)
    dark, chroma, mn, _x = _vs_paper(rgb, paper_v, paper_c)
    if mode == "blue":
        return _blue_ink_adaptive(rgb, sat_min=26 - 24 * sens, paper_v=paper_v, paper_c=paper_c)
    if mode == "color":   # цветное и не как бумага; чёрный текст (низкая S) и жёлтый фон не берём
        hsv = _rgb_to_hsv_cv(rgb)
        smin = 40 - 30 * sens
        sat = np.clip((hsv[..., 1] - smin) / 40.0, 0, 1)
        ph = float(_rgb_to_hsv_cv(np.array([[paper_c]], np.uint8))[0, 0, 0])
        hd = np.minimum(np.abs(hsv[..., 0] - ph), 180.0 - np.abs(hsv[..., 0] - ph))
        # красная ручка на жёлтом: разница тона ~20; бумага и коричневая кайма текста — около 0
        hue_sep = np.clip((hd - 8.0) / 14.0, 0, 1)
        a = (sat * np.maximum(dark, chroma) * hue_sep).astype(np.float32)
        return _strip_printed_letters(_strip_form_lines(a))
    # чёрная / любая: темнее бумаги; плюс отличие цвета — карандаш на жёлтом фоне
    margin = 34 - 18 * sens
    a = np.clip((paper_v - margin - mn) / 40.0, 0, 1)
    sat = _rgb_to_hsv_cv(rgb)[..., 1]
    if mode == "black":
        # на жёлтой бумаге чёрная ручка выглядит коричневой (S > 80)
        p_s = float(_rgb_to_hsv_cv(np.array([[paper_c]], np.uint8))[0, 0, 1])
        sat_max = 80.0 + 0.7 * p_s
        a = np.maximum(a, chroma * 0.9) * (sat < sat_max)
        a = _strip_printed_letters(_strip_form_lines(a))
    if mode == "any":
        a = _strip_printed_letters(_strip_form_lines(a))
        a = np.maximum(a, ink_alpha(rgb, "color", sensitivity))
    return a.astype(np.float32)


def _components(mask: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Связные области (8-связность) маски: список (ys, xs). Без OpenCV/SciPy: сначала отрезки подряд идущих
    пикселей по строкам, затем объединение пересекающихся отрезков соседних строк (быстро и на больших масках)."""
    h, w = mask.shape
    if not mask.any():
        return []
    pad = np.zeros((h, w + 2), np.int8)
    pad[:, 1:-1] = mask
    d = np.diff(pad, axis=1)
    runs = []                         # (строка, начало, конец)
    row_runs = []
    for y in range(h):
        st, en = np.nonzero(d[y] == 1)[0], np.nonzero(d[y] == -1)[0]
        row_runs.append(list(range(len(runs), len(runs) + len(st))))
        runs.extend((y, int(a), int(b)) for a, b in zip(st, en))
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for y in range(1, h):
        prev, cur = row_runs[y - 1], row_runs[y]
        j = 0
        for i in cur:
            _, a, b = runs[i]
            while j < len(prev) and runs[prev[j]][2] < a:     # 8-связность: касание по диагонали
                j += 1
            k = j
            while k < len(prev) and runs[prev[k]][1] <= b:
                ri, rk = find(i), find(prev[k])
                if ri != rk:
                    parent[ri] = rk
                k += 1
    groups: dict = {}
    for i, (y, a, b) in enumerate(runs):
        groups.setdefault(find(i), []).append((y, a, b))
    out = []
    for rs in groups.values():
        ys = np.concatenate([np.full(b - a, y) for y, a, b in rs])
        xs = np.concatenate([np.arange(a, b) for y, a, b in rs])
        out.append((ys, xs))
    return out


def _strip_printed_letters(a: np.ndarray) -> np.ndarray:
    """Убирает отдельные печатные буквы бланка (ровные, похожего роста, стоят в строку).
    Рукописная строка обычно один-два связных штриха, её не трогаем."""
    from PIL import ImageFilter
    core = a > 0.45
    comps = _components(core)
    if len(comps) < 5:
        return a
    largest_h = max(int(ys.max() - ys.min() + 1) for ys, xs in comps)
    glyphs = []
    for ys, xs in comps:
        h = int(ys.max() - ys.min() + 1)
        w = int(xs.max() - xs.min() + 1)
        n = len(ys)
        if 6 <= h <= 52 and w <= 3.0 * h + 8 and n >= 0.25 * h * w and n <= 1800:
            glyphs.append((float(ys.mean()), h, ys, xs))
    if len(glyphs) < 6:
        return a
    glyphs.sort(key=lambda g: g[0])
    used = [False] * len(glyphs)
    drop = np.zeros(a.shape, bool)
    for i, g in enumerate(glyphs):
        if used[i]:
            continue
        cluster = [g]
        used[i] = True
        for j in range(i + 1, len(glyphs)):
            if used[j]:
                continue
            if abs(glyphs[j][0] - g[0]) <= max(4.0, 0.45 * g[1]):
                cluster.append(glyphs[j])
                used[j] = True
        hs = np.array([c[1] for c in cluster], float)
        med = float(np.median(hs))
        close = [c for c, h in zip(cluster, hs) if abs(h - med) <= 0.5 * med]
        # одна более высокая буква не должна спасать всю печатную строку
        # печатные буквы заметно ниже, чем основной штрих подписи; куски самой подписи не трогаем
        if len(close) >= 6 and med <= 0.5 * largest_h - 2:
            for _, _, ys, xs in close:
                drop[ys, xs] = True
    if drop.any():
        grow = np.asarray(Image.fromarray((drop.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(3))) > 0
        a = a * ~grow
    return a


def stroke_width(core: np.ndarray) -> float:
    """Толщина штриха в пикселях: 2 * площадь / периметр (для линии ≈ её ширина)."""
    from PIL import ImageFilter
    area = int(core.sum())
    if area < 20:
        return 2.0
    er = np.asarray(Image.fromarray((core * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))) > 0
    perim = int((core & ~er).sum())
    return float(np.clip(2.0 * area / max(perim, 1), 1.5, 12.0))


def _beside_letters(kept: np.ndarray, ys, xs, sw: float, reach: int) -> bool:
    """Точка стоит рядом с буквой, как точка после инициала: слева/справа (в пределах reach) есть
    заметный кусок чернил, который поднимается выше точки (буква выше точки) и опускается примерно до
    её низа (точка — на строке, а не висит над или под надписью). Цветная «бахрома» сжатия у края
    печатного текста и пыль у кромки этому не отвечают."""
    h, w = kept.shape
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    win = kept[max(0, y0 - int(8 * sw) - reach):min(h, y1 + reach + 1), max(0, x0 - reach):min(w, x1 + reach + 1)]
    if win.sum() < 4 * sw * sw:
        return False
    rows = np.nonzero(win.any(1))[0] + max(0, y0 - int(8 * sw) - reach)
    return rows.min() <= y0 - 2 * sw and rows.max() >= y1 - 2 * sw


def _remove_specks(a: np.ndarray, radius: int = 7, min_pixels: int = 30) -> np.ndarray:
    """Убирает одиночные точки (пыль скана): оставляем чернила только там, где в окне
    (2*radius+1)^2 набирается не меньше min_pixels «уверенных» пикселей. Без OpenCV.

    Но точка, поставленная ручкой рядом с надписью (точки после инициалов «Е. П.», точка над «i»),
    по плотности неотличима от пыли — её возвращаем, если она: (1) не мельче следа ручки
    (площадь от трети квадрата толщины штриха), (2) компактная и круглая — не длиннее ~2,5 толщин, не тоньше
    0,5 толщины, вытянутость до 2,5 раз (обрывки линий бланка и волоски так не выглядят), (3) уверенно
    закрашена, (4) лежит вплотную к уже принятым чернилам (не дальше ~4 толщин штриха), (5) стоит «на строке»
    рядом с буквой (см. _beside_letters).
    Пыль посреди пустого места по-прежнему убирается."""
    from PIL import ImageFilter
    # бледная ручка: слабая альфа, порог 0.5 съедает штрих; обычная шариковая при 0.35
    # оставляет кайму и обрывки линии бланка.
    # бледная ручка даёт мало «уверенных» пикселей — порог 0.5 съедает штрих;
    # обычная шариковая (тысячи пикселей >0.5) при 0.35 оставляет кайму бланка.
    core_t = 0.5 if int((a > 0.5).sum()) >= 2000 else 0.35
    core = a > core_t
    win = (2 * radius + 1) ** 2
    dens = Image.fromarray((core * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(radius))
    ok = np.asarray(dens, np.float32) / 255.0 * win >= min_pixels
    keep = np.asarray(Image.fromarray((ok * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    lost = core & ~keep
    kept = core & keep
    if lost.any() and kept.sum() >= min_pixels:
        sw = stroke_width(kept)
        reach = int(round(4 * sw + 3))
        near = np.asarray(Image.fromarray((kept * 255).astype(np.uint8)).filter(
            ImageFilter.MaxFilter(2 * reach + 1))) > 0
        comps = _components(lost & near) if (lost & near).sum() < 20000 else []
        rescue = np.zeros_like(core)
        for ys, xs in comps:
            n = len(ys)
            bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            if (n >= max(4, 0.33 * sw * sw) and max(bw, bh) <= 2.5 * sw + 2
                    and min(bw, bh) >= max(2, 0.5 * sw) and max(bw, bh) <= 2.5 * min(bw, bh)
                    and n >= 0.5 * bw * bh and float(a[ys, xs].mean()) >= 0.6
                    and _beside_letters(kept, ys, xs, sw, reach)):
                rescue[ys, xs] = True
        if rescue.any():
            grow = np.asarray(Image.fromarray((rescue * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
            keep = keep | grow
    return a * keep


def extract_ink(rgb: np.ndarray, color=None, uniform: bool = True,
                mode: str = "blue", sensitivity: float = 0.5, exclude: np.ndarray | None = None) -> np.ndarray:
    """Возвращает RGBA (H,W,4) uint8: только чернила, фон прозрачный.
    exclude — маска пикселей, которые точно не подпись (печатный текст бланка), они убираются."""
    if color is None:
        color = ink_color_for(mode)
    a = ink_alpha(rgb, mode, sensitivity)
    if exclude is not None and exclude.shape == a.shape:
        a = a * ~exclude
    a = _remove_specks(a)
    a = np.power(a, 0.7)
    v = a[a > 0.4]
    if v.size:
        gain = min(2.5, 0.95 / max(float(np.percentile(v, 95)), 1e-3))
        a = np.clip(a * gain, 0, 1)
    a[a < 0.04] = 0
    if uniform:
        col = np.broadcast_to(np.array(color, np.uint8), a.shape + (3,))
    else:
        col = unblend_white(rgb, a)
    return np.dstack([col, (a * 255).astype(np.uint8)]).astype(np.uint8)


def unblend_white(rgb: np.ndarray, alpha: np.ndarray, paper=255.0) -> np.ndarray:
    """Настоящий цвет чернил: пиксель скана = a*цвет + (1-a)*бумага  ->  цвет = бумага - (бумага - пиксель)/a.
    Без этого края подписи/печати в режиме «сохранить цвет» получаются бледными с белым ореолом.
    paper — цвет бумаги (число, RGB или карта H×W×3); по умолчанию белая."""
    a = np.clip(alpha.astype(np.float32), 1e-3, 1)[..., None]
    p = np.asarray(paper, np.float32)
    c = p - (p - rgb.astype(np.float32)) / a
    return np.clip(c, 0, 255).astype(np.uint8)


def has_ink(rgba: np.ndarray, min_pixels: int = 30) -> bool:
    return int((rgba[..., 3] > 100).sum()) >= min_pixels


def remove_white_background(img: Image.Image, threshold: int = 235, soft: int = 30) -> Image.Image:
    """Делает белый/светлый фон прозрачным с мягким краем."""
    rgba = np.array(img.convert("RGBA")).astype(np.float32)
    mn = rgba[..., :3].min(-1)
    a = np.clip((threshold - mn) / max(soft, 1), 0, 1)
    rgba[..., 3] = rgba[..., 3] * a
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def to_png_bytes(arr_or_img) -> bytes:
    img = arr_or_img if isinstance(arr_or_img, Image.Image) else Image.fromarray(arr_or_img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
