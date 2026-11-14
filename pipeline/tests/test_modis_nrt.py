import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from conftest import write_tif
from tp.events import load_event
from tp.modis_nrt import (
    classify,
    compare_water,
    pick_scene,
    scene_date,
    search_params,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = Path(__file__).resolve().parents[2]


def test_search_params_event_window():
    p = search_params(load_event("wa-2025-12"))
    assert p["temporal"] == ["2025-11-01", "2026-01-31"]
    assert p["bounding_box"] == [-122.6, 47.0, -121.5, 48.5]
    assert p["short_names"][0] == "MCDWD_L3_FWD_NRT"
    assert "MCDWD_L3_F1C_NRT" in p["short_names"]


def test_search_params_overrides():
    p = search_params(load_event("rs-2024-05"), short_name="X",
                      start="2024-05-01", end="2024-05-03")
    assert p["short_names"][0] == "X"
    assert p["temporal"] == ["2024-05-01", "2024-05-03"]


def test_classify():
    water, valid = classify(np.array([[0, 1, 2, 3, 255, np.nan]]))
    assert water.tolist() == [[False, True, True, True, False, False]]
    assert valid.tolist() == [[True, True, True, True, False, False]]


def test_scene_date_parse():
    assert scene_date("MCDWD_L3_F1C_NRT.A2025345.h10v05.061.tif") == "2025-12-11"
    assert scene_date("MCDWD_L3_NRT.A2025001.h28v07.061.hdf") == "2025-01-01"
    assert scene_date("no_date_here.tif") is None


def test_pick_scene_nearest():
    names = ["M.A2025001.h00v00.tif", "M.A2025020.h00v00.tif",
             "M.A2025010.h00v00.tif"]
    assert pick_scene(names, "2025-01-11").name == "M.A2025010.h00v00.tif"
    assert pick_scene([], "2025-01-01") is None


def test_compare_water_agreement(tmp_path):
    ref = np.zeros((4, 4), np.float32)
    ref[:2, :2] = 1                       # ref water: 4 px top-left
    nrt = np.zeros((4, 4), np.float32)
    nrt[:2, :2] = 3                       # NRT flood class over the same block
    nrt[0, 0] = 0                         # 1 px disagreement (FN)
    ref_p = write_tif(tmp_path / "ref.tif", ref)
    nrt_p = write_tif(
        tmp_path / "MCDWD_L3_F1C_NRT.A2025345.h10v05.061.tif", nrt)

    m = compare_water(nrt_p, ref_p)
    assert m["modis_nrt_agreement_pct"] == pytest.approx(100 * 15 / 16)
    assert m["modis_nrt_iou"] == pytest.approx(3 / 4)          # tp=3 fn=1 fp=0
    assert m["modis_nrt_f1"] == pytest.approx(6 / 7)
    assert m["modis_nrt_water_px"] == 3
    assert m["modis_nrt_ref_water_px"] == 4
    assert m["modis_nrt_valid_px"] == 16
    assert m["modis_nrt_scene_date"] == "2025-12-11"
    assert m["modis_nrt_granule"] == "MCDWD_L3_F1C_NRT.A2025345.h10v05.061"
    assert m["modis_nrt_reason"] is None


def test_compare_water_reproject(tmp_path):
    # same footprint as ref (same origin) but 20 m NRT vs 10 m ref grid
    ref = np.zeros((4, 4), np.float32)
    ref[:2, :] = 1                        # top half water
    nrt = np.zeros((2, 2), np.float32)
    nrt[0, :] = 1
    ref_p = write_tif(tmp_path / "ref.tif", ref, res=10.0)
    nrt_p = write_tif(tmp_path / "nrt.tif", nrt, res=20.0)
    m = compare_water(nrt_p, ref_p)
    assert m["modis_nrt_agreement_pct"] == pytest.approx(100.0)


def test_compare_water_no_scene(tmp_path):
    ref_p = write_tif(tmp_path / "ref.tif", np.ones((4, 4), np.float32))
    for nrt in (None, tmp_path / "missing.tif"):
        m = compare_water(nrt, ref_p)
        assert m["modis_nrt_agreement_pct"] is None
        assert m["modis_nrt_scene_date"] is None
        assert m["modis_nrt_granule"] is None
        assert m["modis_nrt_reason"]


def test_compare_water_no_ref(tmp_path):
    nrt_p = write_tif(tmp_path / "nrt.A2025001.h00v00.tif",
                      np.ones((4, 4), np.float32))
    for ref in (None, tmp_path / "missing.tif"):
        m = compare_water(nrt_p, ref)
        assert m["modis_nrt_agreement_pct"] is None
        assert m["modis_nrt_reason"]
        # scene metadata is still parsed without a reference
        assert m["modis_nrt_scene_date"] == "2025-01-01"
        assert m["modis_nrt_granule"] == "nrt.A2025001.h00v00"


def test_compare_water_unreadable(tmp_path):
    bad = tmp_path / "bad.tif"
    bad.write_text("not a raster")
    ref_p = write_tif(tmp_path / "ref.tif", np.zeros((4, 4), np.float32))
    m = compare_water(bad, ref_p)
    assert m["modis_nrt_agreement_pct"] is None
    assert "unreadable" in m["modis_nrt_reason"]


def test_compare_water_no_valid_overlap(tmp_path):
    ref_p = write_tif(tmp_path / "ref.tif", np.zeros((4, 4), np.float32))
    nrt_p = write_tif(tmp_path / "nrt.tif", np.full((4, 4), 255, np.float32))
    m = compare_water(nrt_p, ref_p)
    assert m["modis_nrt_agreement_pct"] is None
    assert "no overlapping" in m["modis_nrt_reason"]


def test_dry_run_offline():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "11_modis_nrt.py"),
         "--event", "rs-2024-05", "--dry-run"],
        check=False, capture_output=True, text=True, cwd=REPO,
    )
    assert r.returncode == 0, r.stderr
    params = json.loads(r.stdout)
    assert params["temporal"] == ["2024-04-20", "2024-05-31"]
    assert params["bounding_box"] == [-52.5, -30.5, -50.5, -29.0]
    assert "MCDWD_L3_F1C_NRT" in params["short_names"]
