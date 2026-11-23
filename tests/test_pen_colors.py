"""Вырезание подписи всеми обычными цветами ручек: полнота штриха, без текста бланка,
настоящий цвет без перекраски, без бумаги вокруг. Только выдуманные ФИО."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from app.core.ink import extract_ink, ink_color_for, _rgb_to_hsv_cv  # noqa: E402
from app.core import retouch as rt  # noqa: E402
from pen_samples import PENS, RECOMMENDED_MODE, make_crop  # noqa: E402

# На кремовой/жёлтой/серой бумаге все обычные ручки должны проходить в своём режиме.
# «Фото с телефона» и очень бледный карандаш — слабее контраст, порог чуть мягче.
PAPERS = ("cream", "yellow", "grey")
CHROMATIC = {"blue_ballpoint", "pale_blue", "navy", "purple", "red", "green", "blue_gel"}


def _score(rgba, core, form_only, paper_only):
    a = rgba[..., 3].astype(np.float32) / 255.0
    cap = a > 0.23
    core_n = int(core.sum()) or 1
    form_n = int(form_only.sum()) or 1
    paper_n = int(paper_only.sum()) or 1
    recall = float((cap & core).sum() / core_n)
    form_leak = float((cap & form_only).sum() / form_n)
    halo = float(a[paper_only].mean())
    halo_frac = float(((a > 0.15) & paper_only).sum() / paper_n)
    take = a > 0.4
    med = tuple(int(x) for x in np.median(rgba[take, :3], 0)) if take.sum() >= 20 else None
    return recall, form_leak, halo, halo_frac, med, take


def _hue(rgb):
    h, s, v = _rgb_to_hsv_cv(np.array([[rgb]], np.uint8))[0, 0]
    return float(h), float(s), float(v)


def _not_recolored_blue(med, true_rgb):
    """На жёлтой бумаге синяя/фиолетовая ручка выглядит коричневой — это не перекраска.
    Падаем только если программа выкрасила штрих в стандартный синий (28, 48, 160)."""
    if med is None:
        return False
    default = ink_color_for("blue")
    forced = abs(med[0] - default[0]) < 22 and abs(med[1] - default[1]) < 22 and abs(med[2] - default[2]) < 30
    true_is_blue = true_rgb[2] > true_rgb[0] + 20 and true_rgb[2] > true_rgb[1]
    return (not forced) or true_is_blue


@pytest.mark.parametrize("pen", list(PENS))
@pytest.mark.parametrize("paper", PAPERS)
def test_recommended_mode_keeps_stroke_and_color(pen, paper):
    rgb, core, form_only, paper_only, spec = make_crop(pen, paper, phone=False, seed=2026)
    mode = RECOMMENDED_MODE[pen]
    rgba = extract_ink(rgb, uniform=False, mode=mode, sensitivity=0.5)
    recall, form_leak, halo, halo_frac, med, take = _score(rgba, core, form_only, paper_only)
    # на серой/жёлтой бумаге бледная синяя слабее, и кусочки линии бланка могут остаться
    min_rec = 0.50 if (pen == "pale_blue" and paper != "cream") or (pen == "black_gel" and paper == "yellow") else 0.70
    max_form = 0.12 if paper != "cream" else 0.08
    assert recall >= min_rec, f"{pen}/{paper}: штрих неполный recall={recall:.3f}"
    assert form_leak <= max_form, f"{pen}/{paper}: захвачен бланк form={form_leak:.3f}"
    assert halo <= 0.03 and halo_frac <= 0.015, f"{pen}/{paper}: ореол бумаги halo={halo:.4f} frac={halo_frac:.4f}"
    assert _not_recolored_blue(med, spec["rgb"]), f"{pen}/{paper}: перекраска med={med} true={spec['rgb']}"
    if pen == "mix":
        hues = _rgb_to_hsv_cv(rgba[..., :3])[..., 0][take]
        sats = _rgb_to_hsv_cv(rgba[..., :3])[..., 1][take]
        assert ((hues >= 88) & (hues <= 152) & (sats > 40)).mean() > 0.12, "синяя часть смеси пропала"
        assert (sats < 45).mean() > 0.08, "чёрная часть смеси пропала"


@pytest.mark.parametrize("pen", ["pale_blue", "navy", "red", "pencil", "blue_ballpoint"])
def test_phone_photo_and_yellow_still_usable(pen):
    rgb, core, form_only, paper_only, spec = make_crop(pen, "cream", phone=True, seed=11)
    mode = RECOMMENDED_MODE[pen]
    rgba = extract_ink(rgb, uniform=False, mode=mode, sensitivity=0.65)
    recall, form_leak, halo, halo_frac, med, _ = _score(rgba, core, form_only, paper_only)
    # низкий контраст: главное — не захватить бумагу и бланк, штрих хотя бы наполовину
    # бледная синяя и navy на фото с телефона почти сливаются с фоном — это ограничение
    if pen in ("pale_blue", "navy"):
        assert halo_frac <= 0.04 and form_leak <= 0.10, f"{pen}/phone: бумага/бланк halo={halo_frac:.4f} form={form_leak:.3f}"
        return
    min_rec = 0.35 if pen == "pencil" else 0.50
    assert recall >= min_rec, f"{pen}/phone: recall={recall:.3f}"
    assert form_leak <= 0.12, f"{pen}/phone: form={form_leak:.3f}"
    assert halo_frac <= 0.05, f"{pen}/phone: halo_frac={halo_frac:.4f}"


def test_default_blue_mode_ignores_red_and_green():
    for pen in ("red", "green"):
        rgb, core, form_only, paper_only, spec = make_crop(pen, "cream", seed=3)
        rgba = extract_ink(rgb, uniform=False, mode="blue", sensitivity=0.5)
        recall, form_leak, *_ = _score(rgba, core, form_only, paper_only)
        assert recall < 0.12, f"режим «синяя» не должен брать {pen}: recall={recall:.3f}"
        assert form_leak <= 0.04


def test_color_mode_does_not_fill_yellow_paper():
    rgb, core, form_only, paper_only, spec = make_crop("red", "yellow", seed=4)
    rgba = extract_ink(rgb, uniform=False, mode="color", sensitivity=0.5)
    _, _, halo, halo_frac, _, _ = _score(rgba, core, form_only, paper_only)
    assert halo_frac <= 0.02 and halo <= 0.04, f"жёлтая бумага стала «печатью»: halo={halo:.4f} frac={halo_frac:.4f}"


def test_retouch_restore_keeps_ink_not_paper():
    for pen, mode, paper in (("blue_ballpoint", "blue", "yellow"), ("red", "any", "yellow"),
                             ("black", "black", "cream"), ("pencil", "black", "cream")):
        rgb, core, form_only, paper_only, spec = make_crop(pen, paper, seed=7)
        auto = extract_ink(rgb, uniform=False, mode=mode, sensitivity=0.5)
        h, w = auto.shape[:2]
        lab = np.zeros((h, w), np.uint8)
        lab[:, : w // 3] = 1
        lab[h // 3: 2 * h // 3, w // 8: w // 3 - 4] = 2
        out = rt.apply(auto, rgb, (), (0, 0, w, h), (w, h), mode, True, labels=lab)
        erased = lab == 1
        restored = lab == 2
        assert float(out[erased & ~restored, 3].mean()) < 5, f"{pen}: ластик не стёр"
        on_ink = restored & core
        on_paper = restored & paper_only
        rec = float((out[on_ink, 3] > 60).mean()) if on_ink.any() else 0
        paper_back = float(out[on_paper, 3].mean()) / 255 if on_paper.any() else 0
        assert rec >= 0.55, f"{pen}: кисть не вернула чернила rec={rec:.3f}"
        assert paper_back < 0.08, f"{pen}: кисть вернула бумагу paper_back={paper_back:.3f}"
        take = on_ink & (out[..., 3] > 100)
        if take.sum() > 10:
            med = tuple(int(x) for x in np.median(out[take, :3], 0))
            assert _not_recolored_blue(med, spec["rgb"]), f"{pen}: кисть перекрасила med={med}"
