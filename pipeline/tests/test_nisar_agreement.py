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
