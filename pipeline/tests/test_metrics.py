import json
import os

import pytest
from tp.metrics import merge_metrics


def _read(path):
    return json.loads(path.read_text())


def test_merge_creates_root_cases_shape(tmp_path):
    path = tmp_path / "rs-2024-05" / "metrics.json"
    m = merge_metrics("rs-2024-05", flat={"iou": 0.4}, path=path)
    assert path.exists()
    assert m == _read(path)                     # returns the merged root dict
    blob = m["cases"]["rs_2024"]
    assert blob["event"] == "rs-2024-05"
    assert blob["iou"] == 0.4
    # trailing newline, repo style
    assert path.read_text().endswith("}\n")


def test_case_id_input_derives_event(tmp_path):
    m = merge_metrics("wa_2025", flat={"cloud_pct": 61.0},
                      path=tmp_path / "metrics.json")
    blob = m["cases"]["wa_2025"]
    assert blob["event"] == "wa-2025-12"        # derived via events/*.json
    assert blob["cloud_pct"] == 61.0


def test_merge_preserves_keys_across_cases(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", flat={"iou": 0.5}, path=path)
    m = merge_metrics("wa-2025-12", flat={"flooded_km2": 12.3}, path=path)
    assert m["cases"]["rs_2024"]["iou"] == 0.5  # untouched case kept
    assert m["cases"]["wa_2025"]["flooded_km2"] == 12.3
    assert set(m["cases"]) == {"rs_2024", "wa_2025"}


def test_null_writes_reason_under_null_reasons(tmp_path):
    path = tmp_path / "metrics.json"
    m = merge_metrics(
        "wa-2025-12",
        flat={"people_flooded": None, "iou": None},
        reasons={"people_flooded": "no NISAR flood asset yet"},
        path=path,
    )
    blob = m["cases"]["wa_2025"]
    assert blob["people_flooded"] is None
    assert blob["iou"] is None
    assert blob["_null_reasons"]["people_flooded"] == "no NISAR flood asset yet"
    assert blob["_null_reasons"]["iou"]         # generic fallback reason


def test_real_value_clears_stale_reason(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("wa-2025-12", flat={"iou": None}, path=path)
    m = merge_metrics("wa-2025-12", flat={"iou": 0.61}, path=path)
    blob = m["cases"]["wa_2025"]
    assert blob["iou"] == 0.61
    assert "_null_reasons" not in blob or "iou" not in blob["_null_reasons"]


def test_null_never_clobbers_written_value(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", flat={"iou": 0.6}, path=path)
    m = merge_metrics("rs-2024-05", flat={"iou": None}, path=path)
    assert m["cases"]["rs_2024"]["iou"] == 0.6


def test_collections_merge(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", collections={"imerg": "NASA/GPM_L3/IMERG_V07"},
                  path=path)
    m = merge_metrics("rs-2024-05",
                      collections={"smap": "NASA/SMAP/SPL4SMGP/008"},
                      path=path)
    assert m["cases"]["rs_2024"]["collections"] == {
        "imerg": "NASA/GPM_L3/IMERG_V07", "smap": "NASA/SMAP/SPL4SMGP/008"}


def test_atomic_write_uses_tmp_then_replace(tmp_path, monkeypatch):
    path = tmp_path / "metrics.json"
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr("tp.metrics.os.replace", spy)
    merge_metrics("rs-2024-05", flat={"iou": 0.4}, path=path)
    assert calls == [(path.with_name("metrics.json.tmp"), path)]
    assert not (tmp_path / "metrics.json.tmp").exists()


def test_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"cases": {"rs_2024": {"iou": 0.9}}}))
    monkeypatch.setattr(
        "tp.metrics.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        merge_metrics("rs-2024-05", flat={"f1": 0.7}, path=path)
    assert _read(path) == {"cases": {"rs_2024": {"iou": 0.9}}}


def test_repeated_merges_accumulate(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", flat={"iou": 0.4}, path=path)
    merge_metrics("rs-2024-05", flat={"f1": 0.5}, path=path)
    m = merge_metrics("rs-2024-05", flat={"iou": 0.6, "cloud_pct": 12.0},
                      collections={"opera": "DSWx-HLS"}, path=path)
    assert m["cases"]["rs_2024"] == {
        "event": "rs-2024-05", "iou": 0.6, "f1": 0.5, "cloud_pct": 12.0,
        "collections": {"opera": "DSWx-HLS"}}
