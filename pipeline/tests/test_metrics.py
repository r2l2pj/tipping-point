import json
import os

import pytest
from tp.metrics import merge_metrics


def _read(path):
    return json.loads(path.read_text())


def test_merge_creates_file_with_event(tmp_path):
    path = tmp_path / "rs-2024-05" / "metrics.json"
    m = merge_metrics("rs-2024-05", flat={"iou": 0.4}, path=path)
    assert path.exists()
    assert m == _read(path)                     # returns the merged dict
    assert m["event"] == "rs-2024-05"
    assert m["iou"] == 0.4
    # trailing newline, repo style
    assert path.read_text().endswith("}\n")


def test_merge_preserves_existing_keys(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"event": "rs-2024-05", "iou": 0.5,
                                "collections": {"nisar": "GCOV"}}))
    merge_metrics("rs-2024-05", flat={"flooded_km2": 12.3}, path=path)
    m = _read(path)
    assert m["iou"] == 0.5                    # untouched key kept
    assert m["flooded_km2"] == 12.3
    assert m["collections"] == {"nisar": "GCOV"}


def test_null_writes_reason_under_null_reasons(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics(
        "wa-2025-12",
        flat={"people_flooded": None, "iou": None},
        reasons={"people_flooded": "no NISAR flood asset yet"},
        path=path,
    )
    m = _read(path)
    assert m["people_flooded"] is None
    assert m["iou"] is None
    assert m["_null_reasons"]["people_flooded"] == "no NISAR flood asset yet"
    assert m["_null_reasons"]["iou"]          # generic fallback reason


def test_real_value_clears_stale_reason(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("wa-2025-12", flat={"iou": None}, path=path)
    assert _read(path)["_null_reasons"]["iou"]
    merge_metrics("wa-2025-12", flat={"iou": 0.61}, path=path)
    m = _read(path)
    assert m["iou"] == 0.61
    assert "_null_reasons" not in m or "iou" not in m["_null_reasons"]


def test_collections_merge(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", collections={"imerg": "NASA/GPM_L3/IMERG_V07"},
                  path=path)
    merge_metrics("rs-2024-05", collections={"smap": "NASA/SMAP/SPL4SMGP/008"},
                  path=path)
    m = _read(path)
    assert m["collections"] == {"imerg": "NASA/GPM_L3/IMERG_V07",
                                "smap": "NASA/SMAP/SPL4SMGP/008"}


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
    path.write_text(json.dumps({"event": "rs-2024-05", "iou": 0.9}))
    monkeypatch.setattr(
        "tp.metrics.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        merge_metrics("rs-2024-05", flat={"f1": 0.7}, path=path)
    assert _read(path) == {"event": "rs-2024-05", "iou": 0.9}


def test_repeated_merges_accumulate(tmp_path):
    path = tmp_path / "metrics.json"
    merge_metrics("rs-2024-05", flat={"iou": 0.4}, path=path)
    merge_metrics("rs-2024-05", flat={"f1": 0.5}, path=path)
    merge_metrics("rs-2024-05", flat={"iou": 0.6, "cloud_pct": 12.0},
                  collections={"opera": "DSWx-HLS"}, path=path)
    m = _read(path)
    assert m == {"event": "rs-2024-05", "iou": 0.6, "f1": 0.5,
                 "cloud_pct": 12.0,
                 "collections": {"opera": "DSWx-HLS"}}
