"""tools/nisar_agreement.py — scoring against a single-pass NISAR water mask (hermetic)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import nisar_agreement as na


def _w(water, observed, day="2025-12-16"):
    return {"water": water, "observed": observed, "pass_ids": ["p"], "pass_dates": [day], "map_day": day}


def test_agreement_day_hits_area_outside_floodplain_and_leads():
    t = np.linspace(0, 1, 100).reshape(10, 10)
    tpi = np.zeros((10, 10))
    tpi[0, :] = 0.9                        # flags row 0 only
    water = np.zeros((10, 10), bool)
    water[0, :5] = True                    # 5 water cells, all flagged
    water[9, 0] = True                     # 1 water cell outside the flag
    observed = np.ones((10, 10), bool)
    observed[5:, 5:] = False
    fp = np.ones((10, 10), bool)
    fp[9, 0] = False                       # the missed cell is off the floodplain
    r = na.agreement_day(_w(water, observed), tpi, {1: (tpi, ["f"]), 2: (None, "no run"), 3: (None, "x")}, t, fp)
    assert r["water_cells"] == 6 and r["observed_cells"] == 75
    assert r["outside_floodplain_pct"] == round(100 / 6, 1)
    o = r["observed"]["critico"]
    assert o["hit_pct"] == round(100 * 5 / 6, 2) and o["area_pct"] == round(100 * 10 / 75, 3)
    assert r["forecast"]["lead_2d"] == {"null_reason": "no run"}
    k = na.flat_keys([r])
    assert k["nisar_agreement_obs_critical_hit_pct"] == round(100 * 5 / 6, 1)
    assert k["nisar_agreement_fc2d_hit_pct"] is None
    assert k["nisar_agreement_outside_floodplain_pct"] == round(100 / 6, 1)


def test_no_water_is_a_null_not_a_zero():
    z = np.zeros((4, 4), bool)
    r = na.agreement_day(_w(z, ~z), np.zeros((4, 4)), {}, np.ones((4, 4)), None)
    assert r["null_reason"] and "observed" not in r


def _row(water, observed, tpi, day, passes, transform=None, fp=None):
    w = _w(water, observed, day) | {"pass_dates": passes, "pass_ids": [f"p{p}" for p in passes],
                                    "pass_tracks": ["172D"] * len(passes), "transform": transform}
    t = np.linspace(0, 1, tpi.size).reshape(tpi.shape)
    return na.agreement_day(w, tpi, {1: (tpi, ["f"]), 2: (None, "no GFS run x"), 3: (tpi, ["f"])}, t, fp)


def test_water_km2_geodesic_and_tracks():
    water = np.zeros((4, 4), bool)
    water[0, 0] = water[3, 3] = True
    tr = [0.01, 0.0, -122.0, 0.0, -0.01, 48.0]
    r = _row(water, np.ones((4, 4), bool), np.full((4, 4), 0.9), "2025-12-16", ["2025-12-16"], tr)
    lat = np.array([48.0 - 0.005, 48.0 - 0.035])
    exp = float((0.01 * 111.32 * np.cos(np.deg2rad(lat)) * 0.01 * 110.574).sum())
    assert abs(r["water_km2"] - exp) < 0.01 and r["pass_tracks"] == ["172D"]
    assert _row(water, np.ones((4, 4), bool), np.zeros((4, 4)), "2025-12-16", ["2025-12-16"])["water_km2"] is None


def test_block_headline_passes_and_flat_keys(tmp_path):
    water = np.zeros((10, 10), bool)
    water[0, :4] = True
    tpi = np.zeros((10, 10))
    tpi[0, :2] = 0.9                                        # 2 of 4 water cells flagged
    fp = np.ones((10, 10), bool)
    fp[0, 3] = False
    tr = [0.01, 0.0, -122.0, 0.0, -0.01, 48.0]
    days = [_row(water, np.ones((10, 10), bool), tpi, "20251216", ["2025-12-04", "2025-12-16"], tr, fp),
            _row(water, np.ones((10, 10), bool), tpi, "20251217", ["2025-12-16"], tr, fp),
            _row(water, np.ones((10, 10), bool), tpi, "20251204", ["2025-12-04"], tr, fp)]
    blk, flat, why = na.block(days, "2025-12-16")
    assert blk["headline"]["map_day"] == "20251216"
    assert [d["map_day"] for d in blk["passes"]] == ["20251216", "20251204"]   # age-0 rows only
    assert flat["nisar_agreement_obs_hit_pct"] == 50.0 and flat["nisar_agreement_fc1d_hit_pct"] == 50.0
    assert flat["nisar_agreement_fc2d_hit_pct"] is None and why["nisar_agreement_fc2d_hit_pct"] == "no GFS run x"
    assert flat["nisar_agreement_outside_floodplain_pct"] == 25.0 and flat["nisar_agreement_water_km2"] > 0
    assert flat["nisar_agreement_obs_lift"] == round(0.5 / 0.02, 2)
    _, f2, w2 = na.block(days, "2025-12-30")
    assert all(v is None for v in f2.values()) and set(w2) == set(f2)
    m = tmp_path / "metrics.json"
    m.write_text('{"cases": {"wa_2025": {"event": "wa-2025-12", "keep": 1}}}')
    na.write_metrics("wa_2025", days, "2025-12-16", m)
    import json
    c = json.loads(m.read_text())["cases"]["wa_2025"]
    assert c["keep"] == 1 and c["nisar_agreement"]["headline"]["map_day"] == "20251216"
    assert c["_null_reasons"]["nisar_agreement_fc2d_hit_pct"] == "no GFS run x"


def test_load_water_ignores_empty_floodplain(tmp_path):
    p = tmp_path / "water_20251216.npz"
    np.savez_compressed(p, water=np.ones((2, 2), bool), observed=np.ones((2, 2), bool),
                        transform=np.array([0.01, 0, -122, 0, -0.01, 48.0]), floodplain=np.zeros(0, bool),
                        pass_ids=np.array(["a"]), pass_dates=np.array(["2025-12-16"]), map_day="20251216")
    w = na.load_water(p)
    assert w["floodplain"] is None and w["transform"][0] == 0.01
