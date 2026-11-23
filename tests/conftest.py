"""Общие данные для сквозных проверок: выдуманные сканы (qa/fake_scans.py) создаются один раз за прогон.
PDFTOOL_E2E_DATA=<папка> — хранить их между прогонами (быстрее локально)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qa"))


@pytest.fixture(scope="session")
def fake(tmp_path_factory):
    from fake_scans import Scenario, make_pair
    base = os.environ.get("PDFTOOL_E2E_DATA") or str(tmp_path_factory.mktemp("fake_scans"))
    cache = {}

    def get(name, sc: "Scenario"):
        if name not in cache:
            import json
            folder = os.path.join(base, name)
            mj = os.path.join(folder, "meta.json")
            key = repr(sc)
            meta = None
            if os.path.exists(mj):
                old = json.load(open(mj, encoding="utf-8"))
                if old.get("key") == key and all(os.path.exists(old[k]) for k in ("acts", "receipts")):
                    meta = old
            if meta is None:
                meta = make_pair(folder, sc)
                meta["key"] = key
                json.dump(meta, open(mj, "w", encoding="utf-8"), ensure_ascii=False)
            meta["scenario"] = sc
            cache[name] = meta
        return cache[name]
    get.Scenario = Scenario
    get.base = base
    return get
