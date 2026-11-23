"""Регрессии по итогам сквозной проверки 1.4.2 (чёрная ручка, повтор номеров, связные области)."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.core import formmask, numbers
from app.core.digits import DigitResult
from app.core.ink import BLACK_INK_RGB, _components, extract_ink, ink_color_for

FONT = "app/ui/assets/fonts/Inter-Regular.ttf"


def _crop(seed, dx=0, dy=0):
    """Кусок «акта»: печатный текст бланка (одинаковый) + своя чёрная закорючка (разная)."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", (600, 200), (238, 236, 230))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, 22)
    d.text((20 + dx, 15 + dy), "В чем и расписываемся:", fill=(30, 30, 30), font=f)
    d.text((40 + dx, 125 + dy), "(Ф. И. О.)", fill=(30, 30, 30), font=f)
    d.text((420 + dx, 125 + dy), "(подпись)", fill=(30, 30, 30), font=f)
    pts = [(int(x), int(70 + 25 * np.sin(x / rng.uniform(9, 20) + rng.uniform(0, 6))))
           for x in np.linspace(330, 560, 60)]
    d.line(pts, fill=(25, 25, 28), width=3)
    sm = Image.new("L", (600, 200), 0)
    ImageDraw.Draw(sm).line(pts, fill=255, width=3)
    sig = np.asarray(sm) > 0
    return np.asarray(im), sig


def test_form_text_removed_signature_kept():
    crops, sigs = {}, {}
    for p, (dx, dy) in enumerate([(0, 0), (4, -3), (-5, 2), (2, 5)]):
        crops[p], sigs[p] = _crop(p, dx, dy)
    fm = formmask.form_masks(crops)
    assert set(fm) == set(crops)
    for p in crops:
        dk = formmask.dark(crops[p])
        printed = dk & ~sigs[p]
        assert (printed & fm[p]).sum() / printed.sum() > 0.9, "печатный текст должен уйти"
        assert (sigs[p] & fm[p]).sum() / sigs[p].sum() < 0.1, "подпись должна остаться"
        a = extract_ink(crops[p], mode="black", exclude=fm[p])[..., 3] > 60
        assert (a & printed).sum() < 0.1 * printed.sum()
        assert (a & sigs[p]).sum() > 0.6 * sigs[p].sum()


def test_form_needs_three_pages():
    c, _ = _crop(0)
    assert formmask.form_masks({0: c, 1: c}) == {}


def test_black_ink_stays_black():
    assert ink_color_for("black") == BLACK_INK_RGB and ink_color_for("any") == BLACK_INK_RGB
    c, sig = _crop(1)
    out = extract_ink(c, mode="black")
    px = out[sig & (out[..., 3] > 200)][:, :3].astype(int)
    assert len(px) and abs(px[:, 2] - px[:, 0]).max() < 20, "чёрная подпись не должна перекрашиваться в синий"


def test_duplicate_numbers():
    r = [DigitResult(t, 0.9, len(t)) for t in ["14", "27", "27", "", "35"]]
    assert numbers.duplicate_numbers(r) == {"27": [1, 2]}


def test_components_match_bfs():
    rng = np.random.default_rng(3)
    m = rng.random((60, 70)) > 0.55
    lab = np.zeros(m.shape, int)
    for i, (ys, xs) in enumerate(_components(m), 1):
        assert (lab[ys, xs] == 0).all()
        lab[ys, xs] = i
    assert ((lab > 0) == m).all()
    h, w = m.shape
    for y in range(h):              # соседи по 8-связности — в одной области
        for x in range(w):
            if not m[y, x]:
                continue
            for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and m[yy, xx]:
                    assert lab[yy, xx] == lab[y, x]
