"""Шаблоны бланков (сохранение/загрузка/экспорт/автоопределение) и пакетный режим на job2."""
import os
import sys
import tempfile

import pymupdf
import pytest

_TPL = tempfile.mkdtemp(prefix="pdftool_tpl_")
os.environ["PDFTOOL_TEMPLATES"] = _TPL
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core import batch, numbers, templates as tp  # noqa: E402
from app.core.pdfutil import NRect  # noqa: E402

DATA = os.environ.get("PDFTOOL_DATA", "/workspace")
ACTS = f"{DATA}/job2/fileB_3.5MB.pdf"
RECEIPTS = f"{DATA}/job2/fileA_3.0MB.pdf"
need = pytest.mark.skipif(not os.path.exists(ACTS), reason="нет тестовых файлов")

# Зоны номера для двух видов бланка актов job2 и для расписки (подобраны по страницам 1 и 4 актов)
ZONE_A = NRect(0.255, 0.335, 0.37, 0.38)
ZONE_B = NRect(0.30, 0.375, 0.40, 0.42)
ZONE_R = NRect(0.37, 0.222, 0.47, 0.254)
ACTS_TRUTH = ["146", "102", "95", "63", "66", "21", "148", "21"]
REC_TRUTH = ["63", "66", "21", "148", "102", "146", "95"]
FORM_B = {3, 4, 6}   # страницы актов (0-based) с «растянутым» бланком


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    monkeypatch.setenv("PDFTOOL_TEMPLATES", _TPL)
    for f in os.listdir(_TPL):
        os.remove(os.path.join(_TPL, f))
    yield


def test_roundtrip_save_load_rename_delete_export_import(tmp_path):
    t = tp.Template("Акт ЦО", src_rect=NRect(0.1, 0.7, 0.6, 0.8), dst_rect=NRect(0.3, 0.5, 0.8, 0.6),
                    zones_act=[NRect(0.2, 0.3, 0.4, 0.35)], zones_rec=[ZONE_R, ZONE_A],
                    ink_mode="black", sensitivity=0.7, fp_act=[0.0, 0.5, 1.0], fp_rec=None)
    p = tp.save(t)
    assert p.startswith(_TPL) and p.endswith("Акт ЦО.json")
    got = tp.get("Акт ЦО")
    assert got == t
    tp.rename("Акт ЦО", "Расписка ГВС")
    assert tp.get("Акт ЦО") is None and tp.get("Расписка ГВС").zones_rec == t.zones_rec
    exp = tp.export_to(tp.get("Расписка ГВС"), str(tmp_path / "share.json"))
    tp.delete("Расписка ГВС")
    assert tp.load_all() == []
    imp = tp.import_from(exp)
    assert imp.name == "Расписка ГВС" and imp.ink_mode == "black" and imp.sensitivity == 0.7
    imp2 = tp.import_from(exp)          # повторный импорт не затирает, а добавляет «(2)»
    assert imp2.name == "Расписка ГВС (2)"
    assert [x.name for x in tp.load_all()] == ["Расписка ГВС", "Расписка ГВС (2)"]
    with open(tmp_path / "bad.json", "w") as f:
        f.write('{"name": "x"}')
    with pytest.raises(ValueError):
        tp.import_from(str(tmp_path / "bad.json"))


@need
def test_autodetect_form_per_page_job2():
    a, r = pymupdf.open(ACTS), pymupdf.open(RECEIPTS)
    ta = tp.Template("Акт A", zones_act=[ZONE_A], zones_rec=[ZONE_R], fp_act=tp.fingerprint(a, 0),
                     fp_rec=tp.fingerprint(r, 0))
    tb = tp.Template("Акт B", zones_act=[ZONE_B], fp_act=tp.fingerprint(a, 3))
    det = tp.detect_pages(ACTS, [ta, tb], "act")
    names = {i: d.template.name if d.template else None for i, d in det.items()}
    assert names == {i: ("Акт B" if i in FORM_B else "Акт A") for i in range(8)}, names
    da, dr = [], []
    ra = numbers.read_document(ACTS, [ZONE_A], temps=[ta, tb], role="act", detected=da)
    rr = numbers.read_document(RECEIPTS, [ZONE_R], temps=[ta], role="rec", detected=dr)
    m, _ = numbers.auto_match(rr, ra)
    truth = [3, 4, 7, 6, None, 0, 2]
    print("\njob2 с шаблонами: акты верно", sum(x.text == t for x, t in zip(ra, ACTS_TRUTH)), "/ 8;",
          "расписки верно", sum(x.text == t for x, t in zip(rr, REC_TRUTH)), "/ 7;",
          "автосопоставление верно", sum(1 for x, t in zip(m, truth) if t is not None and x == t), "/ 6;",
          [x.text for x in ra], [x.text for x in rr])
    assert len(ra) == 8 and len(rr) == 7


@need
def test_batch_two_pairs_job2(tmp_path):
    t = tp.Template("Пара job2", fp_act=None, ink_mode="blue", sensitivity=0.5)
    tp.save(t)
    m = [3, 4, 7, 6, None, 0, 2]
    items = [batch.BatchItem(ACTS, RECEIPTS, str(tmp_path / "out1.pdf"), template="Пара job2", mapping=m),
             batch.BatchItem(ACTS, RECEIPTS, str(tmp_path / "out2.pdf"), template=None)]   # по порядку
    seen = []
    res = batch.run_batch(items, tp.load_all(), lambda i, n: seen.append((i, n)))
    assert [r["status"] for r in res] == ["ok", "ok"]
    assert res[0]["placed"] == 6 and res[0]["blank"] == [4]
    assert res[1]["placed"] + len(res[1]["empty_ink"]) == 7
    for k in (1, 2):
        with pymupdf.open(tmp_path / f"out{k}.pdf") as d:
            assert len(d) == 7
    assert seen[-1][0] == seen[-1][1] == 14
    # входы не тронуты, выход поверх входа запрещён, одинаковые выходы запрещены
    bad = [batch.BatchItem(ACTS, RECEIPTS, RECEIPTS)]
    with pytest.raises(ValueError):
        batch.run_batch(bad)
    dup = [batch.BatchItem(ACTS, RECEIPTS, str(tmp_path / "x.pdf"))] * 2
    assert batch.check_items(dup)


@need
def test_batch_cancel(tmp_path):
    items = [batch.BatchItem(ACTS, RECEIPTS, str(tmp_path / f"c{k}.pdf")) for k in range(2)]
    res = batch.run_batch(items, (), lambda i, n: i < 3)
    assert res[0]["status"] == "cancelled" and res[1]["status"] == "skipped"
    assert not any(os.path.exists(tmp_path / f"c{k}.pdf") for k in range(2))
