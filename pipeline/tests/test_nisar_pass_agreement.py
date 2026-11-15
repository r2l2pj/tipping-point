"""tools/nisar_pass_agreement.py — per-pass NISAR agreement (hermetic, synthetic frame)."""
import datetime as dt
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import nisar_pass_agreement as npa  # noqa: E402

TR = from_origin(-57.0, -29.0, 0.01, 0.01)
SHAPE = (20, 20)


def _frame(tmp_path, water_rows, date="2026-10-12"):
    """Fixed-stretch HH PNG (-30..0 dB) over the grid: rows in water_rows at -25 dB, rest -8 dB."""
    db = np.full((200, 200), -8.0)
    for r in water_rows:
        db[r * 10:(r + 1) * 10, :] = -25.0
    g = np.clip(np.round(1 + (db + 30) / 30 * 254), 1, 255).astype(np.uint8)
    im = Image.fromarray(np.dstack([g, np.full_like(g, 255)]), "LA")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    (tmp_path / "f.png").write_bytes(buf.getvalue())
    return {"id": "nisar_hh_x", "date": date, "track": "010D", "png": "f.png", "kind": "nisar_frame",
            "polarization": "HH", "bounds": [-57.0, -29.2, -56.8, -29.0],
            "stretch_db": {"db_min": -30.0, "db_max": 0.0}}


def _grid():
    t = np.tile(np.linspace(0, 1, SHAPE[1]), (SHAPE[0], 1))
    perm = np.zeros(SHAPE, bool)
    perm[19, :] = True                                   # permanent water row: never scored
    return {"shape": SHAPE, "transform": TR, "t": t, "perm_water": perm, "km2": npa.cell_km2(TR, SHAPE)}


def test_pass_record_scores_pass_day_leads_and_window(tmp_path):
    fr = _frame(tmp_path, water_rows=[0, 1, 19])
    g = _grid()
    flagged = np.zeros(SHAPE)
    flagged[0, :] = 0.9                                   # half of the water rows, Critical
    flagged[2:4, :] = 0.5                                 # High only, no water
    obs = {"20261012": np.zeros(SHAPE), "20261001": flagged}
    gfs = {"20261011": {"d1_flood": flagged}}
    fp = np.ones(SHAPE, bool)
    fp[1, :] = False                                      # water row 1 off the floodplain
    r = npa.pass_record(fr, tmp_path, g, obs, gfs, fp)
    assert r["water_cells"] == 40                         # rows 0-1; row 19 is permanent water
    assert r["outside_floodplain_pct"] == 50.0
    assert r["decoded_db"]["min"] < -18
    assert r["observed"]["alto_plus"]["hit_pct"] == 0.0   # plain TPI flagged nothing on the pass day
    l1 = r["forecast"]["lead_1d"]
    assert l1["critico"]["hit_pct"] == 50.0 and l1["issued"] == "2026-10-11"
    assert l1["alto_plus"]["area_pct"] == round(100 * 60 / 380, 3)
    assert "null_reason" in r["forecast"]["lead_2d"]
    w = r["window_12d"]
    assert w["days"] == ["2026-10-01"]                     # only days BEFORE the pass
    assert w["best_day"]["critico"]["days_before"] == 11
    assert w["max_tpi"]["critico"]["hit_pct"] == 50.0
    k = npa.flat_keys(r)
    assert k["nisar_agreement_obs_hit_pct"] == 0.0 and k["nisar_agreement_fc1d_critical_hit_pct"] == 50.0
    assert k["nisar_agreement_fc2d_hit_pct"] is None and k["nisar_agreement_pass_track"] == "010D"


def test_window_never_uses_pass_day_or_later():
    g = _grid()
    water = np.zeros(SHAPE, bool)
    water[0, :] = True
    hot = np.full(SHAPE, 0.9)
    obs = {"20261012": hot, "20261013": hot}
    w = npa.window(dt.date(2026, 10, 12), obs, g["t"], water, np.ones(SHAPE, bool))
    assert "null_reason" in w


def test_no_water_is_null(tmp_path):
    fr = _frame(tmp_path, water_rows=[])
    r = npa.pass_record(fr, tmp_path, _grid(), {}, {}, None)
    assert r["water_cells"] == 0 and r["null_reason"] and "observed" not in r


def test_window_flat_keys_and_slots(tmp_path):
    fr = _frame(tmp_path, water_rows=[0, 1])
    flagged = np.zeros(SHAPE)
    flagged[0, :] = 0.9
    r = npa.pass_record(fr, tmp_path, _grid(), {"20261012": np.zeros(SHAPE), "20260930": flagged}, {}, None)
    k = npa.flat_keys(r)
    assert k["nisar_agreement_win_critical_best_day"] == "2026-09-30"
    assert k["nisar_agreement_win_critical_days_before"] == 12
    assert k["nisar_agreement_win_critical_hit_pct"] == 50.0
    s = npa.slots(r)
    assert s["uy_crit"] == 50.0 and s["uy_k"] == 12 and s["uy_best_day"] == "2026-09-30"
    assert s["uy_passday_crit"] == 0.0 and s["uy_pass"] == "2026-10-12" and s["uy_high"] == 50.0


def test_gfs_window_best_run_inside_window(tmp_path):
    fr = _frame(tmp_path, water_rows=[0, 1])
    flagged = np.zeros(SHAPE)
    flagged[0, :] = 0.9
    gfs = {"20261003": {"d2_flood": flagged, "d1_flood": np.zeros(SHAPE)},   # valid Oct 5 / Oct 4
           "20261011": {"d3_flood": flagged}}                                # valid Oct 14 > pass: excluded
    r = npa.pass_record(fr, tmp_path, _grid(), {"20261012": np.zeros(SHAPE)}, gfs, None)
    g = r["gfs_window_12d"]
    assert g["frames"] == 2
    b = g["best"]["critico"]
    assert b["issued"] == "2026-10-03" and b["valid"] == "2026-10-05" and b["issued_days_before"] == 9
    k = npa.flat_keys(r)
    assert k["nisar_agreement_gfswin_critical_hit_pct"] == 50.0
