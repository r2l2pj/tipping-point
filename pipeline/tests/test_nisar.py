from pathlib import Path

from tp.nisar import (
    _granule_meta,
    download_plan,
    gdal_subset_cmd,
    pick_pairs,
)

GCOV_NAMES = [
    "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_D0",
    "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_D0",
    "NISAR_L2_PR_GCOV_005_172_D_064_4005_DHDH_A_20251122T031815_D0",
    "NISAR_L2_PR_GCOV_001_005_A_026_4005_DHDH_A_20251204T000000_D0",
    "NISAR_L2_PR_GCOV_002_005_A_026_4005_DHDH_A_20251228T000000_D0",
]


def test_granule_meta_parse():
    m = _granule_meta(GCOV_NAMES[0])
    assert m == {
        "product": "GCOV", "track": 172, "look": "D",
        "frame": 64, "date": "2025-12-04",
    }
    assert _granule_meta("not_a_granule") is None


def test_pick_pairs_same_frame():
    pairs = pick_pairs(GCOV_NAMES, "2025-12-12")
    assert set(pairs) == {"172D_064", "005A_026"}
    pre, post = pairs["172D_064"]["pre"], pairs["172D_064"]["post"]
    assert "20251204" in pre and "20251216" in post


def test_pick_pairs_no_post():
    pairs = pick_pairs(GCOV_NAMES, "2026-06-01")
    assert pairs == {}


class _G:
    def __init__(self, native_id):
        self.meta = {"native-id": native_id}


def test_pick_pairs_objects():
    pairs = pick_pairs([_G(n) for n in GCOV_NAMES[:2]], "2025-12-12")
    assert pairs["172D_064"]["pre"].meta["native-id"] == GCOV_NAMES[0]


def test_gdal_subset_cmd():
    cmd = gdal_subset_cmd(
        "https://example.com/x.h5",
        "/science/LSAR/GCOV/grids/frequencyA/HHHH",
        (-122.6, 47.0, -121.5, 48.5),
        Path("out.tif"),
    )
    assert "gdalwarp" in cmd
    assert "NETCDF:" in cmd and "/vsicurl/" in cmd
    assert "-te -122.6 47.0 -121.5 48.5" in cmd


def test_download_plan_wa():
    from tp.events import load_event

    plan = download_plan(load_event("wa-2025-12"))
    gcov = [s for s in plan if s["product"] == "GCOV"]
    gunw = [s for s in plan if s["product"] == "GUNW"]
    wm = [s for s in plan if s["product"] == "WATERMASK"]
    assert len(gcov) == 6      # HHHH, HVHV, mask x pre/post
    assert len(gunw) == 2
    assert len(wm) == 1
    assert all("wrappedInterferogram" in s["h5_path"] for s in gunw)
    assert {s["which"] for s in gcov} == {"pre", "post"}
