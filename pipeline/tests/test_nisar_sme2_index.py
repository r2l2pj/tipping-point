"""tools/nisar_sme2_layers.merge_index: append/replace by (id, date), never rebuild."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_p = Path(__file__).resolve().parents[2] / "tools" / "nisar_sme2_layers.py"
_spec = importlib.util.spec_from_file_location("nisar_sme2_layers", _p)
sme2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sme2)


def test_merge_keeps_other_layers_and_replaces_same_id_date(tmp_path):
    idx = tmp_path / "_index.json"
    idx.write_text(json.dumps({"case": "x", "layers": [
        {"id": "tpi_flood", "date": "2026-10-01"},
        {"id": "nisar_sme2", "date": "2026-09-30", "note": "old"}]}))
    sme2.merge_index(idx, {"id": "nisar_sme2", "date": "2026-09-30", "note": "new"})
    sme2.merge_index(idx, {"id": "nisar_sme2", "date": "2026-10-12", "note": "b"})
    doc = json.loads(idx.read_text())
    assert doc["case"] == "x"
    keys = [(e["id"], e["date"]) for e in doc["layers"]]
    assert keys == [("tpi_flood", "2026-10-01"), ("nisar_sme2", "2026-09-30"), ("nisar_sme2", "2026-10-12")]
    assert doc["layers"][1]["note"] == "new"


def test_legend_is_object_with_stops():
    assert sme2.LEGEND["unit"] == "m³/m³"
    assert all({"v", "color", "label"} <= set(s) for s in sme2.LEGEND["stops"])
