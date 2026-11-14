import json

import numpy as np
import pytest
import rioxarray as rxr
from conftest import write_tif
from tp.validation import (
    case_id,
    cloud_only_merge,
    cloud_pct,
    hls_fmask_cloud_pct,
    mosaic_tiles,
    opera_product,
    validate_flood,
    write_metrics,
)


def test_case_id_and_opera_product():
    assert case_id("wa-2025-12") == "wa_2025"
    assert case_id("rs-2024-05") == "rs_2024"
    assert opera_product("wa_2025") == "OPERA_L3_DSWX-S1_V1"
    assert opera_product("rs_2024") == "OPERA_L3_DSWX-HLS_V1"


def _ref_da(path):
    return rxr.open_rasterio(path).squeeze()


def test_mosaic_prefers_first_valid_value(tmp_path):
    ref_p = write_tif(tmp_path / "ref.tif", np.zeros((4, 4), np.float32))
    ref = _ref_da(ref_p)
    a = np.array([
        [1,   0,   0,   253],
        [253, 1,   0,   0],
        [253, 0,   0,   0],
        [255, 0,   0,   0],
    ], np.uint8)
    b = np.array([
        [255, 255, 255, 255],
        [2,   2,   255, 255],
        [0,   255, 255, 255],
        [1,   255, 255, 255],
    ], np.uint8)
    pa = write_tif(tmp_path / "a.tif", a, nodata=255)
    pb = write_tif(tmp_path / "b.tif", b, nodata=255)

    m = mosaic_tiles([pa, pb], ref)
    assert m[0, 3] == 253      # clouded in A, never covered by B -> stays cloud
    assert m[1, 0] == 2        # B's observed value wins over A's cloud
    assert m[2, 0] == 0
    assert m[3, 0] == 1        # A nodata, B observed
    assert m[1, 1] == 1        # A first: its observed value beats B's 2
    m_rev = mosaic_tiles([pb, pa], ref)
    assert m_rev[1, 1] == 2    # order flipped -> B wins
    assert cloud_pct(m) == pytest.approx(100 / 16)   # 1 cloud px, 0 nodata


def test_mosaic_reprojects_coarser_tile(tmp_path):
    ref_p = write_tif(tmp_path / "ref.tif", np.zeros((4, 4), np.float32))
    ref = _ref_da(ref_p)
    # 2x2 @ 20 m covers the same 40 m extent as the 4x4 @ 10 m ref
    c = np.array([[1, 253], [253, 0]], np.uint8)
    pc = write_tif(tmp_path / "c.tif", c, res=20.0, nodata=255)
    m = mosaic_tiles([pc], ref)
    assert m[0, 0] == 1 and m[1, 1] == 1
    assert m[0, 2] == 253 and m[1, 3] == 253
    assert m[2, 0] == 253 and m[3, 1] == 253
    assert m[2, 2] == 0 and m[3, 3] == 0
    assert cloud_pct(m) == pytest.approx(50.0)       # 8 cloud px of 16


def _hand_rasters(tmp_path):
    wtr = np.array([
        [1, 1, 0, 253],
        [1, 0, 0, 253],
        [0, 2, 0, 255],
        [0, 0, 0, 255],
    ], np.uint8)
    nisar = np.array([
        [1, 0, 0, 0],
        [1, 1, 0, 0],
        [0, 1, 1, 0],
        [0, 0, 0, 0],
    ], np.float32)
    wtr_p = write_tif(tmp_path / "wtr.tif", wtr, nodata=255)
    nisar_p = write_tif(tmp_path / "nisar.tif", nisar)
    # tp=3 ((0,0),(1,0),(2,1)), fp=2 ((1,1),(2,2)), fn=1 ((0,1))
    return nisar_p, wtr_p


def test_validate_flood_hand_computed(tmp_path):
    nisar_p, wtr_p = _hand_rasters(tmp_path)
    m = validate_flood(nisar_p, wtr_p)
    assert m["iou"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(6 / 9)
    assert m["cloud_pct"] == pytest.approx(200 / 14)


def test_validate_flood_accepts_tile_list(tmp_path):
    nisar_p, wtr_p = _hand_rasters(tmp_path)
    single = validate_flood(nisar_p, wtr_p)
    mosaicked = validate_flood(nisar_p, [wtr_p])
    assert mosaicked == pytest.approx(single)


def test_hls_fmask_bit1_cloud(tmp_path):
    fmask = np.array([
        [2, 6, 0, 0],    # 2 = cloud bit, 6 = cloud + adjacent, still cloud
        [0, 0, 0, 0],
        [4, 0, 0, 0],    # 4 = bit2 (shadow/adjacent) — not cloud
        [0, 0, 0, 255],  # 255 = nodata
    ], np.uint8)
    p = write_tif(tmp_path / "fmask.tif", fmask, nodata=255)
    assert hls_fmask_cloud_pct(p) == pytest.approx(200 / 15)


def test_cloud_only_merge_preserves_keys(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(
        {"event": "rs-2024-05", "flooded_km2": 10.0}))
    m = cloud_only_merge("rs-2024-05", 92.5, "hls_fmask", path=path)
    assert m["cloud_pct"] == 92.5
    assert m["iou"] is None and m["f1"] is None
    assert "clouded" in m["_null_reasons"]["iou"]
    assert "clouded" in m["_null_reasons"]["f1"]
    assert m["flooded_km2"] == 10.0          # pre-existing key survives
    assert m["collections"]["cloud_pct_source"] == "hls_fmask"


def test_write_metrics_preserves_existing_keys(tmp_path):
    # the old write_metrics clobbered metrics.json via write_text
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(
        {"event": "rs-2024-05", "flooded_km2": 42.0}))
    m = write_metrics("rs-2024-05", {"iou": 0.5, "f1": 0.66}, path=path)
    on_disk = json.loads(path.read_text())
    assert on_disk == m
    assert on_disk["flooded_km2"] == 42.0
    assert on_disk["iou"] == 0.5 and on_disk["f1"] == 0.66
